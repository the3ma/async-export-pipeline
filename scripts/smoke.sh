#!/usr/bin/env bash
# End-to-end proof against the running local stack (make up && make seed):
#   1. queue an export that is larger than PART_ROWS  -> must become a multi-part zip
#   2. follow the job to `completed` (parts_done / part_count progress)
#   3. another user must not see it (owner scoping)
#   4. audited download: the audit row exists BEFORE we fetch the file
#   5. the zip holds every part, and the data rows add up to the row count and to the database
#   6. the notification e-mail arrived and contains no file and no download link
set -euo pipefail

API="${API:-http://localhost:8080/api}"         # through nginx (injects X-User-Id: demo-user)
API_DIRECT="${API_DIRECT:-http://localhost:8000/api}"
MAILPIT="${MAILPIT:-http://localhost:8025}"
TIMEOUT="${TIMEOUT:-600}"
OUT="${OUT:-.smoke}"
mkdir -p "$OUT"; rm -f "$OUT"/*

say()  { printf '\n\033[1m== %s\033[0m\n' "$*"; }
fail() { printf '\033[31mFAIL: %s\033[0m\n' "$*" >&2; exit 1; }
ok()   { printf '\033[32mok\033[0m  %s\n' "$*"; }
psql_q() { docker compose exec -T db psql -U postgres -d activity -tAc "$1"; }

say "health"
curl -fsS "$API/health" | tee "$OUT/health.json" | jq -e '.status == "ok"' >/dev/null || fail "api not healthy"
ok "api healthy"

DB_ROWS=$(psql_q "SELECT count(*) FROM activity_event")
PART_ROWS=$(docker compose exec -T worker printenv PART_ROWS)
[ "$DB_ROWS" -gt "$PART_ROWS" ] || fail "seed more rows than PART_ROWS ($DB_ROWS <= $PART_ROWS): make seed SEED_ROWS=150000"
ok "database has $DB_ROWS events, PART_ROWS=$PART_ROWS"

say "1. queue a CSV summary export of all areas"
JOB=$(curl -fsS -X POST "$API/exports" -H 'Content-Type: application/json' \
  -d '{"area":"all","mode":"summary","format":"csv"}')
ID=$(jq -r '.export_id' <<<"$JOB")
[ -n "$ID" ] && [ "$ID" != null ] || fail "no export_id in $JOB"
ok "queued $ID ($(jq -r .file_name <<<"$JOB"))"

say "2. follow the job"
deadline=$(( $(date +%s) + TIMEOUT ))
while :; do
  J=$(curl -fsS "$API/exports/$ID")
  ST=$(jq -r .status <<<"$J")
  printf '   %s  parts %s/%s\n' "$ST" "$(jq -r '.parts_done // 0' <<<"$J")" "$(jq -r '.part_count // 1' <<<"$J")"
  case "$ST" in completed) break ;; failed) fail "job failed: $(jq -c . <<<"$J")" ;; esac
  [ "$(date +%s)" -lt "$deadline" ] || fail "timed out after ${TIMEOUT}s"
  sleep 3
done
ROWS=$(jq -r .row_count <<<"$J"); PARTS=$(jq -r .part_count <<<"$J"); NAME=$(jq -r .file_name <<<"$J")
[ "$ROWS" -eq "$DB_ROWS" ] || fail "row_count $ROWS != database $DB_ROWS"
[ "$PARTS" -gt 1 ] || fail "expected a multi-part export, got part_count=$PARTS"
[[ "$NAME" == *.zip ]] || fail "expected a .zip, got $NAME"
ok "completed: $ROWS rows in $PARTS parts -> $NAME"
jq -e 'has("url") | not' <<<"$J" >/dev/null || fail "status response must never contain a URL"
ok "status response has no download URL"

say "3. owner scoping"
code=$(curl -s -o /dev/null -w '%{http_code}' -H 'X-User-Id: someone-else' "$API_DIRECT/exports/$ID")
[ "$code" = 404 ] || fail "another user got HTTP $code (want 404)"
ok "another user gets 404"

say "4. audited download"
BEFORE=$(psql_q "SELECT count(*) FROM activity_event WHERE action='export' AND entity_type='download' AND entity_id='$ID'")
D=$(curl -fsS -X POST "$API/exports/$ID/download")
AFTER=$(psql_q "SELECT count(*) FROM activity_event WHERE action='export' AND entity_type='download' AND entity_id='$ID'")
[ "$AFTER" -eq $((BEFORE + 1)) ] || fail "no audit row written for the download"
ok "audit row written before the URL was handed out"
URL=$(jq -r .url <<<"$D")
curl -fsS -o "$OUT/$NAME" "$URL" || fail "download failed"
ok "downloaded $(du -h "$OUT/$NAME" | cut -f1)"

say "5. verify the archive"
python3 - "$OUT/$NAME" "$ROWS" "$PARTS" <<'PY'
import csv, io, sys, zipfile
path, rows, parts = sys.argv[1], int(sys.argv[2]), int(sys.argv[3])
with zipfile.ZipFile(path) as z:
    names = sorted(z.namelist())
    assert len(names) == parts, f"{len(names)} members != {parts} parts"
    for i, n in enumerate(names, 1):
        assert f"_part{i:02d}-of-{parts:02d}" in n, n
    total = 0
    for n in names:
        with z.open(n) as f:
            r = csv.reader(io.TextIOWrapper(f, encoding="utf-8-sig"))
            next(r)                      # header
            total += sum(1 for _ in r)
    assert total == rows, f"{total} data rows != {rows}"
print(f"   {parts} members, {total} data rows")
PY
ok "every part present, data rows add up"

say "6. notification e-mail"
sleep 2
MSG=$(curl -fsS "$MAILPIT/api/v1/search?query=$ID" | jq -r '.messages[0].ID // empty')
[ -n "$MSG" ] || fail "no e-mail mentioning $ID"
BODY=$(curl -fsS "$MAILPIT/api/v1/message/$MSG" | jq -r '.Text + .HTML')
grep -q '/exports' <<<"$BODY" || fail "e-mail does not link to the exports page"
grep -qiE '4566|X-Amz-Signature|\.zip"' <<<"$BODY" && fail "e-mail contains a file or a download link"
[ "$(curl -fsS "$MAILPIT/api/v1/message/$MSG" | jq '.Attachments | length')" -eq 0 ] || fail "e-mail has an attachment"
ok "e-mail links to /exports, no attachment, no download link"

say "SMOKE PASSED"
