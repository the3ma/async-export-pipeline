# 2. The solution: an asynchronous, streaming, audited export pipeline

The core idea: **an export is a job, not a request.** The API only *records* the intent and returns
immediately. A worker does the heavy lifting in bounded steps, the UI follows the job's status, and the file
is handed out only through an owner-checked, audited, short-lived link.

## The building blocks

| Concern | Mechanism | Fixes problem # |
|---|---|---|
| Don't block the request | `POST /exports` writes a job (`queued`), sends a queue message, returns `202 Accepted` | 2, 7 |
| No row limit, bounded memory | Server-side cursor streaming; rows are written as they are read, never collected | 1, 3 |
| Excel limit | Results over `PART_ROWS` (1,000,000) are split into parts; one queue message per part | 6 |
| Stay inside a worker's time limit | Each part is its own worker invocation; the next part is enqueued by the previous one | 2 |
| One download | A final `zip` stage streams all parts into one archive (CSV deflated, XLSX stored), then deletes the parts | 6 |
| Consistent snapshot | `high_water_event_id` (= `MAX(event_id)` at start) is pinned in the job; every part reads `event_id <= high_water` | 5 |
| No page drift | **Keyset pagination** on `(occurred_at DESC, event_id DESC)`. The cursor is the last row of the previous part, not an offset | 4, 5 |
| Protect the database | Read-only `exporter` role, covering indexes, reserved worker concurrency (default 5) | 4 |
| Status and progress | Job record: `queued → running → completed / failed`, plus `parts_done / part_count` | 7 |
| "My exports" tray | `GET /exports` reads a per-owner index (newest first); the UI polls with back-off and shows a toast on completion | 7 |
| Notify, safely | E-mail with a link to the tray, never the file or a URL | 8 |
| Audited, owner-only download | `POST /exports/{id}/download` checks the owner, writes an `export / download` activity row **first**, then returns a 60-second pre-signed URL | 8, 10 |
| Retention | Storage lifecycle expires `exports/` after 7 days; the job table has a TTL | 9 |
| Operability | At-least-once delivery + **idempotent stages**, `maxReceiveCount 3` → dead-letter queue → alarm | 2, 7 |

## Why these choices (short ADRs)

- [ADR-0001](adr/0001-async-job-over-sync-request.md): an async job over a sync request
- [ADR-0002](adr/0002-keyset-pagination-and-high-water-mark.md): keyset pagination and a high-water mark
  instead of `OFFSET` / snapshot isolation
- [ADR-0003](adr/0003-one-invocation-per-part.md): one invocation per part, chained through the queue
- [ADR-0004](adr/0004-audit-before-presign.md): write the audit row before handing out a pre-signed URL
- [ADR-0005](adr/0005-no-links-in-email.md): notifications never carry the file or a link to it

## The flow, end to end

1. The user picks *Export → CSV (Summary)* in the grid. The UI calls `POST /api/exports` with the current
   filters.
2. The API validates the request, writes the job (`queued`, `expires_at = now + 7 d`) and sends
   `{ export_id, stage: "export" }` to the queue. If sending fails, the job is marked `failed /
   QUEUE_SEND_FAILED`, so a job never sits in `queued` forever.
3. The worker claims the job with a **conditional update** (`queued → running`). A re-delivered message
   finds the job already running and stops.
4. It counts the matching rows and pins `high_water_event_id`.
   - **≤ 1,000,000 rows:** stream one file to object storage (multipart upload), mark `completed`, notify.
   - **> 1,000,000 rows:** set `part_count`, enqueue `part 1`. Each part reads the next 1,000,000 rows from
     the keyset cursor, uploads `…_part02-of-05.csv`, increments `parts_done` and enqueues the next part.
     After the last part, `zip` merges them into `…​.zip`.
   - **> `MAX_PARTS` × 1,000,000:** `failed / TOO_LARGE`, and the user is told to narrow the filters.
5. The UI's background poll sees `completed` and shows a toast anywhere in the app. The e-mail arrives with
   a link to *My exports*.
6. *Download* calls `POST /api/exports/{id}/download`. The API checks the owner, the status and the expiry,
   **writes the audit row**, then returns a pre-signed URL that is valid for 60 s.

## What runs where

| | Local (docker compose) | AWS (CDK) |
|---|---|---|
| Activity log | PostgreSQL 16 | Amazon RDS / Aurora PostgreSQL (out of scope; connection via secret) |
| Job store | DynamoDB on LocalStack | DynamoDB on-demand, TTL, PITR in prod |
| Queue + DLQ | SQS on LocalStack | SQS with server-side encryption |
| Worker | `poller.py` container, same handler | Lambda (Python 3.12, arm64, 15 min, reserved concurrency) |
| API | FastAPI container | FastAPI on Lambda + HTTP API (Mangum adapter) |
| Files | S3 on LocalStack | S3 with block-public-access, SSE, 7-day lifecycle |
| E-mail | Mailpit | Amazon SES |
| Edge | nginx (UI + `/api`, injects a demo user) | API Gateway + CloudFront/S3 for the UI (not in this demo) |

Next: [3. Running it locally](03-running-locally.md) · [Integration contract](CONTRACT.md)
