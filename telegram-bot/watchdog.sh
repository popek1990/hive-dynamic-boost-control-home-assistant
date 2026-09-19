#!/bin/bash
# External health check for the Hive heating bot.
#
# Asks the bot's own /health endpoint whether it is alive. If it is not, it sends
# a Telegram message directly - the bot itself may be the thing that died.
#
# Run it from cron, for example every 15 minutes:
#   */15 * * * * /opt/hive-bot/watchdog.sh
#
# It reports once per outage and once when the bot comes back.

set -u

DIRECTORY="$(cd "$(dirname "$0")" && pwd)"
ENV_FILE="$DIRECTORY/.env"
STATE_FILE="$DIRECTORY/state.json"
SERVICE="${HIVE_BOT_SERVICE:-hive-bot}"
MARKER="$DIRECTORY/.watchdog-reported"

if [ ! -r "$ENV_FILE" ]; then
  echo "watchdog: cannot read $ENV_FILE" >&2
  exit 1
fi

# Read the settings without executing the file: a value containing $(...) in a
# sourced .env would run as a shell command.
setting() {
  sed -n "s/^[[:space:]]*$1[[:space:]]*=[[:space:]]*//p" "$ENV_FILE" \
    | head -n1 | sed -e 's/[[:space:]]*#.*$//' -e 's/^"\(.*\)"$/\1/' -e "s/^'\(.*\)'$/\1/"
}

TOKEN="$(setting TELEGRAM_TOKEN)"
CHAT_ID="$(setting TELEGRAM_CHAT_ID)"
[ -n "$CHAT_ID" ] || CHAT_ID="$(setting TELEGRAM_CZAT_ID)"   # old name, still accepted
PORT="$(setting BOT_HEALTH_PORT)"
[ -n "$PORT" ] || PORT=8099

# The chat id normally lives in .env; if it does not, take the one the bot bound
# itself to. Without it the alert cannot be delivered at all, so say so loudly.
if [ -z "$CHAT_ID" ] && [ -r "$STATE_FILE" ]; then
  CHAT_ID="$(python3 -c 'import json,sys
try:
    print(json.load(open(sys.argv[1])).get("chat_id") or "")
except Exception:
    print("")' "$STATE_FILE" 2>/dev/null)"
fi

if [ -z "$TOKEN" ] || [ -z "$CHAT_ID" ]; then
  echo "watchdog: no Telegram token or chat id - cannot alert. Set TELEGRAM_CHAT_ID in $ENV_FILE" >&2
  exit 2
fi

# Send a message without putting the token on the command line, where every
# local user could read it out of 'ps'.
notify() {
  printf 'url = "https://api.telegram.org/bot%s/sendMessage"\n' "$TOKEN" \
    | curl --config - --silent --show-error --max-time 15 \
        --data-urlencode "chat_id=$CHAT_ID" \
        --data-urlencode "text=$1" >/dev/null
}

if curl -fsS --max-time 10 "http://127.0.0.1:${PORT}/health" >/dev/null 2>&1; then
  if [ -f "$MARKER" ]; then
    rm -f "$MARKER"
    notify "✅ The heating bot is answering again (health check OK)."
  fi
  exit 0
fi

STATUS="$(systemctl is-active "$SERVICE" 2>/dev/null || echo unknown)"
if [ ! -f "$MARKER" ]; then
  : > "$MARKER"
  notify "⚠️ The heating bot is not answering its health check (service: ${STATUS}). Check: journalctl -u ${SERVICE} -n 50"
fi
exit 1
