# pipecat-ai-small-webrtc-prebuilt v2.5.0 — verified layout

Snapshot taken 2026-06-30 from `~/workspace/pipecat/.venv/lib/python3.11/site-packages/`.

## Wheel contents

```
pipecat_ai_small_webrtc_prebuilt/
├── __init__.py          # empty — all API is in frontend.py
├── frontend.py          # 28 lines — the SmallWebRTCPrebuiltUI ASGI app
├── client/
│   └── dist/
│       ├── index.html
│       ├── favicon.svg
│       ├── pipecat-logo.svg
│       └── assets/      # pre-built JS bundle (hash-named)
└── __pycache__/
```

`frontend.py` exposes `SmallWebRTCPrebuiltUI` — a Starlette app that serves
`client/dist/*`. Mount it as `app.mount("/client", SmallWebRTCPrebuiltUI())`.

## pipecat 1.4.0 transport source layout

```
pipecat/transports/smallwebrtc/
├── __init__.py          # empty
├── request_handler.py   # 260 lines — FastAPI + RTVI data channel
├── transport.py         # 1025 lines — SmallWebRTCTransport
└── connection.py        # 811 lines — WebRTC connection mgmt
```

In pipecat 1.4.0 the `transports/` dir exists. Older 1.x releases may not
have it — if missing, upgrade.

## RTVI v2 protocol (what the client sends after SDP)

After SDP exchange on `/api/offer`, the client opens the WebRTC data channel
and sends RTVI messages like `describe-actions`, `start-bot`, etc. The
`SmallWebRTCRequestHandler` registers handlers for these automatically. If
you write a custom handler you must register them or you'll see:

```
Error: {"label":"rtvi-ai","type":"error-response","data":{"message":"Not Found","fatal":true}}
```

## Proven mount order (from project memory + this skill's Pitfall 2)

```python
# 1. API routes FIRST
@app.post("/api/offer")
async def offer(req): ...

@app.get("/")
async def root(): return RedirectResponse("/client/")

# 2. Static mount LAST
app.mount("/client", SmallWebRTCPrebuiltUI())
```

## Default port

SmallWebRTCRequestHandler default `port=7860` conflicts with many dashboards.
On this machine RawPCM already uses `PORT=8765`. PrebuiltUI runs cleanly on
`PORT=8766` without touching the RawPCM path. Two parallel servers, two
parallel clients, choose later.

## Why no `small-webrtc-prebuilt` example in pipecat-examples

The `pipecat-examples` repo (40 directories as of 2026-06-30) has no
`small-webrtc-prebuilt` example. Closest neighbours: `p2p-webrtc` (same
transport, custom client) and `bot-ready-signalling` (different transport).
For PrebuiltUI, read the venv source directly — that's the source of truth.
