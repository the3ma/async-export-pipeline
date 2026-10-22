import { Annotations, Match } from 'aws-cdk-lib/assertions';
import { actionsOf, resourcesOf, roleStatements, synth } from './helpers';

describe('storage', () => {
  const { template } = synth();

  test('exports bucket: private, SSE-S3, TLS only, unversioned', () => {
    template.hasResourceProperties('AWS::S3::Bucket', {
      PublicAccessBlockConfiguration: {
        BlockPublicAcls: true,
        BlockPublicPolicy: true,
        IgnorePublicAcls: true,
        RestrictPublicBuckets: true,
      },
      BucketEncryption: {
        ServerSideEncryptionConfiguration: [{ ServerSideEncryptionByDefault: { SSEAlgorithm: 'AES256' } }],
      },
      VersioningConfiguration: Match.absent(),
    });
    template.hasResourceProperties('AWS::S3::BucketPolicy', {
      PolicyDocument: {
        Statement: Match.arrayWith([
          Match.objectLike({ Effect: 'Deny', Condition: { Bool: { 'aws:SecureTransport': 'false' } } }),
        ]),
      },
    });
  });

  test('lifecycle: exports/ expire after 7 days, incomplete multipart aborted after 1 day', () => {
    template.hasResourceProperties('AWS::S3::Bucket', {
      LifecycleConfiguration: {
        Rules: Match.arrayWith([
          Match.objectLike({ Prefix: 'exports/', ExpirationInDays: 7, Status: 'Enabled' }),
          Match.objectLike({ AbortIncompleteMultipartUpload: { DaysAfterInitiation: 1 }, Status: 'Enabled' }),
        ]),
      },
    });
  });

  test('jobs table: PK export_id, GSI owner/requested_at, TTL expires_at, on-demand', () => {
    template.hasResourceProperties('AWS::DynamoDB::GlobalTable', {
      KeySchema: [{ AttributeName: 'export_id', KeyType: 'HASH' }],
      BillingMode: 'PAY_PER_REQUEST',
      TimeToLiveSpecification: { AttributeName: 'expires_at', Enabled: true },
      GlobalSecondaryIndexes: [
        Match.objectLike({
          IndexName: 'gsi_owner_requested',
          KeySchema: [
            { AttributeName: 'owner_id', KeyType: 'HASH' },
            { AttributeName: 'requested_at', KeyType: 'RANGE' },
          ],
        }),
      ],
    });
  });

  test('dev keeps PITR off, prod turns it on (and retains data)', () => {
    synth().template.hasResourceProperties('AWS::DynamoDB::GlobalTable', {
      Replicas: [Match.objectLike({ PointInTimeRecoverySpecification: { PointInTimeRecoveryEnabled: false } })],
    });
    const prod = synth({ stage: 'prod' }).template;
    prod.hasResourceProperties('AWS::DynamoDB::GlobalTable', {
      Replicas: [
        Match.objectLike({
          PointInTimeRecoverySpecification: { PointInTimeRecoveryEnabled: true },
          DeletionProtectionEnabled: true,
        }),
      ],
    });
    prod.hasResource('AWS::S3::Bucket', { DeletionPolicy: 'Retain' });
  });
});

describe('queue', () => {
  const { template } = synth();

  test('export-requests redrives to its DLQ after 3 receives, SSE on both', () => {
    template.hasResourceProperties('AWS::SQS::Queue', {
      QueueName: 'export-requests-dev',
      SqsManagedSseEnabled: true,
      RedrivePolicy: {
        deadLetterTargetArn: { 'Fn::GetAtt': [Match.stringLikeRegexp('^ExportRequestsDlq'), 'Arn'] },
        maxReceiveCount: 3,
      },
    });
    template.hasResourceProperties('AWS::SQS::Queue', {
      QueueName: 'export-requests-dlq-dev',
      SqsManagedSseEnabled: true,
    });
  });

  test('visibility timeout exceeds the 15 min worker timeout', () => {
    const queues = template.findResources('AWS::SQS::Queue', { Properties: { QueueName: 'export-requests-dev' } });
    const [queue] = Object.values(queues);
    expect(queue.Properties.VisibilityTimeout).toBeGreaterThan(15 * 60);
  });
});

describe('worker lambda', () => {
  const { template } = synth();

  test('python3.12 on arm64, 15 min, 2048 MB, handler from services/worker', () => {
    template.hasResourceProperties('AWS::Lambda::Function', {
      Handler: 'export_worker.handler.handler',
      Runtime: 'python3.12',
      Architectures: ['arm64'],
      Timeout: 900,
      MemorySize: 2048,
      ReservedConcurrentExecutions: 5,
    });
  });

  test('reserved concurrency is configurable via context', () => {
    synth({ workerReservedConcurrency: '9' }).template.hasResourceProperties('AWS::Lambda::Function', {
      Handler: 'export_worker.handler.handler',
      ReservedConcurrentExecutions: 9,
    });
  });

  test('SQS event source: batch size 1 with partial batch failures', () => {
    template.hasResourceProperties('AWS::Lambda::EventSourceMapping', {
      BatchSize: 1,
      FunctionResponseTypes: ['ReportBatchItemFailures'],
      EventSourceArn: { 'Fn::GetAtt': [Match.stringLikeRegexp('^ExportRequests(?!Dlq)'), 'Arn'] },
      ScalingConfig: { MaximumConcurrency: 5 },
    });
  });

  test('environment follows the contract (no DB password, no endpoint override)', () => {
    template.hasResourceProperties('AWS::Lambda::Function', {
      Handler: 'export_worker.handler.handler',
      Environment: {
        Variables: Match.objectLike({
          EXPORT_BUCKET: Match.anyValue(),
          EXPORT_QUEUE_URL: Match.anyValue(),
          JOBS_TABLE: Match.anyValue(),
          DATABASE_SECRET_ARN: Match.anyValue(),
          PART_ROWS: '1000000',
          MAX_PARTS: '20',
          MAX_RECEIVE_COUNT: '3',
          MAIL_TRANSPORT: 'ses',
          MAIL_FROM: 'exports@example.com',
          APP_BASE_URL: Match.anyValue(),
        }),
      },
    });
    const fns = template.findResources('AWS::Lambda::Function');
    for (const fn of Object.values(fns)) {
      const vars = (fn.Properties.Environment?.Variables ?? {}) as Record<string, unknown>;
      expect(vars).not.toHaveProperty('DATABASE_URL');
      expect(vars).not.toHaveProperty('AWS_ENDPOINT_URL');
    }
  });
});

describe('api', () => {
  const { template } = synth();

  test('FastAPI on Lambda (Mangum) behind an HTTP API with access logs and throttling', () => {
    template.hasResourceProperties('AWS::Lambda::Function', {
      Handler: 'export_api.lambda_handler.handler',
      Runtime: 'python3.12',
      Architectures: ['arm64'],
    });
    template.hasResourceProperties('AWS::ApiGatewayV2::Api', { ProtocolType: 'HTTP' });
    template.hasResourceProperties('AWS::ApiGatewayV2::Stage', {
      AccessLogSettings: { DestinationArn: Match.anyValue(), Format: Match.anyValue() },
      DefaultRouteSettings: { ThrottlingBurstLimit: 50, ThrottlingRateLimit: 25 },
    });
  });

  test('JWT authorizer is attached when issuer + audience are configured', () => {
    const { template: withJwt } = synth({ jwtIssuer: 'https://issuer.example.com/', jwtAudience: 'exports-api' });
    withJwt.hasResourceProperties('AWS::ApiGatewayV2::Authorizer', {
      AuthorizerType: 'JWT',
      JwtConfiguration: { Issuer: 'https://issuer.example.com/', Audience: ['exports-api'] },
    });
    withJwt.hasResourceProperties('AWS::Lambda::Function', {
      Handler: 'export_api.lambda_handler.handler',
      Environment: { Variables: Match.objectLike({ AUTH_MODE: 'jwt' }) },
    });
    template.resourceCountIs('AWS::ApiGatewayV2::Authorizer', 0);
  });
});

describe('IAM least privilege', () => {
  const { template } = synth();
  const all = [...roleStatements(template, 'WorkerRole'), ...roleStatements(template, 'ApiRole')];

  test('no wildcard actions and no s3 access outside exports/', () => {
    expect(all.length).toBeGreaterThan(0);
    for (const s of all) {
      for (const action of actionsOf(s)) {
        expect(action).not.toBe('*');
        expect(action).not.toMatch(/^[a-z0-9-]+:\*$/);
      }
      expect(resourcesOf(s)).not.toContain('*');
      if (actionsOf(s).some((a) => a.startsWith('s3:'))) {
        expect(JSON.stringify(s.Resource)).toContain('/exports/*');
      }
    }
  });

  test('worker: object put/get/delete under exports/, job get/update, send to own queue, SES with sender condition', () => {
    const worker = roleStatements(template, 'WorkerRole');
    const s3 = worker.find((s) => actionsOf(s).includes('s3:PutObject'));
    expect(actionsOf(s3!).sort()).toEqual(['s3:AbortMultipartUpload', 's3:DeleteObject', 's3:GetObject', 's3:PutObject']);

    const ddb = worker.find((s) => actionsOf(s).some((a) => a.startsWith('dynamodb:')));
    expect(actionsOf(ddb!).sort()).toEqual(['dynamodb:GetItem', 'dynamodb:UpdateItem']);

    const send = worker.find((s) => actionsOf(s).includes('sqs:SendMessage'));
    expect(JSON.stringify(send!.Resource)).toMatch(/ExportRequests(?!Dlq)/);

    const ses = worker.find((s) => actionsOf(s).includes('ses:SendEmail'));
    expect(ses!.Condition).toEqual({ StringEquals: { 'ses:FromAddress': 'exports@example.com' } });

    expect(worker.some((s) => actionsOf(s).includes('secretsmanager:GetSecretValue'))).toBe(true);
    // the worker never writes new job records
    expect(worker.flatMap(actionsOf)).not.toContain('dynamodb:PutItem');
  });

  test('api: GetObject only (presign), table + owner GSI, enqueue', () => {
    const api = roleStatements(template, 'ApiRole');
    const s3Actions = api.flatMap(actionsOf).filter((a) => a.startsWith('s3:'));
    expect(s3Actions).toEqual(['s3:GetObject']);
    const ddb = api.find((s) => actionsOf(s).includes('dynamodb:Query'));
    expect(JSON.stringify(ddb!.Resource)).toContain('/index/gsi_owner_requested');
    expect(api.flatMap(actionsOf)).toContain('sqs:SendMessage');
    expect(api.flatMap(actionsOf)).not.toContain('ses:SendEmail');
  });

  test('no AWS managed policies outside VPC mode', () => {
    let lambdaRoles = 0;
    for (const role of Object.values(template.findResources('AWS::IAM::Role'))) {
      const statements: { Principal?: { Service?: string | string[] } }[] =
        role.Properties.AssumeRolePolicyDocument.Statement;
      const services = statements.flatMap((s) => [s.Principal?.Service ?? []].flat());
      if (services.includes('lambda.amazonaws.com')) {
        lambdaRoles += 1;
        expect(role.Properties.ManagedPolicyArns).toBeUndefined();
      }
    }
    expect(lambdaRoles).toBeGreaterThan(0);
  });
});

describe('alarms', () => {
  const { template } = synth();

  test('SNS topic enforces TLS', () => {
    template.resourceCountIs('AWS::SNS::Topic', 1);
    template.hasResourceProperties('AWS::SNS::TopicPolicy', {
      PolicyDocument: {
        Statement: Match.arrayWith([
          Match.objectLike({ Effect: 'Deny', Condition: { Bool: { 'aws:SecureTransport': 'false' } } }),
        ]),
      },
    });
  });

  test.each([
    ['dlq-not-empty', 'ApproximateNumberOfMessagesVisible', 1],
    ['worker-errors', 'Errors', 1],
    ['worker-duration-near-timeout', 'Duration', 720000],
    ['oldest-message-age', 'ApproximateAgeOfOldestMessage', 1800],
  ])('%s alarm routes to SNS', (name, metric, threshold) => {
    template.hasResourceProperties('AWS::CloudWatch::Alarm', {
      AlarmName: `export-pipeline-dev-${name}`,
      MetricName: metric,
      Threshold: threshold,
      AlarmActions: [{ Ref: Match.stringLikeRegexp('^MonitoringAlarmTopic') }],
    });
  });
});

describe('optional VPC placement', () => {
  const { template } = synth({ useVpc: 'true', dbCidr: '10.20.0.0/16', dbPort: '5432' });

  test('lambda SG egress = DB port to the DB CIDR + 443 to the endpoint SG only', () => {
    template.hasResourceProperties('AWS::EC2::SecurityGroup', {
      GroupDescription: Match.stringLikeRegexp('database port'),
      SecurityGroupEgress: [
        Match.objectLike({ CidrIp: '10.20.0.0/16', FromPort: 5432, ToPort: 5432, IpProtocol: 'tcp' }),
      ],
    });
    template.hasResourceProperties('AWS::EC2::SecurityGroupEgress', {
      FromPort: 443,
      ToPort: 443,
      DestinationSecurityGroupId: { 'Fn::GetAtt': [Match.stringLikeRegexp('^NetworkEndpointSg'), 'GroupId'] },
    });
    const sgs = template.findResources('AWS::EC2::SecurityGroup');
    for (const sg of Object.values(sgs)) {
      const egress = (sg.Properties.SecurityGroupEgress ?? []) as Array<{ CidrIp?: string }>;
      expect(egress.some((e) => e.CidrIp === '0.0.0.0/0' && JSON.stringify(e).includes('-1'))).toBe(false);
    }
  });

  test('interface endpoints for every AWS API the functions use; no NAT', () => {
    template.resourceCountIs('AWS::EC2::VPCEndpoint', 5);
    template.resourceCountIs('AWS::EC2::NatGateway', 0);
    template.hasResourceProperties('AWS::Lambda::Function', {
      Handler: 'export_worker.handler.handler',
      VpcConfig: { SecurityGroupIds: Match.anyValue(), SubnetIds: Match.anyValue() },
    });
  });
});

describe('outputs and cdk-nag', () => {
  test('stack outputs the bucket, queue, table and API URL', () => {
    const outputs = synth().template.findOutputs('*');
    expect(Object.keys(outputs)).toEqual(expect.arrayContaining(['BucketName', 'QueueUrl', 'TableName', 'ApiUrl']));
  });

  test.each([
    ['dev', {}],
    ['prod', { stage: 'prod' }],
    ['vpc', { useVpc: 'true' }],
  ])('AwsSolutions checks pass with justified suppressions only (%s)', (_name, context) => {
    const { stack } = synth(context as Record<string, string>, { nag: true });
    const errors = Annotations.fromStack(stack).findError('*', Match.stringLikeRegexp('AwsSolutions-.*'));
    const warnings = Annotations.fromStack(stack).findWarning('*', Match.stringLikeRegexp('AwsSolutions-.*'));
    expect(errors.map((e) => `${e.id}: ${String(e.entry.data)}`)).toEqual([]);
    expect(warnings.map((w) => `${w.id}: ${String(w.entry.data)}`)).toEqual([]);
  });
});

describe('config validation', () => {
  test('rejects unknown stages', () => {
    expect(() => synth({ stage: 'qa' })).toThrow(/stage/);
  });
});
