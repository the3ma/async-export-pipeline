# Integration contract

The single source of truth that every component implements. If code and this file disagree,
fix the code or change this file in the same pull request.

## Domain

An **append-only activity log**: every user action in a product (sign-in, record created,
setting changed, file downloaded …) is one immutable row. Users with the `activity:read`
permission browse it in a grid and export it.

### Table `activity_event` (PostgreSQL 16)

| Column | Type | Notes |
|---|---|---|
| `event_id` | `BIGINT GENERATED ALWAYS AS IDENTITY` | primary key; keyset tie-breaker |
| `occurred_at` | `TIMESTAMPTZ NOT NULL` | keyset sort column |
| `actor_id` | `TEXT NOT NULL` | pseudonymous id (no names/e-mails in the log) |
| `area` | `TEXT NOT NULL` | one of `accounts`, `billing`, `catalog`, `orders`, `settings` |
| `action` | `TEXT NOT NULL` | `create`, `update`, `delete`, `view`, `export`, `sign_in` |
| `entity_type` | `TEXT NOT NULL` | e.g. `invoice`, `product` |
| `entity_id` | `TEXT NOT NULL` | |
| `details` | `JSONB NOT NULL DEFAULT '{}'` | field changes (`[{field, old, new}]`) — only in *detailed* exports |

- Index `ix_activity_area_time (area, occurred_at DESC, event_id DESC)` and
  `ix_activity_time (occurred_at DESC, event_id DESC)`.
- Append-only: a trigger raises on `UPDATE`/`DELETE`/`TRUNCATE`; the app role has `INSERT, SELECT` only;
  the export role has `SELECT` only.

## Export

### Request — `POST /api/exports`

```json
{ "area": "billing", "mode": "summary", "format": "csv",
  "filters": { "from": "2026-01-01T00:00:00Z", "to": "2026-06-30T23:59:59Z", "action": "update" } }
```

- `area`: one of the areas above or `all`. `mode`: `summary` | `detailed`. `format`: `csv` | `xlsx`.
- `filters` are all optional. Unknown fields → `422`.
- Response `202 Accepted`, header `Location: /api/exports/{export_id}`, body = the job record.
- The caller is identified by header `X-User-Id` (demo auth; nginx injects a fixed demo user). Every
  read/write is scoped to that owner.

### Job record (DynamoDB table `exports`, PK `export_id`, GSI `gsi_owner_requested` = `owner_id` + `requested_at`)

| Field | Type | Notes |
|---|---|---|
| `export_id` | string | ULID |
| `owner_id` | string | |
| `area`, `mode`, `format` | string | as requested |
| `filters` | map | as requested |
| `status` | string | `queued` → `running` → `completed` \| `failed` (also `queued` → `failed`) |
| `requested_at`, `started_at`, `completed_at` | ISO-8601 UTC string | |
| `high_water_event_id` | number | `MAX(event_id)` pinned when the export starts; parts read only `event_id <= high_water` |
| `row_count` | number | set on completion |
| `part_count`, `parts_done` | number | multi-part progress |
| `file_name` | string | see naming |
| `object_key` | string | `exports/{owner_hash}/{export_id}/{file_name}`; `owner_hash` = first 16 hex of sha256(owner_id) |
| `failure_code` | string | `QUEUE_SEND_FAILED`, `TOO_LARGE`, `ROWSET_CHANGED`, `WORKER_ERROR`, `EXPIRED` (response only) |
| `expires_at` | number | epoch seconds; DynamoDB TTL. requested + 7 d; completed + 7 d |
| `notified_at`, `seen_at` | ISO string | notification sent / user opened the tray item |
| `expected_rows` | number | internal: row count pinned with the high-water mark; each part is checked against the plan (`ROWSET_CHANGED`) |
| `next_cursor` | map | internal: where part `parts_done + 1` starts, so a lost "next part" message can be recovered |

### Endpoints

| Method + path | Purpose |
|---|---|
| `POST /api/exports` | queue an export (above) |
| `GET /api/exports/{id}` | status of one export (owner only, else `404`) — never contains a download URL |
| `GET /api/exports?limit=20&cursor=` | the caller's exports, newest first (GSI); `limit` 1–50 |
| `POST /api/exports/seen` | `{ "ids": ["..."] }` marks tray items seen |
| `POST /api/exports` on queue failure | `503 { detail, failure_code: QUEUE_SEND_FAILED, export: <job> }` |
| `POST /api/exports/{id}/download` | owner only, `completed` only, not expired: writes an `export`/`download` row to `activity_event` FIRST, then returns `{ "url": "<presigned, 60 s>" , "file_name": "..." }` |
| `GET /api/activity?area=&limit=100&cursor=` | grid page (keyset cursor) |
| `GET /api/health` | liveness + dependencies |

### Response shapes

- **Paginated lists** (`GET /api/exports`, `GET /api/activity`):
  `{ "items": [ ... ], "next_cursor": "<opaque string>" | null }`. The cursor is opaque
  (base64url JSON) and goes back as `?cursor=`.
- **Activity filters** are query params: `area`, `action`, `from`, `to` (ISO-8601), plus `limit` (1–500,
  default 100) and `cursor`.
- **Activity item**: `{ event_id, occurred_at, actor_id, area, action, entity_type, entity_id }`
  (`details` omitted from the grid).
- **Errors**: FastAPI style `{ "detail": "..." }`. Export-specific errors add `failure_code`, e.g.
  `410 { "detail": "Export expired", "failure_code": "EXPIRED" }` and
  `409 { "detail": "Export not completed", "failure_code": "NOT_READY" }`.

- **Seen**: `POST /api/exports/seen` returns `{ "ids": [...] }` with the ids actually updated; other owners' ids are skipped.
- **Download audit row**: `action=export, entity_type=download, entity_id=<export_id>, area=<export area>` (`accounts` for an
  `all` export), `details = { file_name, export_area }`. Pre-signed with `Content-Disposition: attachment`.
- **CSV safety**: UTF-8 with BOM; cells starting with `= + - @` are prefixed with `'` (formula-injection guard).

### Queue message (SQS `export-requests`, DLQ `export-requests-dlq`, maxReceiveCount 3)

```json
{ "export_id": "01J...", "stage": "export" }
{ "export_id": "01J...", "stage": "part", "part": 2, "cursor": {"occurred_at": "...", "event_id": 123} }
{ "export_id": "01J...", "stage": "zip" }
```

### Worker stages

1. **export**: claim the job (`queued` → `running`, conditional update), count matching rows and pin
   `high_water_event_id`. `rows <= PART_ROWS` → stream one file. `rows > PART_ROWS` → set `part_count`,
   enqueue `part 1`. `rows > MAX_PARTS * PART_ROWS` → `failed` / `TOO_LARGE`.
2. **part N**: keyset-read `PART_ROWS` rows ordered `occurred_at DESC, event_id DESC` starting after
   `cursor`, write `…_partNN-of-MM.<ext>` to S3, bump `parts_done`, enqueue `part N+1` with the last row
   as the cursor, or `zip` after the last part.
3. **zip**: stream all parts into one `.zip` (CSV deflated, XLSX stored, Zip64 as needed), set
   `completed`, `row_count`, `expires_at`, **then** delete the parts (crash-safe order) and send the notification.
- Idempotent: a re-delivered message for a finished stage is a no-op. Any stage that exhausts retries marks
  the job `failed` / `WORKER_ERROR` (DLQ alarm fires).
- Config: `PART_ROWS` (default 1,000,000; local demo 50,000), `MAX_PARTS` (default 20).

### File naming

`Activity_<Area>_<Summary|Detailed>_<DD-MMM-YYYY>_<HHmm>_UTC_<exportId>.<csv|xlsx|zip>`, parts
`…_part01-of-05.<ext>`. Area in title case (`Billing`, `All_Areas`). Never user data in a name.

### Notification

On `completed` / `failed`: an e-mail to the owner (local: Mailpit SMTP) with a link to
`/exports` — never the file or a download link; and the UI tray polls `GET /api/exports` (5 s → 30 s
backoff) and shows a toast when a job finishes.

### Retention

S3 lifecycle: objects under `exports/` expire after 7 days; DynamoDB TTL on `expires_at`.
Download after expiry → `410 Gone` with `failure_code: EXPIRED`.

## Runtime configuration (environment)

| Variable | Used by | Local value |
|---|---|---|
| `DATABASE_URL` | api, worker | `postgresql://app:app@db:5432/activity` (worker uses the read-only `exporter` role) |
| `AWS_ENDPOINT_URL` | api, worker | `http://localstack:4566` (unset in AWS) |
| `AWS_REGION` | api, worker | `us-east-1` |
| `EXPORT_BUCKET` | api, worker | `exports` |
| `EXPORT_QUEUE_URL` | api, worker | `http://localstack:4566/000000000000/export-requests` |
| `JOBS_TABLE` | api, worker | `exports` |
| `PART_ROWS`, `MAX_PARTS` | worker | `50000`, `20` |
| `SMTP_HOST`, `SMTP_PORT`, `MAIL_FROM` | worker | `mailpit`, `1025`, `exports@example.test` |
| `APP_BASE_URL` | worker | `http://localhost:8080` |
| `PRESIGN_SECONDS` | api | `60` |
| `PUBLIC_S3_ENDPOINT_URL` | api | `http://localhost:4566` (host the browser uses for pre-signed URLs; unset in AWS) |
| `S3_PART_SIZE`, `FETCH_SIZE`, `RETRY_DELAY_SECONDS` | worker | multipart chunk, cursor fetch size, local poller retry delay (15 s) |
| `LOG_LEVEL` | api, worker | `INFO` |

### AWS-only configuration (set by the CDK stack)

| Variable / item | Used by | Rule |
|---|---|---|
| Lambda handlers | infra | worker `export_worker.handler.handler`; API `export_api.lambda_handler.handler` (Mangum, `lifespan="auto"`, dep `mangum>=0.17`) |
| `DATABASE_SECRET_ARN` | api, worker | Used when `DATABASE_URL` is unset: read the Secrets Manager secret (RDS-style JSON `{engine, host, port, dbname, username, password}`; an optional `url` key wins) and build `postgresql://user:pass@host:port/dbname`. Passwords never sit in Lambda env. |
| `MAIL_TRANSPORT` | worker | `smtp` (default, local Mailpit) or `ses` (SESv2 `send_email` with raw content). IAM limits `ses:SendEmail/SendRawEmail` to `ses:FromAddress == MAIL_FROM`. |
| `MAIL_TO_DOMAIN` | worker | demo recipient = `<owner_id>@<MAIL_TO_DOMAIN>` (a real system would look up the user's address) |
| `MAX_RECEIVE_COUNT`, `TMP_DIR` | worker | `3`, `/tmp` |
| `AUTH_MODE` | api | `header` (default: trust `X-User-Id` set by nginx) or `jwt` (identity = `sub` claim from the API Gateway JWT authorizer, via the Lambda request context) |
| `CORS_ORIGINS` | api | JSON list, set when a web origin is configured |
| `AWS_REGION` | all | from the Lambda runtime; `AWS_ENDPOINT_URL` unset in AWS |

## Local ports (docker compose)

| Service | URL |
|---|---|
| nginx (UI + `/api`) | `http://localhost:8080` |
| API direct (OpenAPI at `/docs`) | `http://localhost:8000` |
| Mailpit UI | `http://localhost:8025` |
| LocalStack | `http://localhost:4566` |
| PostgreSQL | `localhost:5432` |

## Repository layout

```
infra/            AWS CDK app (TypeScript)
services/api/     FastAPI service (Python 3.12, uv)
services/worker/  export worker: Lambda handler + local SQS poller (Python 3.12, uv)
services/shared/  python package `exportkit`: models, naming, job store, keys (used by api + worker)
web/              Vite + React + TypeScript UI
db/               schema + seed (SQL)
deploy/           nginx config, LocalStack init script
docs/             problem statement, ADRs, diagrams (.mmd + .png)
```
