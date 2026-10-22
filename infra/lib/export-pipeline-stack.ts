import { Annotations, CfnOutput, Duration, RemovalPolicy, Size, Stack } from 'aws-cdk-lib';
import type { StackProps } from 'aws-cdk-lib';
import * as apigw from 'aws-cdk-lib/aws-apigatewayv2';
import { HttpJwtAuthorizer } from 'aws-cdk-lib/aws-apigatewayv2-authorizers';
import { HttpLambdaIntegration } from 'aws-cdk-lib/aws-apigatewayv2-integrations';
import * as dynamodb from 'aws-cdk-lib/aws-dynamodb';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import { SqsEventSource } from 'aws-cdk-lib/aws-lambda-event-sources';
import * as logs from 'aws-cdk-lib/aws-logs';
import * as s3 from 'aws-cdk-lib/aws-s3';
import * as secretsmanager from 'aws-cdk-lib/aws-secretsmanager';
import * as sqs from 'aws-cdk-lib/aws-sqs';
import { NagSuppressions } from 'cdk-nag';
import type { Construct } from 'constructs';
import type { PipelineConfig } from './config';
import { Monitoring } from './constructs/monitoring';
import { Network } from './constructs/network';
import type { ServiceComponent } from './constructs/python-code';
import { resolvePythonCode } from './constructs/python-code';

export const EXPORTS_PREFIX = 'exports/';
export const OWNER_GSI = 'gsi_owner_requested';
/** Redrive threshold; also passed to the worker so it can mark the job failed on the last receive. */
export const MAX_RECEIVE_COUNT = 3;

export interface ExportPipelineStackProps extends StackProps {
  readonly config: PipelineConfig;
}

/**
 * Async export pipeline: HTTP API (FastAPI on Lambda via Mangum) queues jobs in DynamoDB + SQS,
 * an SQS-triggered worker Lambda streams the export to S3, and a 7-day retention cleans up.
 */
export class ExportPipelineStack extends Stack {
  public readonly bucket: s3.Bucket;
  public readonly queue: sqs.Queue;
  public readonly dlq: sqs.Queue;
  public readonly table: dynamodb.TableV2;
  public readonly worker: lambda.Function;
  public readonly api: lambda.Function;
  public readonly httpApi: apigw.HttpApi;
  public readonly monitoring: Monitoring;

  constructor(scope: Construct, id: string, props: ExportPipelineStackProps) {
    super(scope, id, props);
    const cfg = props.config;
    const removalPolicy = cfg.retainData ? RemovalPolicy.RETAIN : RemovalPolicy.DESTROY;
    const workerTimeout = Duration.minutes(cfg.workerTimeoutMinutes);

    // ---------------------------------------------------------------- storage
    this.bucket = new s3.Bucket(this, 'ExportBucket', {
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      encryption: s3.BucketEncryption.S3_MANAGED,
      enforceSSL: true,
      versioned: false,
      objectOwnership: s3.ObjectOwnership.BUCKET_OWNER_ENFORCED,
      removalPolicy,
      lifecycleRules: [
        { id: 'expire-exports', prefix: EXPORTS_PREFIX, expiration: Duration.days(7) },
        { id: 'abort-incomplete-multipart', abortIncompleteMultipartUploadAfter: Duration.days(1) },
      ],
    });
    NagSuppressions.addResourceSuppressions(this.bucket, [
      {
        id: 'AwsSolutions-S1',
        reason:
          'Objects are short-lived (7-day lifecycle) and every download is recorded in the application activity log ' +
          'before the presigned URL is issued. Enable CloudTrail S3 data events at the account level for object-level audit.',
      },
    ]);

    this.dlq = new sqs.Queue(this, 'ExportRequestsDlq', {
      queueName: `export-requests-dlq-${cfg.stage}`,
      encryption: sqs.QueueEncryption.SQS_MANAGED,
      enforceSSL: true,
      retentionPeriod: Duration.days(14),
      removalPolicy,
    });
    NagSuppressions.addResourceSuppressions(this.dlq, [
      { id: 'AwsSolutions-SQS3', reason: 'This queue is the dead-letter queue of export-requests.' },
    ]);

    this.queue = new sqs.Queue(this, 'ExportRequests', {
      queueName: `export-requests-${cfg.stage}`,
      encryption: sqs.QueueEncryption.SQS_MANAGED,
      enforceSSL: true,
      // Must exceed the worker timeout so an in-flight message is never handed to a second worker.
      visibilityTimeout: workerTimeout.plus(Duration.minutes(1)),
      retentionPeriod: Duration.days(4),
      deadLetterQueue: { queue: this.dlq, maxReceiveCount: MAX_RECEIVE_COUNT },
      removalPolicy,
    });

    this.table = new dynamodb.TableV2(this, 'ExportsTable', {
      tableName: `exports-${cfg.stage}`,
      partitionKey: { name: 'export_id', type: dynamodb.AttributeType.STRING },
      billing: dynamodb.Billing.onDemand(),
      timeToLiveAttribute: 'expires_at',
      pointInTimeRecoverySpecification: { pointInTimeRecoveryEnabled: cfg.pointInTimeRecovery },
      encryption: dynamodb.TableEncryptionV2.awsManagedKey(),
      deletionProtection: cfg.retainData,
      removalPolicy,
      globalSecondaryIndexes: [
        {
          indexName: OWNER_GSI,
          partitionKey: { name: 'owner_id', type: dynamodb.AttributeType.STRING },
          sortKey: { name: 'requested_at', type: dynamodb.AttributeType.STRING },
          projectionType: dynamodb.ProjectionType.ALL,
        },
      ],
    });
    if (!cfg.pointInTimeRecovery) {
      NagSuppressions.addResourceSuppressions(this.table, [
        { id: 'AwsSolutions-DDB3', reason: 'Non-production stage: job records are disposable (TTL 7 days). PITR is enabled in prod.' },
      ]);
    }

    // ---------------------------------------------------------------- secrets
    const workerDbSecret = this.dbSecret('WorkerDbSecret', cfg.workerDbSecretArn, 'exporter', 'read-only exporter role (worker)');
    const apiDbSecret = this.dbSecret('ApiDbSecret', cfg.apiDbSecretArn, 'app', 'application role (api)');

    // ---------------------------------------------------------------- network (optional)
    const network = cfg.useVpc
      ? new Network(this, 'Network', { vpcId: cfg.vpcId, dbCidr: cfg.dbCidr, dbPort: cfg.dbPort })
      : undefined;
    const vpcProps: Partial<lambda.FunctionProps> = network
      ? { vpc: network.vpc, vpcSubnets: network.subnets, securityGroups: [network.lambdaSecurityGroup] }
      : {};

    // ---------------------------------------------------------------- worker
    const workerLogs = this.logGroup('WorkerLogs', cfg, removalPolicy);
    const workerRole = this.functionRole('WorkerRole', workerLogs, 'Export worker Lambda');
    const workerCode = this.code('worker', cfg);

    this.worker = new lambda.Function(this, 'Worker', {
      description: 'Async export worker (export -> part N -> zip stages)',
      runtime: lambda.Runtime.PYTHON_3_12,
      architecture: lambda.Architecture.ARM_64,
      handler: cfg.workerHandler,
      code: workerCode,
      timeout: workerTimeout,
      memorySize: cfg.workerMemoryMb,
      ephemeralStorageSize: Size.mebibytes(2048),
      reservedConcurrentExecutions: cfg.workerReservedConcurrency,
      role: workerRole,
      logGroup: workerLogs,
      loggingFormat: lambda.LoggingFormat.JSON,
      environment: {
        DATABASE_SECRET_ARN: workerDbSecret.secretArn,
        EXPORT_BUCKET: this.bucket.bucketName,
        EXPORT_QUEUE_URL: this.queue.queueUrl,
        JOBS_TABLE: this.table.tableName,
        PART_ROWS: String(cfg.partRows),
        MAX_PARTS: String(cfg.maxParts),
        MAX_RECEIVE_COUNT: String(MAX_RECEIVE_COUNT),
        TMP_DIR: '/tmp',
        MAIL_TRANSPORT: 'ses',
        MAIL_FROM: cfg.mailFrom,
        MAIL_TO_DOMAIN: cfg.mailToDomain,
        APP_BASE_URL: cfg.appBaseUrl,
        STAGE: cfg.stage,
      },
      ...vpcProps,
    });
    this.worker.addEventSource(
      new SqsEventSource(this.queue, {
        batchSize: 1,
        reportBatchItemFailures: true,
        // Keep the poller from out-running reserved concurrency (throttles would burn receive counts).
        ...(cfg.workerReservedConcurrency >= 2 ? { maxConcurrency: cfg.workerReservedConcurrency } : {}),
      }),
    );

    const exportObjects = this.bucket.arnForObjects(`${EXPORTS_PREFIX}*`);
    workerRole.addToPolicy(
      new iam.PolicyStatement({
        sid: 'ExportObjectsReadWrite',
        actions: ['s3:PutObject', 's3:GetObject', 's3:DeleteObject', 's3:AbortMultipartUpload'],
        resources: [exportObjects],
      }),
    );
    workerRole.addToPolicy(
      new iam.PolicyStatement({
        sid: 'JobRecordReadUpdate',
        actions: ['dynamodb:GetItem', 'dynamodb:UpdateItem'],
        resources: [this.table.tableArn],
      }),
    );
    workerRole.addToPolicy(
      new iam.PolicyStatement({ sid: 'EnqueueNextStage', actions: ['sqs:SendMessage'], resources: [this.queue.queueArn] }),
    );
    workerRole.addToPolicy(
      new iam.PolicyStatement({
        sid: 'SendNotification',
        actions: ['ses:SendEmail', 'ses:SendRawEmail'],
        resources: [this.formatArn({ service: 'ses', resource: 'identity', resourceName: '*' })],
        conditions: { StringEquals: { 'ses:FromAddress': cfg.mailFrom } },
      }),
    );
    workerDbSecret.grantRead(workerRole);

    // ---------------------------------------------------------------- api
    const apiLogs = this.logGroup('ApiLogs', cfg, removalPolicy);
    const apiRole = this.functionRole('ApiRole', apiLogs, 'Export API Lambda');
    const apiCode = this.code('api', cfg);
    const jwtAuth = cfg.jwtIssuer !== undefined && cfg.jwtAudience !== undefined && cfg.jwtAudience.length > 0;

    this.api = new lambda.Function(this, 'Api', {
      description: 'Export API (FastAPI via Mangum)',
      runtime: lambda.Runtime.PYTHON_3_12,
      architecture: lambda.Architecture.ARM_64,
      handler: cfg.apiHandler,
      code: apiCode,
      timeout: Duration.seconds(29),
      memorySize: 1024,
      role: apiRole,
      logGroup: apiLogs,
      loggingFormat: lambda.LoggingFormat.JSON,
      environment: {
        DATABASE_SECRET_ARN: apiDbSecret.secretArn,
        EXPORT_BUCKET: this.bucket.bucketName,
        EXPORT_QUEUE_URL: this.queue.queueUrl,
        JOBS_TABLE: this.table.tableName,
        PRESIGN_SECONDS: String(cfg.presignSeconds),
        AUTH_MODE: jwtAuth ? 'jwt' : 'header',
        ...(cfg.webOrigin ? { CORS_ORIGINS: JSON.stringify([cfg.webOrigin]) } : {}),
        STAGE: cfg.stage,
      },
      ...vpcProps,
    });

    apiRole.addToPolicy(
      new iam.PolicyStatement({ sid: 'PresignExportDownload', actions: ['s3:GetObject'], resources: [exportObjects] }),
    );
    apiRole.addToPolicy(
      new iam.PolicyStatement({
        sid: 'JobRecordsReadWrite',
        actions: ['dynamodb:GetItem', 'dynamodb:PutItem', 'dynamodb:UpdateItem', 'dynamodb:Query'],
        resources: [this.table.tableArn, `${this.table.tableArn}/index/${OWNER_GSI}`],
      }),
    );
    apiRole.addToPolicy(
      new iam.PolicyStatement({ sid: 'EnqueueExport', actions: ['sqs:SendMessage'], resources: [this.queue.queueArn] }),
    );
    apiDbSecret.grantRead(apiRole);

    const accessLogs = new logs.LogGroup(this, 'HttpApiAccessLogs', {
      retention: cfg.logRetentionDays as logs.RetentionDays,
      removalPolicy,
    });
    this.httpApi = new apigw.HttpApi(this, 'HttpApi', {
      apiName: `export-pipeline-${cfg.stage}`,
      description: 'Async export pipeline API',
      defaultIntegration: new HttpLambdaIntegration('ApiIntegration', this.api),
      ...(jwtAuth
        ? {
            defaultAuthorizer: new HttpJwtAuthorizer('JwtAuthorizer', cfg.jwtIssuer as string, {
              jwtAudience: cfg.jwtAudience as string[],
            }),
          }
        : {}),
    });
    const stage = this.httpApi.defaultStage?.node.defaultChild as apigw.CfnStage;
    stage.accessLogSettings = {
      destinationArn: accessLogs.logGroupArn,
      format: JSON.stringify({
        requestId: '$context.requestId',
        ip: '$context.identity.sourceIp',
        requestTime: '$context.requestTime',
        method: '$context.httpMethod',
        path: '$context.path',
        status: '$context.status',
        latencyMs: '$context.responseLatency',
        integrationError: '$context.integrationErrorMessage',
      }),
    };
    stage.defaultRouteSettings = { throttlingBurstLimit: 50, throttlingRateLimit: 25 };

    if (!jwtAuth) {
      const msg =
        'HTTP API has no authorizer: the api trusts the X-User-Id header (demo auth). ' +
        'Set -c jwtIssuer=... -c jwtAudience=... before exposing this stage.';
      if (cfg.stage === 'prod') Annotations.of(this.httpApi).addWarningV2('export-pipeline:no-authorizer', msg);
      NagSuppressions.addResourceSuppressions(
        this.httpApi,
        [{ id: 'AwsSolutions-APIG4', reason: `Demo stage. ${msg}` }],
        true,
      );
    }

    // ---------------------------------------------------------------- monitoring
    this.monitoring = new Monitoring(this, 'Monitoring', {
      stage: cfg.stage,
      queue: this.queue,
      dlq: this.dlq,
      worker: this.worker,
      workerTimeout,
      api: this.api,
      alarmEmail: cfg.alarmEmail,
    });

    // ---------------------------------------------------------------- nag: function-level
    for (const [fn, role] of [
      [this.worker, workerRole],
      [this.api, apiRole],
    ] as const) {
      NagSuppressions.addResourceSuppressions(fn, [
        {
          id: 'AwsSolutions-L1',
          reason: 'Pinned to Python 3.12 on purpose: the services are built and tested against 3.12 (see services/*/pyproject.toml).',
        },
      ]);
      NagSuppressions.addResourceSuppressions(
        role,
        [
          {
            id: 'AwsSolutions-IAM5',
            reason:
              'Object-level actions are scoped to the exports/ prefix (bucket/exports/*) because object keys are generated per ' +
              'export; SES identity wildcard is constrained by the ses:FromAddress condition; log-stream ARNs are generated by Lambda.',
            appliesTo: [
              { regex: '/^Resource::<ExportBucket[A-Za-z0-9]+\\.Arn>\\/exports\\/\\*$/g' },
              { regex: '/^Resource::arn:<AWS::Partition>:ses:<AWS::Region>:<AWS::AccountId>:identity/\\*$/g' },
              { regex: '/^Resource::<(Worker|Api)Logs[A-Za-z0-9]+\\.Arn>:\\*$/g' },
              { regex: '/^Resource::arn:.*:secretsmanager:.*-\\?\\?\\?\\?\\?\\?$/g' },
            ],
          },
          {
            id: 'AwsSolutions-IAM4',
            reason: 'AWSLambdaVPCAccessExecutionRole is the AWS-maintained minimum for ENI management in a VPC (VPC mode only).',
            appliesTo: ['Policy::arn:<AWS::Partition>:iam::aws:policy/service-role/AWSLambdaVPCAccessExecutionRole'],
          },
        ],
        true,
      );
    }

    // ---------------------------------------------------------------- outputs
    new CfnOutput(this, 'BucketName', { value: this.bucket.bucketName, description: 'EXPORT_BUCKET' });
    new CfnOutput(this, 'QueueUrl', { value: this.queue.queueUrl, description: 'EXPORT_QUEUE_URL' });
    new CfnOutput(this, 'DlqUrl', { value: this.dlq.queueUrl, description: 'Dead-letter queue' });
    new CfnOutput(this, 'TableName', { value: this.table.tableName, description: 'JOBS_TABLE' });
    new CfnOutput(this, 'ApiUrl', { value: this.httpApi.apiEndpoint, description: 'HTTP API base URL (routes under /api)' });
    new CfnOutput(this, 'WorkerFunctionName', { value: this.worker.functionName });
    new CfnOutput(this, 'AlarmTopicArn', { value: this.monitoring.topic.topicArn });
    new CfnOutput(this, 'WorkerDbSecretArn', { value: workerDbSecret.secretArn });
    new CfnOutput(this, 'ApiDbSecretArn', { value: apiDbSecret.secretArn });
  }

  private code(component: ServiceComponent, cfg: PipelineConfig): lambda.Code {
    const resolved = resolvePythonCode({
      component,
      servicesDir: cfg.servicesDir,
      artifact: component === 'worker' ? cfg.workerArtifact : cfg.apiArtifact,
      skipBundling: cfg.skipBundling,
    });
    if (resolved.source === 'source-copy') {
      const msg =
        `${component} code is a dependency-free source copy (skipBundling or no Docker). ` +
        `The template synthesizes but the function will not run. Deploy with -c ${component}Artifact=<zip> or with Docker.`;
      Annotations.of(this).addWarningV2(`export-pipeline:${component}-source-copy`, msg);
    }
    return resolved.code;
  }

  private logGroup(id: string, cfg: PipelineConfig, removalPolicy: RemovalPolicy): logs.LogGroup {
    return new logs.LogGroup(this, id, { retention: cfg.logRetentionDays as logs.RetentionDays, removalPolicy });
  }

  /** Function role without the broad AWSLambdaBasicExecutionRole: logs only to its own log group. */
  private functionRole(id: string, logGroup: logs.LogGroup, description: string): iam.Role {
    const role = new iam.Role(this, id, {
      assumedBy: new iam.ServicePrincipal('lambda.amazonaws.com'),
      description,
    });
    role.addToPolicy(
      new iam.PolicyStatement({
        sid: 'OwnLogGroup',
        actions: ['logs:CreateLogStream', 'logs:PutLogEvents'],
        resources: [logGroup.logGroupArn],
      }),
    );
    return role;
  }

  /**
   * Database credentials. Pass an existing secret ARN via context, or a placeholder secret is created
   * (RDS-style JSON: host, port, dbname, username, password) that an operator fills in after deploy.
   */
  private dbSecret(id: string, arn: string | undefined, username: string, purpose: string): secretsmanager.ISecret {
    if (arn) {
      return /-[A-Za-z0-9]{6}$/.test(arn)
        ? secretsmanager.Secret.fromSecretCompleteArn(this, id, arn)
        : secretsmanager.Secret.fromSecretPartialArn(this, id, arn);
    }
    const secret = new secretsmanager.Secret(this, id, {
      description: `PostgreSQL connection for the ${purpose}. Placeholder: set host/port/dbname to the real database.`,
      generateSecretString: {
        secretStringTemplate: JSON.stringify({ engine: 'postgres', host: 'db.invalid', port: 5432, dbname: 'activity', username }),
        generateStringKey: 'password',
        excludePunctuation: true,
        passwordLength: 32,
      },
    });
    NagSuppressions.addResourceSuppressions(secret, [
      {
        id: 'AwsSolutions-SMG4',
        reason: 'The database is external to this stack; rotation is owned by the database platform (or use an RDS-managed secret ARN via context).',
      },
    ]);
    return secret;
  }
}
