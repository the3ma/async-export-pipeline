# ADR-0004: Write the audit row before handing out a pre-signed URL

**Status:** accepted

## Context
In an activity-log product, exporting the log is itself an activity. Once a pre-signed URL leaves the
API, the download happens directly between the browser and object storage, outside the application.

## Decision
`POST /api/exports/{id}/download` is the only way to get a download URL. It checks that the caller owns
the export, that the status is `completed` and that the export hasn't expired. Then it **inserts an
`export / download` activity row**, and only after that succeeds does it return a URL that is valid for
60 seconds. Status endpoints never return URLs.

## Consequences
- Every handed-out URL has a matching audit row (attributable, contemporaneous).
- If writing the audit row fails, the download fails. That's intentional: no audit, no file.
- Short URL lifetimes mean a copied link is useless within a minute.
