# pipecat-ai-small-webrtc-prebuilt v2.5.0 — verified transport API

Snapshot taken 2026-06-30 from `~/workspace/pipecat/.venv/lib/python3.11/site-packages/`.
This file documents the **exact API surface** that works, including the
broken-import trap that bit me once. Treat it as the source of truth over
memory and old blog posts.

## The working imports (pipecat 1.4.0)

```python
from pipecat.transports.base_transport import TransportParams
from pipecat.transports.smallwebrtc.connection import IceServer, SmallWebRTCConnection
from pipecat.transports.smallwebrtc.request_handler import SmallWebRTCRequestHandler
from pipecat.transports.smallwebrtc.transport import SmallWebRTCTransport
from pipecat_ai_small_webrtc_prebuilt.frontend import SmallWebRTCPrebuiltUI
```

## Pitfall: `SmallWebRTCTransportParams` does NOT exist

There is no `SmallWebRTCTransportParams` class in 1.4.0. It is a hallucinated
name that appears in some blog posts and even in the LSP completion list at
times. The correct params class is the generic `TransportParams` from
`pipecat.transports.base_transport`.

LSP will flag `SmallWebRTCTransportParams` as `unknown import`. **Treat
that warning as a real bug, not lint noise.** The import fails at runtime
inside the first WebRTC request handler — the server starts, the static
client loads, SDP exchange returns 200, but the moment a client sends a
real offer, the worker tries to construct the transport and crashes with
`ImportError: cannot import name 'SmallWebRTCTransportParams'`.

Reproduction from logs (2026-06-30, this session):

```
webrtc_connection_callback failed for peer SmallWebRTCConnection#0-...:
  cannot import name 'SmallWebRTCTransportParams' from
  'pipecat.transports.smallwebrtc.transport'
```

Fix: replace with `TransportParams` and import from `pipecat.transports.base_transport`.

## Constructor signatures (verified by reading source)

```python
# PrebuiltUI is a StaticFiles instance — just mount it, do NOT call ()
# (calling returns a NEW StaticFiles but loses html=True)
SmallWebRTCPrebuiltUI  # type: starlette.staticfiles.StaticFiles

# STUN/TURN config (omit for host-only, but then ICE fails across NATs)
webrtc_handler = SmallWebRTCRequestHandler(
    ice_servers=[IceServer(urls="stun:stun.l.google.com:19302")],
)

# Transport with explicit 16kHz mono (matches Edge TTS / Whisper defaults)
transport = SmallWebRTCTransport(
    webrtc_connection=connection,
    params=TransportParams(
        audio_in_enabled=True,
        audio_out_enabled=True,
        audio_in_sample_rate=16000,
        audio_out_sample_rate=16000,
        video_in_enabled=False,         # audio-only path; video transport untested
        video_out_enabled=False,
    ),
)
```

`TransportParams` field list (from `pipecat/transports/base_transport.py`):
- `audio_in_enabled`, `audio_in_sample_rate`, `audio_in_channels`, `audio_in_filter`, `audio_in_stream_on_start`, `audio_in_passthrough`
- `audio_out_enabled`, `audio_out_sample_rate`, `audio_out_channels`, `audio_out_bitrate`
- `video_in_enabled`, `video_out_enabled`, `video_out_is_live`, `video_out_width/height/bitrate/framerate/color_format/codec`
- NO `vad_analyzer` field — pass `SileroVADAnalyzer()` to `LLMUserAggregatorParams(vad_analyzer=vad)` in your pipeline composition, not via transport.

## Mount path trap

`SmallWebRTCPrebuiltUI` is a `StaticFiles` instance. It catches every
unrouted request under whatever prefix you mount it. If you mount at `/`:

```python
app.mount("/", SmallWebRTCPrebuiltUI)   # WRONG
# → POST /api/offer gets 405 because StaticFiles eats it
#   and returns 405 (Method Not Allowed) instead of routing to your handler.
```

Always mount at a sub-path AND register the API route before the mount:

```python
@app.post("/api/offer")                  # 1. API first
async def offer(req: SmallWebRTCRequest, ...): ...

@app.get("/")                            # 2. / → redirect to /client/
async def root(): return RedirectResponse("/client/")

app.mount("/client", SmallWebRTCPrebuiltUI, name="client")   # 3. mount last
```

Reference: `pipecat.runner.run` line 749 does the same thing
(`app.mount("/client", PipecatPrebuiltUI)` — the daily variant). The
small-webrtc variant follows the same convention.

## End-to-end verification (2026-06-30, this session)

The full working server is in `templates/server_prebuilt.py`. It was
verified against the running instance with an aiortc client:

| Probe                                  | Result                        |
| -------------------------------------- | ----------------------------- |
| `GET /client/`                         | 200, 441B, byte-identical to wheel `index.html` |
| `GET /client/assets/index-DOtyWvZp.js` | 200, 978KB                    |
| `GET /client/assets/index-MrVEiU1O.css`| 200, 94KB                     |
| `GET /client/favicon.svg`              | 200                           |
| `POST /api/offer` (real aiortc SDP)    | 200, SDP answer 1909B         |
| `pc_id` returned                       | `SmallWebRTCConnection#0-...` |
| aiortc client + host-only ICE          | `ICE=completed, connState=connected` |
| Server → client tracks                 | 1 audio track (`receivers=[track=audio]`) |

## Lint warning policy

When `write_file` or `patch` returns `lint.status: ok` but LSP diagnostics
flag `unknown import` (or similar), the import is broken at runtime —
do not dismiss it as noise. Re-verify the class name against the venv
source before continuing. (This bit me once — I shipped a server with
`SmallWebRTCTransportParams`, server started, SDP exchange worked, but
the pipeline crashed with `ImportError` on the first real connection.
Server-side logs were the only signal.)
