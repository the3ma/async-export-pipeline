# Running locally

The whole pipeline runs on one machine with Docker. LocalStack stands in for S3, SQS and DynamoDB,
Mailpit catches the notification e-mails, and the worker polls the queue the way the Lambda event
source mapping does in AWS.

## Prerequisites

| Tool | Version | Needed for |
|---|---|---|
| Docker + Compose v2 | 24+ | the stack |
| GNU make | any | shortcuts (optional; every target is a one-liner) |
| uv | 0.11+ | Python tests outside Docker |
| Node.js | 22 | web + infra tests outside Docker |

## Start, seed, export

```bash
make up                  # build images, start db, localstack, api, worker, web, mailpit; waits for health
make seed                # 1.2M activity events (SEED_ROWS=150000 make seed for a quick run)
make smoke               # end-to-end check, see below
```

| URL | What |
|---|---|
| http://localhost:8080 | Web UI (nginx serves the SPA and proxies `/api`) |
| http://localhost:8000/docs | FastAPI OpenAPI explorer |
| http://localhost:8025 | Mailpit - the "export ready" e-mails |
| http://localhost:4566 | LocalStack (S3 / SQS / DynamoDB) |
| localhost:5432 | PostgreSQL 16, database `activity` (`app`/`app`) |

The local `PART_ROWS` is 50,000 (1,000,000 in AWS), so a full export of the seeded log is split
into many parts and then zipped - the same code path a very large production export takes.

## Who am I?

Locally the API runs with `AUTH_MODE=header` and trusts `X-User-Id`. The nginx in front of the UI
injects `X-User-Id: demo-user`, so the browser always acts as one user. To act as somebody else,
call the API directly:

```bash
curl -s -H 'X-User-Id: someone-else' http://localhost:8000/api/exports      # empty list
```

In AWS the same API runs with `AUTH_MODE=jwt` and reads the subject from a verified token.

## What `make smoke` proves

1. The API is healthy and the log holds more rows than one part.
2. `POST /api/exports` returns `202` and a job id.
3. The job reaches `completed` with `row_count` equal to the table count, more than one part, and a `.zip` file name - and the status response never contains a URL.
4. Another user gets `404` for the same job.
5. `POST /api/exports/{id}/download` writes exactly one audit row, then returns a short-lived presigned URL.
6. The zip holds one CSV per part, named `_partNN-of-MM`, and the data rows add up to the total.
7. The e-mail links to the `/exports` page and carries no presigned URL and no attachment.

## Common tasks

```bash
make logs                                   # follow api + worker
docker compose exec db psql -U app activity # poke at the data
docker compose restart worker               # pick up a worker code change after `docker compose build worker`
make down                                   # stop and delete volumes (data is gone)
```

## Developing without Docker

```bash
uv sync && uv run pytest -q          # Python; integration tests need DATABASE_URL to a Postgres 16
cd web && npm ci && npm run dev:mock # UI against MSW mocks, no backend at all
cd web && npm run dev                # UI against a running API on :8000 (API_PROXY_TARGET to change)
cd infra && npm ci && npm test       # CDK assertions + cdk-nag
make test                            # everything CI runs, except the compose smoke test
```

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `make up` hangs on localstack | The init hook creates the bucket, queue and table; check `docker compose logs localstack`. |
| Job stays `queued` | Worker not running or cannot reach LocalStack - `docker compose logs worker`. |
| Download returns `410` `EXPIRED` | Files are kept for 7 days; LocalStack does not run lifecycle rules, so the API checks `expires_at` itself. |
| Job `failed` with `TOO_LARGE` | The export would exceed `MAX_PARTS` parts - narrow the area or date range. |
| Download link opens nothing | Presigned URLs live 60 seconds by design - press Download again. |
| Port already in use | Another local Postgres / nginx; change the left side of the port mapping in `docker-compose.yml`. |
