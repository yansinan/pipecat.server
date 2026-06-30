# SmallWebRTC + PrebuiltUI Integration Debug Transcript

Session: 2026-06-30, ~4h debugging chain from broken "一直 loading" to full Client READY / Agent READY.

## Bug 1: Wrong TransportParams class

**Error**: `ImportError: cannot import name 'SmallWebRTCTransportParams' from pipecat.transports.smallwebrtc.transport`

**Root cause**: Class doesn't exist. Use `TransportParams` from `pipecat.transports.base_transport`.

**Fix**: `from pipecat.transports.base_transport import TransportParams`

## Bug 2: Wrong JS bundle in wheel

**Symptom**: PrebuiltUI loaded but Connect button produced `authenticating → disconnected` instantly. Server log: POST /start returned 200 but client never called /api/offer.

**Root cause**: `pipecat-ai-small-webrtc-prebuilt==2.5.0` contains a Daily-based client that doesn't talk to `/api/offer`.

**Fix**: `uv pip install pipecat-ai-prebuilt==1.0.3` (separate package from `pipecat-ai-small-webrtc-prebuilt`), switch import to `from pipecat_ai_prebuilt.frontend import PipecatPrebuiltUI`.

## Bug 3: /start returned wrong field name

**Symptom**: Client called /start but proceeded no further.

**Root cause**: Client expects `sessionId` (camelCase), my endpoint returned `pc_id` (snake_case).

**Also**: Client expects `iceConfig: { iceServers: [...] }` in the response to configure ICE servers.

**Fix**: Return `{"sessionId": ..., "iceConfig": {"iceServers": [... ]}}`.

## Bug 4: Client uses /sessions/{id}/api/offer path

**Symptom**: Server log showed `POST /sessions/{sessionId}/api/offer → 404 Not Found` while `POST /api/offer` was never called.

**Root cause**: SmallWebRTC transport in the client uses the sessionId from /start as a URL prefix.

**Fix**: Register both paths: `@app.post("/api/offer")` and `@app.post("/sessions/{session_id}/api/offer")`.

## Bug 5: pipeline runner blocked HTTP handler

**Symptom**: POST /api/offer timed out (curl hung for 5+ seconds). Client saw ICE `connecting → disconnected` after 10s.

**Root cause**: `on_connection` callback did `await runner.run()` which blocks until pipeline ends. The HTTP handler never returned the SDP answer.

**Fix**: Use `background_tasks.add_task(_run_pipeline, connection, session_id)` and return immediately.

## Bug 6: Missing RTVIProcessor + RTVIObserver

**Symptom**: Pipeline started but crash was logged: `RTVIProcessor found in pipeline but no RTVIObserver in observers`.

**Fix**: Add `observers=[RTVIObserver()]` to `PipelineWorker(...)`.

## Bug 7: build_pipeline keyword-only args

**Symptom**: `TypeError: build_pipeline() takes 0 positional arguments but 1 was given`.

**Root cause**: Function signature is `def build_pipeline(*, transport, ...)` — keyword-only.

**Fix**: `build_pipeline(transport=transport)`.

## Final verification

```
Browser status: Client: READY, Agent: READY
aiortc test: ICE connected + DC OPEN
Server: running for 200+ seconds
```
