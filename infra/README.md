# infra: AWS CDK app

This is the AWS CDK v2 app (TypeScript) that deploys the export pipeline described in
[`docs/CONTRACT.md`](../docs/CONTRACT.md). It defines one stack, `ExportPipeline-<stage>`, and the
stage is either `dev` or `prod`.

```
 client ──HTTPS──▶ API Gateway HTTP API ──▶ API Lambda (FastAPI via Mangum)
                                               │  put job        │ send {"stage":"export"}
                                               ▼                 ▼
                                   DynamoDB `exports-<stage>`   SQS `export-requests-<stage>` ──(3 receives)──▶ DLQ
                                               ▲                 │ batch 1, partial failures
                                               │ get/update      ▼
                                               └───────── Worker Lambda (export → part N → zip)
                                                               │ stream parts / zip        │ SES e-mail (link to /exports)
                                                               ▼                           ▼
                                                     S3 `exports/…` (7-day lifecycle)   owner
 CloudWatch alarms (DLQ not empty, worker errors, duration near timeout, oldest message age, API errors) ──▶ SNS
```

## Design choices

| Concern | Choice | Why |
|---|---|---|
| API hosting | **Lambda + API Gateway HTTP API**, with the FastAPI app wrapped by [Mangum](https://mangum.fastapiexpert.com/) | It costs nothing when idle, there is no ALB/cluster to run, and the API's calls are short (enqueue, read, presign). The api code stays plain FastAPI, and only `export_api/lambda_handler.py` knows about Lambda. |
| Worker | Lambda, Python 3.12, **arm64**, 15 min, 2048 MB, 2 GiB `/tmp`, reserved concurrency 5 | Each stage (export, part N, zip) is sized to finish inside one invocation, and `PART_ROWS` is the lever if a stage gets close to the limit (there is an alarm at 80% of the timeout). Reserved concurrency caps the load on the database. |
| Queue | SQS with SSE-SQS and TLS only. Visibility timeout is 16 min, which is longer than the worker timeout. DLQ after **3** receives. | A message that is still being processed is never handed to a second worker. On the last receive the worker marks the job `failed` / `WORKER_ERROR` (`MAX_RECEIVE_COUNT=3` is passed in env), and the DLQ alarm fires. The event source's `maxConcurrency` equals reserved concurrency, so throttles do not use up receive counts. |
| Job store | DynamoDB on-demand. PK `export_id`, GSI `gsi_owner_requested`, TTL `expires_at`. PITR and deletion protection in prod. | Matches the contract. |
| Files | S3 with public access blocked, SSE-S3, TLS only, no versioning. `exports/` expires after 7 days and incomplete multipart uploads are aborted after 1 day. No CORS. | The browser downloads through a 60-second presigned GET, so it never makes a cross-origin API call to the bucket. |
| E-mail | **SES API** (`MAIL_TRANSPORT=ses`), allowed only for `ses:FromAddress == MAIL_FROM` | It needs no SMTP credentials to store or rotate. The local stack keeps using SMTP (Mailpit). |
| DB credentials | Secrets Manager. The Lambdas get `DATABASE_SECRET_ARN`, **never** `DATABASE_URL`. | A password in a Lambda environment variable can be read by anyone with `lambda:GetFunctionConfiguration`. The worker gets the read-only `exporter` secret and the api gets the `app` secret. |
| Network | Optional VPC (`-c useVpc=true`). The Lambda security group has no default egress: only the DB port to `dbCidr`, plus 443 to the interface-endpoint security group (S3, DynamoDB, SQS, Secrets Manager, SES). There is no NAT. | The functions get no internet route. |

## Commands

```bash
cd infra
npm ci                     # or npm install
npm run build              # tsc --noEmit (strict type check; cdk runs the TS directly via ts-node)
npm run lint               # eslint (typescript-eslint, consistent-type-imports)
npm test                   # jest + aws-cdk-lib/assertions + cdk-nag
npx cdk synth -c stage=dev # also: npm run synth / npm run synth:prod (skipBundling)
```

`cdk-nag` (`AwsSolutionsChecks`) runs on every synth. A finding that is not suppressed fails the
synth and the tests. Each suppression is next to the resource it covers and gives the reason.

## Lambda code: three packaging modes

The worker is built from `services/shared` + `services/worker`, and the api from `services/shared` +
`services/api`. In both cases the third-party dependencies come from the **uv lock file**
(`uv.lock`, exact pins) as binary wheels for `aarch64-manylinux_2_28` / CPython 3.12. Lambda's
python3.12 runtime is Amazon Linux 2023, which has glibc 2.34. The build therefore runs on an x86
host without QEMU.

| Mode | How | When |
|---|---|---|
| **Pre-built artifact** (recommended for CI) | `scripts/build-lambda-artifact.sh worker` and `… api` write `.build/{worker,api}.zip`, then `cdk deploy -c workerArtifact=.build/worker.zip -c apiArtifact=.build/api.zip` | Build once, test the zip, and deploy the same bytes to every stage. Needs `uv`, not Docker. |
| **Docker bundling** | Plain `cdk synth` / `cdk deploy` with Docker running. The SAM `build-python3.12` image runs the same uv commands as the script. | Laptops with Docker. |
| **Source copy (synth only)** | `-c skipBundling=true`, or automatically when Docker is not available and no artifact is given | Copies the `exportkit` package and the service package **without dependencies**, writes `SYNTH_ONLY.txt` into the asset, and emits a synth warning (`export-pipeline:<component>-source-copy`). It exists so `cdk synth` and the unit tests run anywhere. **Never deploy it.** |

Artifact layout is a flat zip root, as Lambda expects for Python:

```
export_worker/  exportkit/  psycopg/  psycopg_binary/  pydantic/ …   (worker.zip)
export_api/     exportkit/  fastapi/  mangum/  psycopg/  …           (api.zip)
```

## Context parameters

| Key | Default | Meaning |
|---|---|---|
| `stage` | `dev` | `dev` or `prod`. Prod turns on PITR and deletion protection, retains data on stack delete, and keeps logs for 90 days instead of 14. |
| `workerArtifact` / `apiArtifact` | – | Path to a pre-built zip (see above) |
| `skipBundling` | `false` | Use the synth-only source copy |
| `workerReservedConcurrency` | `5` | Reserved concurrency for the worker, which is also the event-source `maxConcurrency`. The account needs enough unreserved concurrency for this. |
| `workerMemoryMb` / `workerTimeoutMinutes` | `2048` / `15` | |
| `partRows` / `maxParts` | `1000000` / `20` | Worker `PART_ROWS` / `MAX_PARTS` |
| `presignSeconds` | `60` | Api `PRESIGN_SECONDS` |
| `workerDbSecretArn` / `apiDbSecretArn` | – | Existing secrets (for example RDS-managed secrets). If they are not set, placeholder secrets are created, and you fill in host/port/dbname after deploy. |
| `useVpc` | `false` | Run both Lambdas in a VPC (see Network above) |
| `vpcId` | – | Import an existing VPC instead of creating one. This needs `CDK_DEFAULT_ACCOUNT` / `CDK_DEFAULT_REGION`. |
| `dbCidr` / `dbPort` | `10.0.0.0/16` / `5432` | The only non-endpoint egress allowed from the Lambda security group |
| `mailFrom` / `mailToDomain` | `exports@example.com` / `example.com` | SES sender (it must be a verified identity) and the demo recipient mapping `<owner_id>@<domain>` |
| `appBaseUrl` | `https://exports.example.com` | Link in the notification e-mail |
| `webOrigin` | – | Passed to the api as `CORS_ORIGINS='["<origin>"]'` |
| `jwtIssuer` / `jwtAudience` | – | Attach an API Gateway JWT authorizer (audience is comma-separated) and set `AUTH_MODE=jwt` |
| `alarmEmail` | – | E-mail subscription on the alarm topic |
| `workerHandler` / `apiHandler` | `export_worker.handler.handler` / `export_api.lambda_handler.handler` | Lambda handler paths |

## What the services must provide on AWS

- **Worker handler**: `export_worker.handler.handler`. It gets SQS events with batch size 1 and
  returns `{"batchItemFailures": [...]}`.
- **API handler**: `export_api.lambda_handler.handler`, which is `Mangum(app)` with `mangum`
  added to the `export-api` dependencies.
- **Database URL from a secret**: when `DATABASE_URL` is unset and `DATABASE_SECRET_ARN` is set,
  read the secret. Use its `url` key if it has one. Otherwise build
  `postgresql://{username}:{password}@{host}:{port}/{dbname}` from the RDS-style JSON.
- **SES notifier**: used when `MAIL_TRANSPORT=ses` (`smtp` stays the local default). It sends the
  composed message through the SES v2 API.
- **Auth**: with `AUTH_MODE=jwt` the owner is the JWT `sub` claim from the API Gateway request
  context. With `header` (the demo default) the api trusts `X-User-Id`. **A stage without
  `jwtIssuer` must not be exposed publicly.** The stack warns about this for `prod`.

Environment variables set by the stack:

| Function | Variables |
|---|---|
| worker | `DATABASE_SECRET_ARN`, `EXPORT_BUCKET`, `EXPORT_QUEUE_URL`, `JOBS_TABLE`, `PART_ROWS`, `MAX_PARTS`, `MAX_RECEIVE_COUNT=3`, `TMP_DIR=/tmp`, `MAIL_TRANSPORT=ses`, `MAIL_FROM`, `MAIL_TO_DOMAIN`, `APP_BASE_URL`, `STAGE` |
| api | `DATABASE_SECRET_ARN`, `EXPORT_BUCKET`, `EXPORT_QUEUE_URL`, `JOBS_TABLE`, `PRESIGN_SECONDS`, `AUTH_MODE`, `CORS_ORIGINS` (optional), `STAGE` |

The Lambda runtime sets `AWS_REGION`. `AWS_ENDPOINT_URL` is never set on AWS.

## IAM

| Role | Allowed |
|---|---|
| worker | `s3:PutObject/GetObject/DeleteObject/AbortMultipartUpload` on `bucket/exports/*` · `dynamodb:GetItem/UpdateItem` on the table · `sqs:SendMessage` on its own queue (plus the consume actions the event source adds) · `secretsmanager:GetSecretValue` on the exporter secret · `ses:SendEmail/SendRawEmail` with a `ses:FromAddress` condition · logs to its own log group |
| api | `s3:GetObject` on `bucket/exports/*` (for presigning) · `dynamodb:GetItem/PutItem/UpdateItem/Query` on the table and `gsi_owner_requested` · `sqs:SendMessage` · its own secret · logs to its own log group |

Neither role gets `AWSLambdaBasicExecutionRole`. In VPC mode the AWS-managed
`AWSLambdaVPCAccessExecutionRole` is added for ENI management (and suppressed in cdk-nag with a reason).

## Outputs

`BucketName`, `QueueUrl`, `DlqUrl`, `TableName`, `ApiUrl`, `WorkerFunctionName`, `AlarmTopicArn`,
`WorkerDbSecretArn`, `ApiDbSecretArn`.

## Cost notes

When idle, the default stack costs almost nothing: everything is pay-per-use, plus two Secrets
Manager secrets at about $0.40/month each. VPC mode adds five interface endpoints in two AZs,
about $70/month. Use it only when the database is private.
