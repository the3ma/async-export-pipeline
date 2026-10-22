# ADR-0002: Keyset pagination plus a pinned high-water mark

**Status:** accepted

## Context
`LIMIT/OFFSET` paging over a table that keeps growing skips or duplicates rows, and its cost grows with the
offset. A long-running repeatable-read transaction would give a snapshot, but it holds resources for the
whole export, and the export spans many worker invocations.

## Decision
- Order every read by `(occurred_at DESC, event_id DESC)`, which is backed by an index, and continue from the
  last row seen: `WHERE (occurred_at, event_id) < (:ts, :id)`.
- At the start of an export, pin `high_water_event_id = MAX(event_id)` in the job. Every later read adds
  `event_id <= high_water`, so rows inserted during the export are excluded consistently.
- Count once at the start. Each part's row count is checked against the plan, and a mismatch fails the job
  with `ROWSET_CHANGED` instead of producing a silently wrong file.

## Consequences
- Each part costs about the same no matter how deep it is, with no offset scans.
- The snapshot is defined by data (the high-water mark), not by a transaction, so it survives across
  invocations.
- This relies on the log being append-only. Updates and deletes are blocked by a trigger, so the guarantee
  holds.
