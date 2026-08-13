#!/usr/bin/env bash
# vigil kurulumu: bağımlılık kontrolü + amele binary + secrets.env + doğrulama
# Kullanım:  ./deploy/install.sh
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT="$PWD"

echo "==> 1/5 Bağımlılık kontrolü"
for cmd in python3 ffmpeg curl git; do
  if ! command -v "$cmd" >/dev/null 2>&1; then
    echo "    EKSİK: $cmd kurulu değil (ör. brew install $cmd)"
    exit 1
  fi
done
echo "    python3, ffmpeg, curl, git: tamam"

echo "==> 2/5 amele binary (v0.1.0)"
if [ ! -x "$ROOT/bin/amele" ]; then
  mkdir -p "$ROOT/bin"
  os="$(uname -s)"
  arch="$(uname -m)"
  case "$os-$arch" in
    Darwin-arm64)            asset="amele_0.1.0_darwin_arm64.tar.gz" ;;
    Darwin-x86_64|Darwin-amd64) asset="amele_0.1.0_darwin_amd64.tar.gz" ;;
    Linux-arm64)             asset="amele_0.1.0_linux_arm64.tar.gz" ;;
    Linux-x86_64|Linux-amd64) asset="amele_0.1.0_linux_amd64.tar.gz" ;;
    *) echo "    Desteklenmeyen platform: $os-$arch"; exit 1 ;;
  esac
  echo "    indiriliyor: $asset"
  curl -sL "https://github.com/lasthumanintheloop/amele/releases/download/v0.1.0/$asset" -o /tmp/amele.tar.gz
  tar -xzf /tmp/amele.tar.gz -C "$ROOT/bin/"
  rm -f /tmp/amele.tar.gz
  chmod +x "$ROOT"/bin/amele*
fi
"$ROOT/bin/amele" version 2>/dev/null || true

echo "==> 3/5 secrets.env"
if [ ! -f "$ROOT/secrets.env" ]; then
  cp "$ROOT/secrets.env.example" "$ROOT/secrets.env"
  echo "    secrets.env oluşturuldu — ŞİMDİ DÜZENLE: nano secrets.env"
else
  echo "    secrets.env zaten var."
fi

echo "==> 4/6 cameras.json"
if [ ! -f "$ROOT/cameras.json" ]; then
  cp "$ROOT/cameras.example.json" "$ROOT/cameras.json"
  echo "    cameras.json oluşturuldu — gerçek kamera bilgilerini gir: nano cameras.json"
else
  echo "    cameras.json zaten var."
fi

echo "==> 5/6 Ajan doğrulaması"
export AMELE_MODEL="${AMELE_MODEL:-qwen3-vl}"
"$ROOT/bin/amele" validate "$ROOT/agent.yaml" || {
  echo "    agent.yaml doğrulanamadı — yukarıdaki hataya bak."
  exit 1
}

echo "==> 5/5 Telegram testi"
if [ -n "${TELEGRAM_BOT_TOKEN:-}" ] && [ -n "${TELEGRAM_CHAT_ID:-}" ]; then
  python3 "$ROOT/tools/telegram_send.py" --test
else
  echo "    (secrets.env'de TELEGRAM_BOT_TOKEN ve TELEGRAM_CHAT_ID'i doldurduktan sonra:)"
  echo "    set -a; . secrets.env; set +a; python3 tools/telegram_send.py --test"
fi

echo
echo "Kurulum bitti. Sıradaki adımlar:"
echo "  1) secrets.env ve cameras.json düzenle"
echo "  2) Elle test:   set -a; . secrets.env; set +a; bin/amele run agent.yaml \"devriye yap\""
echo "  3) Zamanlayıcı: ./deploy/kur-launchd.sh   (30 dk'da bir + bot sürekli)"
