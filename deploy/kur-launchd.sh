#!/usr/bin/env bash
# launchd işleri: (1) periyodik devriye (varsayılan 30 dk), (2) Telegram botu (sürekli)
#
# Kullanım:
#   ./deploy/kur-launchd.sh            # kur
#   ./deploy/kur-launchd.sh kaldir     # kaldır
#   VIGIL_INTERVAL=900 ./deploy/kur-launchd.sh   # 15 dk'da bir
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT="$PWD"

INTERVAL="${VIGIL_INTERVAL:-1800}"   # saniye (1800 = 30 dk)
LABEL_PATROL="com.vigil.patrol"
LABEL_BOT="com.vigil.bot"
PLIST_DIR="$HOME/Library/LaunchAgents"
PYTHON="$(command -v python3)"

[ -f "$ROOT/secrets.env" ] || { echo "HATA: secrets.env yok — önce ./deploy/install.sh"; exit 1; }
[ -x "$ROOT/bin/amele" ] || { echo "HATA: bin/amele yok — önce ./deploy/install.sh"; exit 1; }
mkdir -p "$PLIST_DIR" "$ROOT/logs"

# secrets.env'i plist EnvironmentVariables XML'ine çevir
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
  } > "$plist"
}

load_plist() {
  local plist="$1"
  if launchctl bootstrap "gui/$(id -u)" "$plist" 2>/dev/null; then
    echo "    yüklendi: $plist"
  else
    launchctl load "$plist" 2>/dev/null && echo "    yüklendi (load): $plist" || echo "    UYARI: $plist yüklenemedi (zaten yüklü olabilir)"
  fi
}

if [ "${1:-}" = "kaldir" ]; then
  echo "==> Kaldırılıyor"
  launchctl bootout "gui/$(id -u)" "$PLIST_DIR/$LABEL_PATROL.plist" 2>/dev/null || launchctl unload "$PLIST_DIR/$LABEL_PATROL.plist" 2>/dev/null || true
  launchctl bootout "gui/$(id -u)" "$PLIST_DIR/$LABEL_BOT.plist" 2>/dev/null || launchctl unload "$PLIST_DIR/$LABEL_BOT.plist" 2>/dev/null || true
  rm -f "$PLIST_DIR/$LABEL_PATROL.plist" "$PLIST_DIR/$LABEL_BOT.plist"
  echo "    kaldırıldı."
  exit 0
fi

echo "==> Periyodik devriye (her $((INTERVAL / 60)) dk)"
write_plist "$LABEL_PATROL" "$PLIST_DIR/$LABEL_PATROL.plist" \
  "$ROOT/bin/amele" "run" "agent.yaml" "-q" "Kamera devriyesi yap ve raporu gönder."
# StartInterval plist'e ayrıca eklenir (write_plist sonrası append)
/usr/libexec/PlistBuddy -c "Add :StartInterval integer $INTERVAL" "$PLIST_DIR/$LABEL_PATROL.plist" 2>/dev/null || \
  /usr/libexec/PlistBuddy -c "Set :StartInterval $INTERVAL" "$PLIST_DIR/$LABEL_PATROL.plist"
load_plist "$PLIST_DIR/$LABEL_PATROL.plist"

echo "==> Telegram botu (sürekli)"
write_plist "$LABEL_BOT" "$PLIST_DIR/$LABEL_BOT.plist" "$PYTHON" "$ROOT/bot/telegram_bot.py"
/usr/libexec/PlistBuddy -c "Add :KeepAlive bool true" "$PLIST_DIR/$LABEL_BOT.plist" 2>/dev/null || true
/usr/libexec/PlistBuddy -c "Add :RunAtLoad bool true" "$PLIST_DIR/$LABEL_BOT.plist" 2>/dev/null || true
load_plist "$PLIST_DIR/$LABEL_BOT.plist"

echo
echo "Kuruldu. Loglar: logs/com.vigil.*.log"
echo "Kaldırmak için: ./deploy/kur-launchd.sh kaldir"
