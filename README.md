# vigil 🎥

Local AI agent that patrols your IP cameras on a schedule and reports to Telegram — running entirely on your own hardware. Built on [amele](https://github.com/lasthumanintheloop/amele): the agent is one YAML file, the runtime is one static binary.

## What it does

- **Periodic patrol** (default every 30 min): walks through every camera in your list, analyzes the live view with a local vision model (Ollama + Qwen3-VL), and sends a report to Telegram:
  ```
  📍 Patrol Report
  • Garage: vehicle in place, door closed
  • Garden: no motion
  • Front Door: ⚠️ a person is visible
  ```
- **On demand** (Telegram bot): message the bot "check" and it runs a patrol and sends the report immediately.
- **Anomaly snapshot**: the frame of any camera with a person / motion / anomaly is attached to the report.
- **Fallback mode**: if the agent loop ever misbehaves, `patrol.py --all` does the same job deterministically.

## Architecture

```
                    ┌─────────────────────────────────────────────┐
                    │  Your machine (local, no cloud dependency)  │
                    │                                             │
  Telegram ───────► │  bot/telegram_bot.py  (listens for "check")  │
    (you)     ◄───  │        │                                    │
                    │        ▼                                    │
                    │  bin/amele run agent.yaml  (agent loop)     │
                    │        │  tools (subprocess)                │
                    │  ┌─────┴─────────────────────┐              │
                    │  │ camera_status.py          │              │
                    │  │  ffmpeg → frame           │              │
                    │  │  Ollama /api/chat (vision)│              │
                    │  │ telegram_send.py / _photo.py             │
                    │  └───────────────────────────┘              │
                    │        ▲                                    │
                    │  Ollama (localhost:11434) ◄── cameras (RTSP) │
                    └─────────────────────────────────────────────┘
```

**Note:** the agent (amele) never sees images itself — its loop is text. Image analysis happens inside the `camera_status.py` tool (ffmpeg frame + Ollama vision call), which returns text to the agent. The agent compiles those texts into the report and sends it via Telegram.

## Repository layout

```
agent.yaml             # agent definition (model, prompt, tools, budgets)
cameras.example.json   # camera list template → cameras.json (your real file never enters git)
secrets.env.example    # secret settings template → secrets.env
tools/                 # amele tools (subprocess scripts)
  camera_status.py     #   capture frame + vision analysis
  telegram_send.py     #   Telegram text
  telegram_photo.py    #   Telegram photo
  patrol.py            #   fallback deterministic patrol
bot/telegram_bot.py    # "check" trigger (long-polling)
deploy/                # installer + scheduling helpers
```

## Requirements

- Python 3.10+
- [ffmpeg](https://ffmpeg.org/) (on PATH)
- [Ollama](https://ollama.com) with a vision model: `ollama pull qwen3-vl`
- A Telegram bot token from [@BotFather](https://t.me/BotFather)
- IP cameras with RTSP streams, reachable from the machine running vigil

Supported platforms: **macOS**, **Linux**, and **Windows (via WSL2)**. The amele binary currently ships macOS (arm64/amd64) and Linux (arm64/amd64) builds — on Windows, run vigil inside WSL2.

## Installation

Clone, then run the installer:

```bash
git clone https://github.com/loxigosoftware/vigil.git
cd vigil
./deploy/install.sh
```

The installer checks python3/ffmpeg/curl/git, downloads the amele binary into `bin/`, creates `secrets.env` and `cameras.json` from templates, and validates `agent.yaml`.

### Prerequisites per platform

- **macOS**: `brew install python3 ffmpeg` and [install Ollama](https://ollama.com) (or `brew install ollama`).
- **Linux**: `sudo apt install python3 ffmpeg git curl` (Debian/Ubuntu), then [install Ollama](https://ollama.com/download/linux).
- **Windows**: enable [WSL2](https://learn.microsoft.com/windows/wsl/install) with a distro (e.g. Ubuntu) and follow the Linux steps inside it. Simplest setup: install Ollama inside WSL as well, so everything runs on localhost. (If you'd rather run Ollama on the Windows side, point `OLLAMA_HOST` at the Windows host IP — WSL2's localhost is separate from Windows'.)

## Configuration

Two files are created by the installer — fill them in:

**1. `secrets.env`** — Telegram bot + RTSP credentials:
- Create a bot with [@BotFather](https://t.me/BotFather) (`/newbot`) and paste the token into `TELEGRAM_BOT_TOKEN`.
- Message your bot once, then find your chat ID (one-liner is in `secrets.env.example`) and put it in `TELEGRAM_CHAT_ID`.
- Put your cameras' RTSP username/password in `RTSP_USER` / `RTSP_PASS` (credentials are never embedded in the camera list).

**2. `cameras.json`** — list every camera you have (as many or as few as that is):
```json
[
  { "name": "Garage", "url": "rtsp://192.168.1.50:554/stream1" },
  { "name": "Garden", "url": "rtsp://192.168.1.51:554/stream1" }
]
```
`name` is the label shown in reports (any language), `url` is the RTSP stream address. Credentials are added automatically from `secrets.env`.

### Providers — local or online (single switch)

Everything — the agent loop *and* the image analysis — follows one switch in `secrets.env`. Local (Ollama) is the default; online is optional.

| Setup | `PROVIDER_TYPE` | `BASE_URL` | `API_KEY` | `AMELE_MODEL` |
|---|---|---|---|---|
| **Local (default)** | `openai` | `http://localhost:11434/v1` | *(empty)* | `qwen3-vl` |
| **OpenAI** | `openai` | `https://api.openai.com/v1` | `sk-...` | `gpt-4.1-mini` |
| **OpenRouter** | `openai` | `https://openrouter.ai/api/v1` | `sk-or-...` | `openai/gpt-4o-mini` |
| **Anthropic** | `anthropic` | `https://api.anthropic.com` | `sk-ant-...` | `claude-3-5-sonnet` |

- **Local** = Ollama on the same machine, works fully offline. **Online** = any OpenAI-compatible endpoint (OpenAI, OpenRouter, vLLM, ...) or the native Anthropic API.
- Image analysis follows the same switch: a local `BASE_URL` (localhost) uses Ollama's native API; an online one uses the provider's vision format. `VISION_MODEL` overrides the image-analysis model (defaults to `AMELE_MODEL`); `VISION_MODE` forces a specific mode if you ever need to.
- With `anthropic`, `BASE_URL` must **not** end in `/v1`. With OpenAI-compatible endpoints it normally does.
- Gemini is not natively supported by amele — use it through OpenRouter (`google/gemini-2.0-flash` style model names).
- API keys live only in `secrets.env` (never in git) and are referenced from `agent.yaml` as `${API_KEY}` — amele rejects literal keys in YAML.
- Check `ollama list` for locally available models; on a powerful machine you can pick a bigger one (e.g. `qwen3-vl:30b`).

**Report language:** follows the `system_prompt` in `agent.yaml` — the default is English; change it if you prefer your own language.

## First patrol

```bash
set -a; . secrets.env; set +a
bin/amele validate agent.yaml                  # is the config valid?
python3 tools/telegram_send.py --test          # Telegram connection (expect a test message)
bin/amele run agent.yaml "patrol"              # manual patrol → report lands in Telegram
python3 tools/patrol.py --all                  # fallback deterministic patrol
```

## Scheduling

### macOS — launchd

```bash
./deploy/kur-launchd.sh              # patrol every 30 min + bot always on
VIGIL_INTERVAL=900 ./deploy/kur-launchd.sh   # change to every 15 min
./deploy/kur-launchd.sh uninstall    # remove
```

Installs two jobs: `com.vigil.patrol` (periodic patrol, logs in `logs/`) and `com.vigil.bot` (bot always running, KeepAlive).

### Linux / WSL2 — cron (simplest)

```
*/30 * * * *  cd /path/to/vigil && set -a && . secrets.env && set +a && bin/amele run agent.yaml -q "patrol"
```

For the Telegram bot ("check" command), run `python3 bot/telegram_bot.py` as a service — e.g. this systemd unit:

```
[Unit]
Description=vigil Telegram bot
After=network.target

[Service]
WorkingDirectory=/path/to/vigil
EnvironmentFile=/path/to/vigil/secrets.env
ExecStart=/usr/bin/python3 /path/to/vigil/bot/telegram_bot.py
Restart=always

[Install]
WantedBy=multi-user.target
```

### Windows — Task Scheduler (WSL2)

Create a task (trigger: repeat every 30 minutes) that runs:

```
wsl -d Ubuntu -- bash -lc 'cd /path/to/vigil && set -a && . secrets.env && set +a && bin/amele run agent.yaml -q "patrol"'
```

(A cron line inside WSL works only while WSL is running; Task Scheduler is more reliable on Windows.)

## Telegram usage

Send the bot "check", "/check" or "patrol" → it runs a patrol and the report arrives when done. (Turkish triggers "kontrol et" / "devriye" still work.) The bot only responds to the owner's `TELEGRAM_CHAT_ID`.

## Security notes

- `secrets.env` and `cameras.json` never enter git — even though this repo is public, your home network layout and credentials stay private.
- amele's workspace sandbox is **accident prevention, not a security boundary** (per its own docs). The real boundary is the user account the agent runs under.
- **Prompt injection:** text/posters in a camera view could trick the model into odd messages. Our tools can only "capture + analyze + message Telegram", so the blast radius is limited — still, don't run the agent as root.
- amele is young (v0.1.0, single developer) — treat vigil as a messenger, not an alarm system. Don't base critical security decisions on it.

## Known limitations / ideas

- amele's loop has no image support → vision work lives in the tool (deliberate design).
- Ollama's OpenAI-compatible `/v1` endpoint tool-calling is expected to work with qwen3-vl; if it gets stuck, update Ollama or use `patrol.py --all` fallback.
- Ideas: ONVIF motion events as triggers, per-camera notes, weather in the report, multiple chat IDs.

## License

MIT — see [LICENSE](LICENSE).
