# Pipecat voice-agent deployment reference — 2026-06-29

## Environment

- Host: x1tablet (Debian 13 trixie, Linux 6.12)
- LLM endpoint: Headroom/LiteLLM at `serverhome.tail2e6efb.ts.net/litellm/headroom/v1/`
- Model: `minimax` (via Headroom proxy)
- ASR: Whisper small (faster-whisper, local)
- TTS: Edge TTS (zh-CN-XiaoxiaoNeural)
- VAD: Silero (local ONNX)
- Transport: FastAPI WebSocket at port 8765

## Project location

`~/workspace/pipecat/`
- Python 3.11.15 via uv, isolated venv
- All caches in `./cache/` (not ~/.cache)
- deps: `pipecat-ai[whisper,websocket]` + `edge-tts` + `fastapi` + `uvicorn`

## Three approaches tried

1. **Pure custom WebSocket** — src/bot.py + src/pipeline.py + RawPCMSerializer. User rejected: "跟官方一点都没关系吗？"
2. **Official SmallWebRTC + PrebuiltUI** — `POST /api/offer` returned 200 ✓, but browser got RTVI `"Not Found"` error. Root cause: PrebuiltUI uses RTVI v2 over data channel after SDP exchange. Requires `SmallWebRTCRequestHandler` to route RTVI actions.
3. **Back to FastAPI WebSocket with test page** — working config. HTTP 200, WS connect OK, received 1920 bytes TTS audio.

## RTVI protocol gotcha

The PrebuiltUI SPA sends `describe-actions`, `start-bot` etc. via WebRTC data channel after ICE completes. Without a server-side RTVI handler, the bot returns `"Not Found"` and conversation never starts. The official example at `examples/transports/transports-small-webrtc.py` handles this via `SmallWebRTCRequestHandler`.

Use `SmallWebRTCRequestHandler(run_bot)` which wraps offer/answer + RTVI data channel dispatch.
