# vigil 🎥

Evdeki IP kameraları **düzenli aralıklarla gezen** ve Telegram'dan **kamera adıyla rapor** gönderen yerel AI ajanı. [amele](https://github.com/lasthumanintheloop/amele) üzerine kurulu: ajan tek bir YAML dosyası, runtime tek bir statik binary.

## Ne yapar?

- **Periyodik devriye** (varsayılan 30 dk): tüm kameraları tek tek gez, görüntüleri yerel görüntü modeliyle (Ollama + Qwen3-VL) analiz et, Telegram'a rapor gönder:
  ```
  📍 Devriye Raporu
  • Garaj: araç yerinde, kapı kapalı
  • Bahçe: hareket yok
  • Ön Kapı: ⚠️ insan görünüyor
  ```
- **İstediğin an** (Telegram botu): bota "kontrol et" yaz → ajan anında devriye yapıp raporu gönderir.
- **Anormallik fotoğrafı**: insan/hareket/anormallik tespit edilen kameranın karesi rapora eklenir.
- **Yedek mod**: ajan döngüsü sorun çıkarırsa `patrol.py --all` aynı işi deterministik yapar.

## Mimari

```
                    ┌─────────────────────────────────────────────┐
                    │  Mac Studio (lokalde, internet'e bağımlı değil) │
                    │                                             │
  Telegram ───────► │  bot/telegram_bot.py  ("kontrol et" dinler)  │
    (sen)     ◄───  │        │                                    │
                    │        ▼                                    │
                    │  bin/amele run agent.yaml  (ajan döngüsü)   │
                    │        │  tools (subprocess)                │
                    │  ┌─────┴─────────────────────┐              │
                    │  │ camera_status.py          │              │
                    │  │  ffmpeg → kare çek        │              │
                    │  │  Ollama /api/chat (Qwen3-VL) analiz      │
                    │  │ telegram_send.py / _photo.py             │
                    │  └───────────────────────────┘              │
                    │        ▲                                    │
                    │  Ollama (localhost:11434) ◄── 14 kamera (RTSP)│
                    └─────────────────────────────────────────────┘
```

**Önemli:** ajan (amele) görüntüyü kendisi GÖRMEZ — amele'nin döngüsü metindir. Görüntü analizini `camera_status.py` aracı yapar (ffmpeg ile kare + Ollama vision çağrısı) ve sonucu metin olarak ajana döndürür. Ajan bu metinleri derleyip raporu Telegram'dan gönderir.

## Repo yapısı

```
agent.yaml             # ajan tanımı (model, prompt, araçlar, bütçeler)
cameras.json           # kamera listesi (ad + RTSP adresi)
secrets.env.example    # gizli ayarlar şablonu → secrets.env
tools/                 # amele araçları (subprocess scriptleri)
  camera_status.py     #   kare çek + görüntü analizi
  telegram_send.py     #   Telegram metin
  telegram_photo.py    #   Telegram fotoğraf
  patrol.py            #   yedek deterministik devriye
bot/telegram_bot.py    # "kontrol et" tetikleyicisi (long-polling)
deploy/                # kurulum + launchd zamanlayıcı
```

## Kurulum (Mac Studio)

```bash
cd vigil
./deploy/install.sh
```

Kurulum: python3/ffmpeg/curl kontrolü → amele binary'sini `bin/`'e indirir (v0.1.0) → `secrets.env` ve `cameras.json` oluşturur (şablonlardan kopyalar) → `agent.yaml`'i doğrular.

Kurulum oluşturduktan sonra iki dosyayı düzenle:

**1. `secrets.env`** — Telegram botu + RTSP kimlik bilgileri:
- Telegram'da @BotFather'a gir, `/newbot` ile bot aç, token'ı `TELEGRAM_BOT_TOKEN`'a yapıştır.
- Bota kendinden bir mesaj at, sonra chat ID'ni bul (secrets.env.example içindeki tek satırlık komutla) ve `TELEGRAM_CHAT_ID`'e yaz.
- Kameraların RTSP kullanıcı/şifresini `RTSP_USER` / `RTSP_PASS`'e yaz (adreslere gömülmez).

**2. `cameras.json`** — 14 kameranı ekle:
```json
[
  { "name": "Garaj", "url": "rtsp://192.168.1.50:554/stream1" },
  { "name": "Bahçe", "url": "rtsp://192.168.1.51:554/stream1" }
]
```
`name` raporda görünen ad (Türkçe karakter serbest), `url` RTSP akış adresi. Kullanıcı/şifre `secrets.env`'den otomatik eklenir.

Model: `secrets.env` içinde `AMELE_MODEL=qwen3-vl` (varsayılan). `ollama list` ile kontrol et; 96GB RAM'de `qwen3-vl:30b` gibi daha güçlü bir sürüm de seçebilirsin (8b'nin 32K context'i dar kalabilir).

## Test

```bash
set -a; . secrets.env; set +a
bin/amele validate agent.yaml                  # yapılandırma doğru mu
python3 tools/telegram_send.py --test          # Telegram bağlantısı
bin/amele run agent.yaml "devriye yap"         # elle devriye (raporu Telegram'a atar)
python3 tools/patrol.py --all                  # yedek deterministik devriye
```

## Zamanlama: launchd (macOS)

```bash
./deploy/kur-launchd.sh              # 30 dk'da bir devriye + bot sürekli
VIGIL_INTERVAL=900 ./deploy/kur-launchd.sh   # 15 dk'ya çevir
./deploy/kur-launchd.sh kaldir       # kaldır
```

İki iş kurar: `com.vigil.patrol` (periyodik devriye, loglar `logs/`) ve `com.vigil.bot` (bot sürekli ayakta, KeepAlive). Cron tercih edersen:

```
*/30 * * * *  cd /path/to/vigil && set -a && . secrets.env && set +a && bin/amele run agent.yaml -q "devriye yap"
```

## Telegram'dan istek

Bota "kontrol et", "/kontrol" veya "devriye" yaz → devriye başlar, bitince rapor gelir. Bot yalnızca `TELEGRAM_CHAT_ID` sahibine yanıt verir.

## Güvenlik notları

- `secrets.env` git'e girmez; RTSP şifreleri cameras.json'a yazılmaz.
- amele'nin workspace sandbox'ı **kaza önlemedir, güvenlik sınırı değildir** (kendi dokümanı böyle diyor). Asıl sınır, ajanın çalıştığı kullanıcı hesabıdır.
- **Prompt injection:** kamera görüntüsüne yazı/afiş konulursa model kandırılıp garip mesajlar atabilir. Bizim araçlar yalnızca "kare çek + analiz + Telegram'a mesaj" olduğu için hasar senaryosu sınırlıdır; yine de ajanı root olarak çalıştırma.
- amele yeni (v0.1.0, tek geliştirici) — bu sistem alarm değil, haberci. Kritik güvenlik kararlarını buna bağlama.

## Bilinen sınırlar / geliştirme fikirleri

- amele döngüsünde görüntü desteği yok → vision işi araç içinde (bilinçli tasarım).
- Ollama'nın OpenAI-uyumlu `/v1` ucunda tool-calling: qwen3-vl ile çalışması beklenir; takılırsa Ollama'yı güncelle veya `patrol.py --all` yedek modunu kullan.
- İstenirse: hareket algılayan ONVIF olaylarıyla tetikleme, kamera başına özel not, rapora hava durumu ekleme, birden fazla chat_id.
