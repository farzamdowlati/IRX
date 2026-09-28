#!/usr/bin/env bash
# rollback-v1.sh — put the v1 model back, exactly as it was.
#
# The v2 cutover removed the v1 cron lines and disabled irx-listener.service. Nothing
# was deleted: the v1 scripts are still in /root/.irx, and the crontab is backed up
# before the change. This script restores BOTH, in the right order.
#
#   bash deploy/rollback-v1.sh            # on tr, from /root/.irx
#
# Why the order matters: irx-listener and irx-bot must never poll the same bot token
# at the same time (Telegram answers 409 to both), so the bot is stopped first.
set -euo pipefail

BAK_DIR=/root/irx-v1-cutover
echo "== IRX v1 rollback @ $(date -u '+%F %T') UTC =="

if [ ! -f "$BAK_DIR/crontab-v1.bak" ]; then
  echo "!! no backup at $BAK_DIR/crontab-v1.bak — refusing to guess. Restore by hand." >&2
  exit 1
fi

echo "-- stopping the v2 bot (frees the bot token)"
systemctl disable --now irx-bot || true

echo "-- restoring the v1 crontab"
crontab "$BAK_DIR/crontab-v1.bak"
crontab -l | grep -c irx_brief && echo "   v1 brief cron lines back in place"

echo "-- re-enabling the v1 listener"
systemctl enable --now irx-listener

echo "-- state"
systemctl is-active irx-listener irx-bot 2>/dev/null || true
echo "== done. The v2 database (data/irx.db) is left untouched for a later retry. =="
