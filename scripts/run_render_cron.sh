#!/bin/sh
set -eu

current_date=$(TZ=Asia/Kolkata date +%F)
if [ -f data/last_email_date.txt ] && [ "$(tr -d '[:space:]' < data/last_email_date.txt)" = "$current_date" ]; then
    echo "=========================================================="
    echo "Mail sent successfully for $current_date."
    echo "Briefing already delivered today. Skipping pipeline execution and dashboard update."
    echo "=========================================================="
    exit 0
fi

mkdir -p tmp
find tmp data -type f \( -name '*.db.lock' -o -name '*-journal' \) -print -delete

python run_daily_15.py

if [ -f data/no_news_found.txt ] && [ "$(tr -d '[:space:]' < data/no_news_found.txt)" = "$current_date" ]; then
    echo "DASHBOARD_SYNC_SKIPPED: no news found for $current_date"
    exit 0
fi

if [ -f data/dashboard_sync_date.txt ] && [ "$(tr -d '[:space:]' < data/dashboard_sync_date.txt)" = "$current_date" ]; then
    echo "DASHBOARD_SYNC_SKIPPED: dashboard already synced for $current_date"
    exit 0
fi

briefing_date=""
if [ -f data/briefing_date.txt ]; then
    briefing_date=$(tr -d '[:space:]' < data/briefing_date.txt)
fi
if [ "$briefing_date" != "$current_date" ]; then
    echo "DASHBOARD_SYNC_SKIPPED: no current-date briefing artifact"
    exit 0
fi

if [ -f data/copy_paste_briefing.txt ] && python run_dashboard_sync.py --file data/copy_paste_briefing.txt; then
    exit 0
fi

python run_dashboard_sync.py --file data/final_briefing.txt