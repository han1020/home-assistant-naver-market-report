#!/usr/bin/env bash
set -euo pipefail

CONFIG_PATH=/data/options.json

OPENAI_API_KEY="$(jq -r '.openai_api_key // ""' "$CONFIG_PATH")"
MODEL="$(jq -r '.model // "gpt-5"' "$CONFIG_PATH")"
SCHEDULE_TIME="$(jq -r '.schedule_time // "10:00"' "$CONFIG_PATH")"
TZ_VALUE="$(jq -r '.timezone // "Asia/Seoul"' "$CONFIG_PATH")"
MAX_PAGES="$(jq -r '.max_pages // 3' "$CONFIG_PATH")"
OUTPUT_SUBDIR="$(jq -r '.output_subdir // "analysis"' "$CONFIG_PATH")"
PUBLISH_PUBLIC="$(jq -r '.publish_public // true' "$CONFIG_PATH")"
PUBLIC_SUBDIR="$(jq -r '.public_subdir // "www"' "$CONFIG_PATH")"
RUN_ON_START="$(jq -r '.run_on_start // true' "$CONFIG_PATH")"
LOCAL_ONLY="$(jq -r '.local_only // false' "$CONFIG_PATH")"
SKIP_ATTACHMENTS="$(jq -r '.skip_attachments // false' "$CONFIG_PATH")"

export OPENAI_API_KEY
export TZ="$TZ_VALUE"
PYTHON="/opt/venv/bin/python3"
APP="/app/naver_market_report.py"
MOBILE_DIR="/config/${OUTPUT_SUBDIR#/}"
PUBLIC_DIR="/config/${PUBLIC_SUBDIR#/}"

mkdir -p /data/outputs /data/downloads /data/state "$MOBILE_DIR"
if [[ "$PUBLISH_PUBLIC" == "true" ]]; then
  mkdir -p "$PUBLIC_DIR"
fi

run_report() {
  echo "[naver-market-report] Starting report run at $(date '+%Y-%m-%d %H:%M:%S %Z')"

  args=(
    "$APP"
    --max-pages "$MAX_PAGES"
    --output-dir /data/outputs
    --downloads-dir /data/downloads
    --state-file /data/state/seen-reports.json
    --mobile-dir "$MOBILE_DIR"
    --model "$MODEL"
    --no-notify-new
    --no-notify-output
  )

  if [[ "$LOCAL_ONLY" == "true" || -z "$OPENAI_API_KEY" ]]; then
    args+=(--local-only)
  fi

  if [[ "$SKIP_ATTACHMENTS" == "true" ]]; then
    args+=(--skip-attachments)
  fi

  "$PYTHON" "${args[@]}"

  if [[ "$PUBLISH_PUBLIC" == "true" ]]; then
    latest_html="$(find "$MOBILE_DIR" -maxdepth 1 -type f -name '*-market-analysis.html' | sort | tail -n 1)"
    if [[ -n "$latest_html" ]]; then
      cp "$latest_html" "$PUBLIC_DIR/$(basename "$latest_html")"
      cp "$latest_html" "$PUBLIC_DIR/latest.html"
      echo "[naver-market-report] Public HTML: $PUBLIC_DIR/latest.html"
      public_path="${PUBLIC_SUBDIR#www}"
      public_path="${public_path#/}"
      if [[ -n "$public_path" ]]; then
        echo "[naver-market-report] Public URL: /local/${public_path}/latest.html"
      else
        echo "[naver-market-report] Public URL: /local/latest.html"
      fi
    else
      echo "[naver-market-report] No HTML file found in $MOBILE_DIR to publish"
    fi
  fi

  echo "[naver-market-report] Finished report run at $(date '+%Y-%m-%d %H:%M:%S %Z')"
  echo "[naver-market-report] HTML output directory: $MOBILE_DIR"
}

seconds_until_next_run() {
  "$PYTHON" - "$SCHEDULE_TIME" "$TZ_VALUE" <<'PY'
import datetime as dt
import sys
from zoneinfo import ZoneInfo

schedule_time, timezone = sys.argv[1:3]
hour_text, minute_text = schedule_time.split(":", 1)
hour = int(hour_text)
minute = int(minute_text)
tz = ZoneInfo(timezone)
now = dt.datetime.now(tz)
target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
if target <= now:
    target += dt.timedelta(days=1)
print(max(1, int((target - now).total_seconds())))
PY
}

if [[ "$RUN_ON_START" == "true" ]]; then
  run_report || echo "[naver-market-report] Initial run failed"
fi

while true; do
  sleep_seconds="$(seconds_until_next_run)"
  echo "[naver-market-report] Next run in ${sleep_seconds}s at ${SCHEDULE_TIME} (${TZ_VALUE})"
  sleep "$sleep_seconds"
  run_report || echo "[naver-market-report] Scheduled run failed"
done
