# Pipecat Voice Agent

Self-hosted Pipecat voice agent with:
- **LLM**: OpenAI-compatible via Headroom / LiteLLM (default: `minimax`)
- **VAD**: Local Silero (ONNX)
- **ASR**: Local Whisper (`faster-whisper`, `small` model)
- **TTS**: Edge TTS (Microsoft online, ffmpeg-decoded)
- **Transport**: FastAPI WebSocket (`ws://0.0.0.0:8765/conversation`)

## Quick start

```bash
# 1. Install uv
curl -LsSf https://astral.sh/uv/install.sh | sh

# 2. Install ffmpeg
sudo apt install -y ffmpeg

# 3. Clone & setup
git clone https://github.com/yansinan/pipecat.server.git
cd pipecat.server

# 4. Configure
cp .env.example .env
# Edit .env: set LLM_BASE_URL / LLM_API_KEY / LLM_MODEL

# 5. Run
chmod +x run.sh
./run.sh
```

## Architecture

```
ws://client ──→ FastAPI WebSocket
                    ↓
        transport.input()
                    ↓
        WhisperSTTService (local)
                    ↓
        LLMUserAggregator (VAD-driven)
                    ↓
        OpenAILLMService ──→ Headroom / LiteLLM
                    ↓
        EdgeTTSService (ffmpeg MP3→PCM)
                    ↓
        transport.output()
                    ↓
              ws://client
```

## Files

| File | Purpose |
|---|---|
| `src/bot.py` | FastAPI WebSocket entry point |
| `src/pipeline.py` | Pipeline assembly (PipelineWorker + LLMContextAggregatorPair) |
| `src/services/edge_tts.py` | Microsoft Edge TTS service |
| `src/services/whisper_stt.py` | Local Whisper STT service |
| `run.sh` | Launcher with venv isolation |
| `DEPLOY.md` | Full deployment guide |

## Environment isolation

All files and caches stay inside the project root:

```
~/workspace/pipecat.server/
├── .venv/           # Python venv (uv-managed)
├── cache/           # Whisper + Silero + uv cache
├── .env             # Env vars (not committed)
└── src/
```

**Use `uv run` — never `source activate` or `pip install`.**

## License

BSD-2-Clause (matches upstream Pipecat)