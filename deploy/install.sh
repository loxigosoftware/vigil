#!/usr/bin/env bash
# vigil installer: dependency check + amele binary + secrets.env + cameras.json + verification
# Usage:  ./deploy/install.sh
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT="$PWD"

# macOS + Homebrew: /opt/homebrew/bin is not in the default non-interactive PATH
if [ "$(uname -s)" = "Darwin" ] && [ -d /opt/homebrew/bin ]; then
  export PATH="/opt/homebrew/bin:$PATH"
fi

echo "==> 1/6 Dependency check"
for cmd in python3 ffmpeg curl git; do
  if ! command -v "$cmd" >/dev/null 2>&1; then
    echo "    MISSING: $cmd is not installed (e.g. brew install $cmd / sudo apt install $cmd)"
    exit 1
  fi
done
echo "    python3, ffmpeg, curl, git: ok"

echo "==> 2/6 amele binary (v0.1.0)"
if [ ! -x "$ROOT/bin/amele" ]; then
  mkdir -p "$ROOT/bin"
  os="$(uname -s)"
  arch="$(uname -m)"
  case "$os-$arch" in
    Darwin-arm64)            asset="amele_0.1.0_darwin_arm64.tar.gz" ;;
    Darwin-x86_64|Darwin-amd64) asset="amele_0.1.0_darwin_amd64.tar.gz" ;;
    Linux-arm64)             asset="amele_0.1.0_linux_arm64.tar.gz" ;;
    Linux-x86_64|Linux-amd64) asset="amele_0.1.0_linux_amd64.tar.gz" ;;
    *) echo "    Unsupported platform: $os-$arch (on Windows? use WSL2)"; exit 1 ;;
  esac
  echo "    downloading: $asset"
  curl -sL "https://github.com/lasthumanintheloop/amele/releases/download/v0.1.0/$asset" -o /tmp/amele.tar.gz
  tar -xzf /tmp/amele.tar.gz -C "$ROOT/bin/"
  rm -f /tmp/amele.tar.gz
  chmod +x "$ROOT"/bin/amele*
fi
"$ROOT/bin/amele" version 2>/dev/null || true

echo "==> 3/6 secrets.env"
if [ ! -f "$ROOT/secrets.env" ]; then
  cp "$ROOT/secrets.env.example" "$ROOT/secrets.env"
  echo "    secrets.env created — EDIT IT NOW: nano secrets.env"
else
  echo "    secrets.env already exists."
fi

echo "==> 4/6 cameras.json"
if [ ! -f "$ROOT/cameras.json" ]; then
  cp "$ROOT/cameras.example.json" "$ROOT/cameras.json"
  echo "    cameras.json created — add your real camera info: nano cameras.json"
else
  echo "    cameras.json already exists."
fi

echo "==> 5/6 Agent validation"
export AMELE_MODEL="${AMELE_MODEL:-qwen3-vl}"
export PROVIDER_TYPE="${PROVIDER_TYPE:-openai}"
export BASE_URL="${BASE_URL:-http://localhost:11434/v1}"
export API_KEY="${API_KEY:-}"
"$ROOT/bin/amele" validate "$ROOT/agent.yaml" || {
  echo "    agent.yaml failed validation — see the error above."
  exit 1
}

echo "==> 6/6 Telegram test"
if [ -n "${TELEGRAM_BOT_TOKEN:-}" ] && [ -n "${TELEGRAM_CHAT_ID:-}" ]; then
  python3 "$ROOT/tools/telegram_send.py" --test
else
  echo "    (after filling TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in secrets.env:)"
  echo "    set -a; . secrets.env; set +a; python3 tools/telegram_send.py --test"
fi

echo
echo "Installation complete. Next steps:"
echo "  1) Edit secrets.env and cameras.json"
echo "  2) Manual test:   set -a; . secrets.env; set +a; bin/amele run agent.yaml \"patrol\""
echo "  3) Scheduling:    macOS: ./deploy/install-launchd.sh   |   Linux/WSL: cron or systemd (see README)"
