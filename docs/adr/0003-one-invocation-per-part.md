# ADR-0003: One worker invocation per part, chained through the queue

**Status:** accepted

## Context
Serverless workers have a hard time limit (15 min on AWS Lambda). Writing 20 × 1,000,000 rows in one
invocation doesn't fit, and one failure would throw away all the work done so far.

## Decision
Split the work into stages: `export` (plan), `part N` (one per million rows) and `zip` (assemble). Each stage
is a queue message, and each one enqueues the next. Every stage is idempotent: it checks the job state and
the existing objects before writing, so an at-least-once re-delivery is safe. After `maxReceiveCount = 3` a
message goes to the dead-letter queue, an alarm fires, and the job is marked `failed`.

## Consequences
- Each invocation has a bounded duration and memory, and a failed part is retried on its own.
- Parts of one export run one after another, while different exports run in parallel up to the reserved
  concurrency. This caps the read load on the database.
- The zip stage streams the parts (CSV deflated, XLSX stored, Zip64), so it also has bounded memory.
