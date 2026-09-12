#!/usr/bin/env bash
set -euo pipefail
: "${OR_KEY:?OPEN_ROUTER_KEY secret is required}"
DIFF_FILE="$1"
MODEL="$2"
OUT_FILE="$3"

# Same SYS_FULL system prompt and JSON schema as the production
# .github/scripts/review.sh in analitiq-ai/.github, so this is a faithful
# test of what a real /review full would send — the only variable is MODEL.
SYS_COMMON='Every comment'"'"'s "line" must be the new-file line number exactly as
you can count it in the unified diff below (the right-hand/"+" side) — never a
line number from any other view of the file. Only comment on a line that is
literally present in the diff text.'

SYS_FULL="You are a senior code reviewer. Report only real bugs, security issues,
and correctness problems. No style nits, no praise. There are no prior findings to
adjudicate — output an empty \"prior_findings\" array. $SYS_COMMON"

SCHEMA='{"type":"json_schema","json_schema":{"name":"review","strict":true,"schema":{
 "type":"object","required":["summary","comments","prior_findings"],"additionalProperties":false,
 "properties":{
  "summary":{"type":"string"},
  "prior_findings":{"type":"array","items":{
    "type":"object","required":["finding","status","note"],
    "additionalProperties":false,
    "properties":{"finding":{"type":"string"},
      "status":{"type":"string","enum":["resolved","not_addressed","partially_addressed"]},
      "note":{"type":"string"}}}},
  "comments":{"type":"array","items":{
    "type":"object","required":["path","line","severity","body"],
    "additionalProperties":false,
    "properties":{"path":{"type":"string"},"line":{"type":"integer"},
      "severity":{"type":"string","enum":["critical","major","minor"]},
      "body":{"type":"string"}}}}}}}}'

jq -n --arg m "$MODEL" --arg s "$SYS_FULL" \
      --arg d "$(cat "$DIFF_FILE")" \
      --arg p '[]' \
      --argjson f "$SCHEMA" '{
  model:$m, response_format:$f,
  messages:[{role:"system",content:$s},
            {role:"user",content:("PRIOR_FINDINGS:\n"+$p+"\n\nDIFF:\n"+$d)}]
}' > /tmp/bench_req.json

START=$(date +%s)
set +e
curl -sS --fail-with-body https://openrouter.ai/api/v1/chat/completions \
  -H "Authorization: Bearer $OR_KEY" -H "Content-Type: application/json" \
  -d @/tmp/bench_req.json -o /tmp/bench_response.json
CURL_STATUS=$?
set -e
END=$(date +%s)

if [ "$CURL_STATUS" -ne 0 ]; then
  echo "::error::request failed for $MODEL on $DIFF_FILE (curl exit $CURL_STATUS)"
  cat /tmp/bench_response.json >&2 || true
  jq -n --arg model "$MODEL" --arg diff "$DIFF_FILE" --arg err "curl exit $CURL_STATUS" \
    '{model:$model, diff:$diff, error:$err}' > "$OUT_FILE"
  exit 0
fi

# `|` binds looser than `//`, so `.content | fromjson? // {parse_error: .content}`
# parses as `.content | (fromjson? // {parse_error: .content})` — inside that
# parenthesized alternative, `.` is the content STRING, not the root response,
# so the fallback branch itself throws instead of capturing the raw content.
# Bind it with `as` to sidestep the precedence trap entirely.
jq --arg model "$MODEL" --arg diff "$DIFF_FILE" --argjson secs "$((END-START))" \
  '.choices[0].message.content as $c
   | {model:$model, diff:$diff, wall_seconds:$secs, usage,
      finish_reason: .choices[0].finish_reason,
      has_tool_calls: (.choices[0].message.tool_calls != null),
      parsed: (($c | fromjson?) // {parse_error: $c})}' \
  /tmp/bench_response.json > "$OUT_FILE"

cp /tmp/bench_response.json "${OUT_FILE%.json}.raw.json"
