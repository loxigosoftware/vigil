#!/usr/bin/env bash
# launchd jobs: (1) periodic patrol (default every 30 min), (2) Telegram bot (always on)
#
# Usage:
#   ./deploy/install-launchd.sh            # install
#   ./deploy/install-launchd.sh uninstall  # remove (the old "kaldir" still works)
#   VIGIL_INTERVAL=900 ./deploy/install-launchd.sh   # patrol every 15 min
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT="$PWD"

INTERVAL="${VIGIL_INTERVAL:-1800}"   # seconds (1800 = 30 min)
LABEL_PATROL="com.vigil.patrol"
LABEL_BOT="com.vigil.bot"
PLIST_DIR="$HOME/Library/LaunchAgents"
PYTHON="$(command -v python3)"

[ -f "$ROOT/secrets.env" ] || { echo "ERROR: secrets.env missing — run ./deploy/install.sh first"; exit 1; }
[ -x "$ROOT/bin/amele" ] || { echo "ERROR: bin/amele missing — run ./deploy/install.sh first"; exit 1; }
mkdir -p "$PLIST_DIR" "$ROOT/logs"

# Convert secrets.env into plist EnvironmentVariables XML
env_xml=""
while IFS='=' read -r k v; do
  [ -n "$k" ] || continue
  v="${v%\"}"; v="${v#\"}"
  env_xml+="<key>${k}</key><string>${v}</string>"
done < <(grep -vE '^\s*(#|$)' "$ROOT/secrets.env")

write_plist() {  # $1=label $2=plist $3..=program+args
  local label="$1" plist="$2"; shift 2
  {
    echo '<?xml version="1.0" encoding="UTF-8"?>'
    echo '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">'
    echo '<plist version="1.0"><dict>'
    echo "<key>Label</key><string>${label}</string>"
    echo "<key>ProgramArguments</key><array>"
    for a in "$@"; do echo "<string>${a}</string>"; done
    echo "</array>"
    echo "<key>WorkingDirectory</key><string>${ROOT}</string>"
    echo "<key>EnvironmentVariables</key><dict>${env_xml}</dict>"
    echo "<key>StandardOutPath</key><string>${ROOT}/logs/${label}.out.log</string>"
    echo "<key>StandardErrorPath</key><string>${ROOT}/logs/${label}.err.log</string>"
    echo '</dict></plist>'
  } > "$plist"
}

load_plist() {
  local plist="$1"
  if launchctl bootstrap "gui/$(id -u)" "$plist" 2>/dev/null; then
    echo "    loaded: $plist"
  else
    launchctl load "$plist" 2>/dev/null && echo "    loaded (load): $plist" || echo "    WARNING: $plist could not be loaded (already loaded?)"
  fi
}

if [ "${1:-}" = "uninstall" ] || [ "${1:-}" = "kaldir" ]; then
  echo "==> Uninstalling"
  launchctl bootout "gui/$(id -u)" "$PLIST_DIR/$LABEL_PATROL.plist" 2>/dev/null || launchctl unload "$PLIST_DIR/$LABEL_PATROL.plist" 2>/dev/null || true
  launchctl bootout "gui/$(id -u)" "$PLIST_DIR/$LABEL_BOT.plist" 2>/dev/null || launchctl unload "$PLIST_DIR/$LABEL_BOT.plist" 2>/dev/null || true
  rm -f "$PLIST_DIR/$LABEL_PATROL.plist" "$PLIST_DIR/$LABEL_BOT.plist"
  echo "    removed."
  exit 0
fi

echo "==> Periodic patrol (every $((INTERVAL / 60)) min)"
write_plist "$LABEL_PATROL" "$PLIST_DIR/$LABEL_PATROL.plist" \
  "$ROOT/bin/amele" "run" "agent.yaml" "-q" "Check all cameras and send the patrol report."
# StartInterval is appended to the plist separately (after write_plist)
/usr/libexec/PlistBuddy -c "Add :StartInterval integer $INTERVAL" "$PLIST_DIR/$LABEL_PATROL.plist" 2>/dev/null || \
  /usr/libexec/PlistBuddy -c "Set :StartInterval $INTERVAL" "$PLIST_DIR/$LABEL_PATROL.plist"
load_plist "$PLIST_DIR/$LABEL_PATROL.plist"

echo "==> Telegram bot (always on)"
write_plist "$LABEL_BOT" "$PLIST_DIR/$LABEL_BOT.plist" "$PYTHON" "$ROOT/bot/telegram_bot.py"
/usr/libexec/PlistBuddy -c "Add :KeepAlive bool true" "$PLIST_DIR/$LABEL_BOT.plist" 2>/dev/null || true
/usr/libexec/PlistBuddy -c "Add :RunAtLoad bool true" "$PLIST_DIR/$LABEL_BOT.plist" 2>/dev/null || true
load_plist "$PLIST_DIR/$LABEL_BOT.plist"

echo
echo "Installed. Logs: logs/com.vigil.*.log"
echo "To uninstall: ./deploy/install-launchd.sh uninstall"
