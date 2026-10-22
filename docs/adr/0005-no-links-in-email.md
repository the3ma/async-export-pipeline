# ADR-0005: Notifications never carry the file or a link to it

**Status:** accepted

## Context
E-mail is forwarded, archived, and read on shared devices. A link in an e-mail is a credential with no
owner check, and a download through it bypasses the audit path (ADR-0004).

## Decision
The completion or failure e-mail says *what* finished (area, type, row count, status) and links to the
"My exports" page. The user signs in, and the download goes through the audited endpoint. The in-app toast
does the same.

## Consequences
- One extra click for the user. In exchange, every download is owner-checked and audited.
- The e-mail contains no data, so a misdirected e-mail leaks nothing.
