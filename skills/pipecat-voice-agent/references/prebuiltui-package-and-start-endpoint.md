# PrebuiltUI — package trap & /start endpoint (2026-06-30)

This file supersedes the assumption in the older references that
`pipecat-ai-small-webrtc-prebuilt` is the right PrebuiltUI package. It is
not. The actual client bundle lives in a **different** PyPI package and
the wire protocol requires an extra HTTP endpoint the older reference
omits.

## Package trap: `pipecat-ai-small-webrtc-prebuilt` ≠ PrebuiltUI

`pipecat-ai-small-webrtc-prebuilt==2.5.0` (the package whose name sounds
right) ships a **broken client bundle**:

- The bundled `client/dist/assets/index-*.js` contains `Daily`-flavored
  glue (call-machine, sendMessageToCallMachine, etc.).
- It does **not** call `POST /start` or the standard
  `PipecatClient.startBot()` flow.
- When mounted and opened in a browser, the React UI renders but the
  "Connect" button never resolves — the page just spins ("一直在
  loading") because the client expects a Daily room key the server has
  no way to provide.
- This is the symptom that took down the first iteration of
  `src/server_prebuilt.py` (2026-06-30). Spent a whole debugging cycle
  before grepping the bundle for `/api/offer` and getting 0 hits.

The **real** PrebuiltUI client is in:

```
pip install pipecat-ai-prebuilt==1.0.3
```

```python
from pipecat_ai_prebuilt.frontend import PipecatPrebuiltUI
# Module-level StaticFiles instance — same mounting convention as the
# small-webrtc variant: app.mount("/client", PipecatPrebuiltUI)
```

That bundle's JS includes `PipecatClient`, `startBot`, and the four
transport branches (`smallwebrtc / daily / websocket / twilio`).

**Uninstall the broken package** if it accidentally landed in a venv —
it only adds weight and confusion:

```bash
uv pip uninstall pipecat-ai-small-webrtc-prebuilt
# and pin the right one in pyproject.toml:
#   dependencies = [..., "pipecat-ai-prebuilt>=1.0.3", ...]
```

## Mandatory endpoint: `POST /start`

The PrebuiltUI client flow is:

1. User clicks "Connect" in the React UI.
2. Client calls `PipecatClient.startBot({endpoint: "/start", requestData: {createDailyRoom: false, enableDefaultIceServers: true, transport: "webrtc"}})`.
3. Client `POST /start` with that body. Server mints a `pc_id` and returns `{"pc_id": "..."}`.
4. Client creates a `SmallWebRTCTransport` instance, generates a SDP offer, and `POST /api/offer` with `{sdp, type: "offer", pc_id}`.
5. Server creates the `SmallWebRTCConnection`, runs `handle_web_request` to produce an answer, returns `{sdp, type: "answer", pc_id}`.
6. ICE/STUN completes. Data channel opens. RTVI messages flow.

If step 3 (`/start`) returns 404, the client's `startBot` promise never
resolves and the page hangs in loading. Implementing only `/api/offer`
is not enough — `/start` is the actual entry point.

Minimal `/start` handler:

```python
import uuid
from fastapi import FastAPI

@app.post("/start")
async def start_bot():
    pc_id = str(uuid.uuid4())
    return {"pc_id": pc_id}
```

`pipecat.runner.run:app` (the official full-featured FastAPI) registers
both `/start` and `/api/offer` internally, so using it directly sidesteps
this — but it pulls in `pipecat_ai_prebuilt` and is harder to customise.
The hand-rolled `src/server_prebuilt.py` template in
`templates/server_prebuilt.py` registers both endpoints explicitly and
works against the real client bundle.

## Confirmed working endpoint set

The minimum route table for the real client to work:

```
GET   /                  → 307 redirect to /client/
POST  /start             → {"pc_id": "..."}
POST  /api/offer         → SDP exchange, returns {sdp, type, pc_id}
PATCH /api/offer         → ICE candidates
MOUNT /client/*          → PipecatPrebuiltUI static
```

## Verification: aiortc e2e probe (no browser required)

A full PrebuiltUI client flow can be simulated headlessly with aiortc.
This is the recipe used to verify the working server on 2026-06-30
without opening a browser — important when browser tooling is blocked
(`/tmp` full, Chrome CDP down, headless server, etc.).

The probe script lives in `templates/aiortc_e2e_probe.py`. It exercises
every step the real client would:

1. `POST /start` → grab `pc_id`.
2. `aiortc.RTCPeerConnection` + `addTransceiver("audio", "sendrecv")` →
   createOffer + setLocalDescription.
3. `POST /api/offer` with `{sdp, type: "offer", pc_id}` → consume answer.
4. `setRemoteDescription` on the answer.
5. Wait for `iceconnectionstatechange` → `completed` (timeout 10s).
6. Hold 8s — confirms server-side pipeline starts and doesn't crash.
7. Inspect `getReceivers()` for a server-pushed audio track.

If all seven steps print without traceback, the server is good for real
browsers too.

## RTVI data channel caveat (still applies)

Once ICE completes, the client opens the SCTP data channel and sends
RTVI messages (`describe-actions`, `start-bot`, `client-ready`, etc.).
The bundled `SmallWebRTCRequestHandler` registers the standard set
automatically. If you roll a custom handler, you must register
`app-message` and the relevant RTVI handlers — otherwise the client
times out on `start-bot` and you'll see:

```
WARNING  pipecat.transports.smallwebrtc.connection:timeout_handler
  Data channel not established within 10s after connection.
  Clearing message queue and disabling future queueing.
```

The warning is non-fatal (audio still flows), but the UI never advances
to the "ready" state. Inheriting `SmallWebRTCRequestHandler` (rather
than reimplementing it) avoids this.

## Source pointers

- The official `pipecat-ai/small-webrtc-prebuilt` GitHub repo (client
  source) is at https://github.com/pipecat-ai/small-webrtc-prebuilt.
  Read `client/src/index.tsx` for the four-transport switch and
  `startBotParams` shape.
- The pre-built client bundle is shipped in
  `pipecat_ai_prebuilt-1.0.3.dist-info/.../client/dist/`.
- `pipecat.runner.run:app` (line 168 = `app: FastAPI = FastAPI()`,
  line 572 = `@app.post("/start")`, line 749 = `app.mount("/client",
  PipecatPrebuiltUI)`, line 796 = `@app.post("/api/offer")`) is the
  canonical reference for endpoint ordering and shape. Lines may
  shift between pipecat versions, so re-verify against the actual
  installed source.
