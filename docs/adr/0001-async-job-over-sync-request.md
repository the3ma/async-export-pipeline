# ADR-0001: An export is an asynchronous job, not a synchronous request

**Status:** accepted

## Context
Exports range from a few rows to tens of millions. A synchronous request is bounded by load-balancer
timeouts (~30 s), by API memory, and by the user keeping the tab open. In the first version that forced a
hidden `LIMIT 100000`, which truncated results without any warning.

## Decision
`POST /api/exports` validates the request, records a job (`queued`), publishes a queue message and returns
`202 Accepted` with a `Location` header. A separate worker produces the file. Clients follow the job's
status; they never wait on the export.

## Consequences
- No timeout or memory ceiling ties result size to the HTTP layer. Truncation is impossible by design.
- New moving parts: a queue, a worker and a job store, plus a status lifecycle to keep consistent. This is
  mitigated by conditional state transitions and idempotent stages (ADR-0003).
- The UI needs status polling and a "My exports" tray. That turns out to be a feature: users can leave and
  come back.
