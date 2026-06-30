---
name: pipecat-realtime-voice
description: Set up pipecat (1.4.0) real-time voice agent transports — SmallWebRTC + official PrebuiltUI, FastAPIWebsocketTransport + RawPCMSerializer, or Daily P2P. Covers transport wiring, ICE/STUN fallbacks for cross-NAT and Cloud browsers, PrebuiltUI mount order, CLI-side e2e verification via aiortc, data-channel warnings, browser autoplay policy, Whisper model preloading, WorkerRunner persistence, VAD→ASR→LLM→TTS pipeline analysis, EdgeTTS 24kHz ↔ Opus 48kHz resample mismatch, official example comparison (pipecat-examples/simple-chatbot), reasoning_content via OpenAILLMService subclass, RTVI observer auto-creation patterns, and DTLS pending-message flush workarounds.
version: 1.3.0
platforms: [linux]
metadata:
  hermes:
    tags: [pipecat, voice-agent, webrtc, websocket, fastapi, real-time, audio, opus, pcm, prebuilt-ui, aiortc, headroom, litellm, dtls, reasoning-content]
    related_skills: [pipecat-framework-setup, pipecat-voice-agent, pipecat-server-deployment, voice-ai-pipelines, service-unreachable-diagnosis, systematic-debugging]
---

# Pipecat Real-Time Voice Agent Transport Setup

## When to use this skill

Use when the user asks to:
- Add a **browser voice interface** to a pipecat agent (any transport: WebRTC, WebSocket, Daily)
- Wire **SmallWebRTC + PrebuiltUI** (the official React client in `pipecat-ai-prebuilt==1.0.3`)
- Wire **FastAPIWebsocketTransport + RawPCMSerializer** (simplest for LAN/same-machine testing)
- Debug **RTVI "Not Found"** errors, **WorkerRunner early exit**, **browser no-audio**, or **AudioContext suspended**

### 1. 官方 pipeline 结构（code-helper 示例 — 分离 STT + LLM + TTS 的规范参考）

`pipecat-examples/code-helper/server/bot.py:175-185` 是官方唯一使用**分离 STT + LLM + TTS**
的参考。其他官方例子的 LLM（GeminiLive 等）内置了 TTS/STT。

```python
pipeline = Pipeline([
    transport.input(),
    stt,                    # 独立 STT
    user_aggregator,
    llm,
    llm_text_processor,     # 官方 LLMTextProcessor
    tts,                    # 独立 TTS
    transport.output(),
    assistant_aggregator,
])
```

**关键特征**:
- **没有** `RTVIProcessor()` 在 pipeline 列表里
- **没有** 自定义 FrameProcessor
- `LLMTextProcessor` 放在 LLM 和 TTS 之间，把 `LLMTextFrame` 聚合为 `AggregatedTextFrame`
- `WorkerRunner` 负责创建 `RTVIProcessor` + `RTVIObserver`（通过 `worker.rtvi` 访问）
- 不要传 `observers=[RTVIObserver()]` — 让 PipelineWorker 自动创建连线 observer
  用 `rtvi_observer_params=RTVIObserverParams(...)` 自定义 observer 行为

**🚫 绝对不要**:
- 不要继承 `FrameProcessor` 造自定义处理器
- 不要在 `process_frame` 里 push `OutputTransportMessageFrame`
- 不要在 pipeline 列表加 `RTVIProcessor()`（WorkerRunner 内置）

## Transport decision table

| Transport | Latency | Complexity | Browser client | When to use |
|---|---|---|---|---|
| FastAPIWebsocket + RawPCMSerializer | ~1s | Low | Self-built HTML page | Same-machine or LAN testing; dev/debug |
| SmallWebRTC + PrebuiltUI | ~300ms | Medium | Official React SPA (RTVI v2) | Production browser clients |
| Daily (pipecat-ai[daily]) | ~200ms | Medium-High | Daily Prebuilt or custom | Cross-NAT / multi-user |

## FastAPIWebsocketTransport + RawPCMSerializer

### Wire format

- PCM 16-bit signed little-endian, 16000 Hz, 1 channel (mono)
- 20ms frames = 320 bytes/chunk (160 samples × 2 bytes)
- Inbound: raw binary WebSocket frames → `RawPCMSerializer.deserialize()` → `InputAudioRawFrame`
- Outbound: `OutputAudioRawFrame` → `RawPCMSerializer.serialize()` → raw binary WS frames

### Server (bot.py)

```python
from pipecat.transports.websocket.fastapi import (
    FastAPIWebsocketParams, FastAPIWebsocketTransport,
)
from src.serializers.pcm import RawPCMSerializer

transport = FastAPIWebsocketTransport(
    websocket=ws,
    params=FastAPIWebsocketParams(
        audio_in_enabled=True,
        audio_in_sample_rate=16000,
        audio_out_enabled=True,
        serializer=RawPCMSerializer(),
    ),
)
```

### Browser client (minimal test page)

Serve an inline HTML page with:
- `AudioContext` created in the button click handler (user gesture)
- `navigator.mediaDevices.getUserMedia(...)` for mic capture
- `AudioWorklet` converting Float32 → Int16 PCM, sent as 320-byte chunks
- Inbound PCM: `Int16Array → Float32Array → AudioContext.createBuffer → createBufferSource.start()`
- **Standalone test button** that sends pre-recorded PCM without mic (isolates server issues from mic issues)

Key JavaScript pattern for audio playback:
```javascript
// Created in user gesture (click handler):
ac = new AudioContext({sampleRate: 16000});
if (ac.state === 'suspended') ac.resume();

// In ws.onmessage (NOT a user gesture — must resume):
if (ac.state === 'suspended') ac.resume();
var b = ac.createBuffer(1, f32.length, 16000);
var s = ac.createBufferSource();
s.buffer = b;
s.connect(ac.destination);
s.start(ac.currentTime);
```

## SmallWebRTC + Vanilla JS client (pipecat-examples/simple-chatbot/client/javascript/)

The `pipecat-examples/simple-chatbot` repo ships a **vanilla JS client** that uses `@pipecat-ai/client-js` + `@pipecat-ai/small-webrtc-transport` directly — no React, no PrebuiltUI bundle.

### Protocol (reverse-engineered from small-webrtc-transport 1.10.3)

`startBotAndConnect({ endpoint, requestData })` does:

```
POST /start ← { createDailyRoom: false, enableDefaultIceServers: true, transport: "webrtc" }
Response MUST contain: { sessionId: "uuid" }

Derive offer URL: replace "/start" with "/sessions/{sessionId}/api/offer"
→ POST /sessions/{sessionId}/api/offer ← SDP Offer
Response: SDP Answer → PeerConnection established
```

**Key: `/start` only needs `{ sessionId }`** — no `connection_url` or `iceConfig` required for SmallWebRTC transport. The offer endpoint is auto-derived from the `/start` URL path.

The existing `server_prebuilt.py` endpoints are **directly compatible** — no code changes needed.

### Client files

```
client/javascript/
├── env.example          VITE_BOT_START_URL="http://localhost:7860/start"
├── index.html           Transport selector + video/conversation/events panels
├── src/config.js        Transport configs + createTransport() factory
├── src/app.js           VoiceChatClient class (connect, disconnect, mic, transcripts)
├── src/style.css        Dark theme
└── package.json         @pipecat-ai/client-js ^1.10.0, @pipecat-ai/small-webrtc-transport ^1.10.3
```

### Config.js patterns

```javascript
export const AVAILABLE_TRANSPORTS = ['daily', 'smallwebrtc'];
export const DEFAULT_TRANSPORT = 'smallwebrtc';  // change from 'daily'

const smallWebRTCConfig = {
  endpoint: "http://localhost:7860/start",
  requestData: {
    createDailyRoom: false,      // critical: no Daily room
    enableDefaultIceServers: true,
    transport: "webrtc",
  },
};
```

### Server re-use

The same `/start`, `/api/offer`, `/sessions/{id}/api/offer` endpoints serve both PrebuiltUI and the vanilla JS client. To strip PrebuiltUI:

1. Remove `app.mount("/client", PipecatPrebuiltUI)` + `pipecat-ai-prebuilt` dependency
2. Keep: `/start`, `/api/offer` (POST+PATCH), `/sessions/{session_id}/api/offer` (POST+PATCH)
3. Use `build_pipeline()` with local STT/TTS + LiteLLM

### CDP browser testing of the JS client

#### Two ways to select SmallWebRTC transport

**Method A — Keyboard via accessibility tree (recommended):**
```python
browser_navigate(url="http://localhost:5173/")
# snapshot shows:
#   combobox "Transport:" [expanded=false, ref=e1]: Daily
#   option "SmallWebRTC" [ref=e8]
browser_click(ref="@e1")          # open dropdown
browser_press(key="ArrowDown")    # select SmallWebRTC
browser_press(key="Enter")        # confirm
```
Then connect:
```python
browser_click(ref="@e3")          # Connect/Disconnect button
```

**Method B — JavaScript module import (fallback when DOMContentLoaded already fired):**
```python
# Import the module manually
browser_console(expression="import('/src/app.js').then(() => { document.getElementById('connect-btn').click(); })")
```

#### Critical: browser_console targets the DevTools page, not your app

**Symptom**: `browser_navigate` shows the correct page (snapshot shows your app's
UI), but `document.getElementById('transport-select')` returns null. 89 DOM
elements, only 4 have ids. The HTML served by Vite clearly has 8+ elements with
id=, yet the browser console finds none.

**Root cause**: The Hermes CDP browser opens Chrome with DevTools enabled. When
you navigate, TWO tabs are created: (1) your application page and (2) a hidden
DevTools page (`devtools://devtools/bundled/devtools_app.html?panel=elements`).
**The `browser_console` tool evaluates JavaScript in the DevTools page, not in
your application page.**

Evidence:
```python
browser_console(expression="location.href")
# Returns: "devtools://devtools/bundled/devtools_app.html?..."
```

The DevTools page's `document` has no elements from your application. All
`getElementById` calls return null. The `browser_navigate` snapshot works because
it targets the application tab via the accessibility tree (which captures the
correct page).

**Fix — close DevTools before using browser_console:**
```python
# 1. List all targets to find the DevTools tab
browser_cdp(method="Target.getTargets", params={})
# → targets show both your app page AND a devtools:// page

# 2. Close the DevTools tab
browser_cdp(method="Target.closeTarget", params={"targetId": "A96A6..."})
```

**WARNING**: Once you call `Target.closeTarget` or `Target.attachToTarget` via
`browser_cdp`, the `browser_console` tool **dies permanently for the rest of
the session**:
```
RuntimeError: CDP error on id=N: {'code': -32001, 'message': 'Session with given id not found.'}
```
`browser_navigate` and `browser_snapshot` still work, but console execution is
broken. The CDP supervisor's internal session mapping cannot be re-created.

**Standard workflow**: Do all browser_console work BEFORE any browser_cdp
Target method calls. If browser_console dies, you must navigate to a `javascript:`
URL or use `browser_cdp` with a fresh target (but Runtime.evaluate is often
unavailable via browser_cdp).

#### Fallback when browser_console is dead

```python
# Navigate to a javascript: URL to execute code
browser_navigate(url="javascript:console.log('test');void(0)")
# Then check browser_console output (won't work if session is dead)

# Better: build a standalone script instead
# See scripts/webrtc-test-client.py — reliable aiortc-based WebRTC test
uv run python3 <script>        # works regardless of CDP session state
```

#### Inject test audio after connection

```python
browser_click(ref="@e6")          # test audio button
```

#### What to monitor

```python
process(action="wait", session_id="...", timeout=15)
process(action="log", session_id="...", limit=200)
```

#### Known CDP limitations

When getElementById() returns null despite the element being visible in the
snapshot, the most likely cause is browser_console running on the DevTools
page (see above). A secondary cause: the HTML `<script type="module"
src="/src/app.js">` loaded but `DOMContentLoaded` already fired before
the CDP injected the script. The module's `window.addEventListener(
'DOMContentLoaded', ...)` listener never fires. Fix: use
`import('/src/app.js')` (dynamic import) which re-runs the module regardless.

## Pitfalls

- **Server port defaults to 7860** (not 8766). JS client env default matches.
- **`config.js` may have template-literal corruption** on `Authorization: ${...}` lines (renders as `*** ${...}`). Only triggers when `VITE_BOT_START_PUBLIC_API_KEY` is set.
- **CDP browser can't test WebRTC** — transport halts at `initializing` because no real mic/camera. Test in real Chrome/Firefox.
- **Old PrebuiltUI on :8766 and new server on :7860 can coexist** — kill the old one before expecting JS client to connect.
- **config.js DEFAULT_TRANSPORT** must be set to `'smallwebrtc'` — the auto-generated value `'daily'` causes the client to POST `{ createDailyRoom: true }` which the `/start` endpoint returns 200 but the Daily transport never initializes without a Daily room URL.

### Template

A minimal server compatible with the JS client is at `templates/bot-js-client-server.py`.

## SmallWebRTC + official PrebuiltUI

### Wiring (server)

⚠️ **Package warning**: `pipecat-ai-small-webrtc-prebuilt==2.5.0` ships a STALE
Daily-only client bundle. Use `pipecat-ai-prebuilt==1.0.3` instead. The bundles
look the same (both mount a `StaticFiles` at `/client`), but only the newer
package's JS calls `/start`, `/api/offer`, and `PipecatClient`. See
`references/prebuilt-package-decision.md` for the smoking-gun grep.

```python
from pipecat_ai_prebuilt.frontend import PipecatPrebuiltUI  # ✓ correct package
from pipecat.transports.base_transport import TransportParams
from pipecat.transports.smallwebrtc.connection import IceServer
from pipecat.transports.smallwebrtc.request_handler import (
    SmallWebRTCRequest, SmallWebRTCPatchRequest, SmallWebRTCRequestHandler,
)
from pipecat.transports.smallwebrtc.transport import SmallWebRTCTransport

app.mount("/client", PipecatPrebuiltUI)

@app.get("/", include_in_schema=False)
async def root():
    return RedirectResponse(url="/client/")


# ========================
# /start — called by client's startBot()
# ========================

@app.post("/start")
async def start_bot():
    """Must return sessionId (camelCase!) + iceConfig."""
    return {
        "sessionId": str(uuid.uuid4()),
        "iceConfig": {"iceServers": [{"urls": ["stun:stun.l.google.com:19302"]}]},
    }


# ========================
# /api/offer (two paths: direct and /sessions/{id}/...)
# ========================

async def _run_pipeline(connection, session_id):
    """Run pipeline in BACKGROUND via WorkerRunner — do NOT block the HTTP handler."""
    transport = SmallWebRTCTransport(
        webrtc_connection=connection,
        params=TransportParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
            audio_in_sample_rate=16000,
            audio_out_sample_rate=16000,
            video_in_enabled=False, video_out_enabled=False,
        ),
    )
    # build_pipeline uses keyword-only params — pass transport=transport
    worker, _ctx = build_pipeline(transport=transport)
    from pipecat.workers.runner import WorkerRunner
    runner = WorkerRunner()
    await runner.add_workers(worker)
    await runner.run(auto_end=False)


@app.post("/api/offer")
async def offer(request: SmallWebRTCRequest, background_tasks: BackgroundTasks):
    async def on_connection(connection):
        # ⭐ Must use background_tasks, NOT await directly
        background_tasks.add_task(_run_pipeline, connection, str(uuid.uuid4()))

    answer = await request_handler.handle_web_request(request, on_connection)
    return answer

@app.post("/sessions/{session_id}/api/offer")
async def offer_with_session(session_id: str, request: SmallWebRTCRequest, background_tasks: BackgroundTasks):
    async def on_connection(connection):
        background_tasks.add_task(_run_pipeline, connection, session_id)
    answer = await request_handler.handle_web_request(request, on_connection)
    return answer

# ---- ICE Candidates ----
# 浏览器推自己的 IP 地址列表，服务端直接调框架方法登记到 aiortc。
# 没有 session 的旧版 /api/offer 已删除（client 只走 /sessions/{id} 路径）。

@app.patch("/sessions/{session_id}/api/offer")
async def ice_candidate_session(session_id: str, request: SmallWebRTCPatchRequest):
    """接收 ICE candidates → 调用框架方法登记到 PeerConnection。"""
    return await webrtc_handler.handle_patch_request(request)
```

**Mount order matters:** define `@app.post("/api/offer")` BEFORE `app.mount("/client", ...)`. If the mount comes first, the static file fallback catches `/api/offer` and returns 405.

### ICE/STUN for cross-NAT

Cloud browser environments (Browserbase, remote Chromium) frequently cannot
reach `stun.l.google.com:19302`. Add fallbacks:

```python
SmallWebRTCRequestHandler(
    ice_servers=[
        IceServer(urls="stun:stun.l.google.com:19302"),
        IceServer(urls="stun:stun1.l.google.com:19302"),
        IceServer(urls="stun:stun2.l.google.com:19302"),
    ],
)
```

For LAN-only setups (test machines on the same network), drop the STUN
servers entirely — host candidates are enough:

```python
SmallWebRTCRequestHandler(ice_servers=[])
```

If the connection stays at `iceConnectionState = "completed"` but
`connectionState` never reaches `"connected"`, see
`pipecat-server-deployment` skill "DTLS handshake stalls in Cloud browser
environments" for the pending-message flush workaround.

## Core debugging principle

**Blame the evidence, not the environment.** When server-side tests pass but
the client-side fails, the instinct is to blame tool limitations (CDP sandbox,
browser restrictions, network proxies). Resist this. The correct response is:
add diagnostic logging to BOTH ends (server log + client console.log), isolate
the exact step where the paths diverge, and fix the code. Every "the
environment doesn't work" excuse is a missed bug in the implementation.

**Test fully before reporting.** Do not describe what you *think* is happening
or what *might* be the cause. Run the actual test, capture actual output (log
entries, HTTP status codes, frame types), and report concrete evidence. When
making a claim about data flow (e.g. "the pipeline splits 82KB into 640B
chunks"), cite the source code that does the splitting. If you cannot find the
code, do not make the claim.

**Self-verify every change with concrete evidence.** After modifying code,
restart the service and run the relevant test path end-to-end. Do NOT hand the
test back to the user with "check if it works now." The person who made the
change validates it — this is a hard rule, not a suggestion. Evidence means:
specific server log lines, curl output showing response data, or test script
output. Do not report paraphrased summaries of what "should" happen.

If the automated browser tools (CDP) are unreliable or the browser_console
session dies (e.g. after Target.closeTarget), build a standalone script-based
replacement (Python + aiortc for WebRTC, Node.js for WS) rather than asking
the user to open their real browser. The test must be rerunnable without
session state.

**Three-strikes rule on asking the user to test:**
1. First request: self-verify and report evidence.
2. Second request (same issue): find the root cause before asking.
3. Third request: you have lost credibility — stop asking, build a script,
   run it, capture output, report facts.

**Find evidence before claiming.** Every claim about data flow, frame routing,
pipeline behavior, or system internals must be backed by one of:
- Source code from the installed package (read_file with line numbers)
- Log output from a running test (specific log lines, not paraphrases)
- A runnable test command that demonstrates the behavior
- Official documentation or GitHub issues

If you cannot find the supporting evidence, do not make the claim. "I think",
"probably", "it might", or "the pipeline splits X into Y chunks" without code
citations are not acceptable. The correct response to uncertainty is: "I don't
know, let me check by [specific action]." Build a tight feedback loop that
tests the hypothesis directly.

**Resist the blame-the-tool reflex.** When server-side tests pass but client-
side fails, the instinct is to blame the CDP browser, the headless environment,
network proxies, or sandbox restrictions. Before going down that path: add
diagnostic logging to BOTH ends (server + client), isolate the exact step
where paths diverge, and fix the code. Three rounds of "the tool doesn't work"
without a code-level fix means you are working around a symptom, not a root
cause. Build a standalone script (Python+aiortc, Node.js+playwright) that does
not depend on the session-scoped browser tool — you will find most "tool bugs"
are actually code bugs in the application under test.

## Pitfalls

### WorkerRunner must use auto_end=False

When using `WorkerRunner` with a WebSocket transport, `runner.run()` defaults to
`auto_end=True`. This means the runner exits after all pending frames are
processed — including the initial `LLMRunFrame` that triggers the greeting.
The WebSocket connection then closes because the `conversation()` handler returns.

**Fix:**
```python
runner = WorkerRunner()
await runner.add_workers(worker)
await worker.queue_frames([LLMRunFrame()])
await runner.run(auto_end=False)  # ← keeps WS alive for user input
```

Without this, the browser connects, receives the greeting, and the WS drops
before the user can send any audio.

### Whisper model must be pre-loaded at startup

Whisper "small" (~950MB) takes **27 seconds** to load from disk on the first
pipeline invocation. During this time the WebSocket stays connected but no
greeting arrives. Users see "已连接(测试)" for 30s and assume it's broken.

**Fix — pre-load at module level:**
```python
# In bot.py, after load_dotenv():
from src.services.whisper_stt import WhisperSTTService
_ = WhisperSTTService(model_size=os.environ.get("WHISPER_MODEL_SIZE", "small"))
```

First-greeting latency drops from ~27s to ~8s after pre-loading.

### Kill zombie bot processes before starting

Multiple `python -m src.bot` processes can accumulate. The newer one fails to
bind port 8765, leaving the stale one serving. The stale one may lack the
latest fixes.

**Diagnosis:**
```bash
ss -tlnp | grep 8765   # who actually has the port
ps aux | grep "src.bot" # how many there are
```

**Fix — clean kill:**
```bash
pkill -f "python -m src.bot"
pkill -9 -f "python3 -m src.bot"  # orphaned children
sleep 2
ss -tlnp | grep 8765 || echo "PORT FREE"
```

Then verify only ONE process remains after restart.

### Prebuilt UI RTVI mismatch

Without `auto_end=False`, the WorkerRunner exits after the first `LLMRunFrame`,
closing the WebSocket. The client speaks into a dead connection.

```python
await runner.run(auto_end=False)       # ← required
```

### 2. Pre-load Whisper model at startup (avoids 27s first-connection delay)

Whisper "small" (~950MB) loads from disk on first pipeline start. When this
happens inside the WS handler, the client sees a 27-second gap between WS
open and greeting. Fix by loading at module level:

```python
load_dotenv(override=True)
for env_var in ["LLM_MODEL", "LLM_BASE_URL", "WHISPER_MODEL_SIZE", "TTS_VOICE"]:
    os.environ.setdefault(env_var, default_map[env_var])

from src.services.whisper_stt import WhisperSTTService
_ = WhisperSTTService(model_size=os.environ.get("WHISPER_MODEL_SIZE", "small"))
```

### 3. Browser AudioContext autoplay policy

| Timing | Action | Why |
|---|---|---|
| Button click | `ac = new AudioContext(...); if(ac.state=='suspended') ac.resume();` | User gesture → allowed |
| WS onmessage | `if(ac.state=='suspended') ac.resume();` then play | Async callback → browser may suspend |

The `ws.onmessage` handler runs in a DIFFERENT execution context from the
user's click. Even if the `AudioContext` was created in the click handler,
playing audio from an async callback can trigger the autoplay policy.
Always resume before using.

### 4. Kill zombie bot processes before restart

Stale processes from prior sessions keep port 8765. New instance fails to
bind. Always clean before restart:

```bash
pkill -9 -f "python -m src.bot" 2>/dev/null
pkill -9 -f "uv run.*src.bot" 2>/dev/null
sleep 2
ss -tlnp | grep 8765 || echo "PORT FREE"
```

Two-shot (TERM then KILL) because `uv run` wraps the real Python process.

### 5. SmallWebRTC data-channel RTVI "Not Found"

- **Not** a fatal error despite `fatal: true` in the client log.
- RTVI v2 protocol messages on the data channel need `pipecat-ai-rtvi-bot-client`.
- Without RTVI framework: use FastAPIWebsocketTransport instead.

### 6. `SmallWebRTCTransportParams` does not exist in pipecat 1.4.0

- Real class: `pipecat.transports.base_transport.TransportParams`
- Always grep the wheel before believing class names from blog posts.

### 7. App mount order

- Define API routes (`/api/offer`) BEFORE `app.mount("/client", ...)`.
- Starlette's mount catches everything under the prefix; routes registered
  before the mount take priority.

### 8. ICE server config

- Add `stun:stun.l.google.com:19302` minimum; for symmetric NAT you need TURN.

### 9. "Data channel not established within 10s" is NOT fatal

- RTVI control messages use SCTP data channel; warning is informational.
- Audio track still flows (verify with `pc.getReceivers()` showing track=audio).

### 10. on_connection MUST NOT block — use background_tasks

**Symptom**: `/api/offer` POST hangs forever (HTTP timeout), pipeline crashes
with `web_rtc_connection_callback failed`.

**Root cause**: the `on_connection` callback runs inside the FastAPI request
handler. Any `await` inside it (pipeline setup, WorkerRunner.run()) blocks
the HTTP response.

**Fix**: use `background_tasks.add_task()`:
```python
async def on_connection(connection):
    background_tasks.add_task(_run_pipeline, connection, session_id)

answer = await webrtc_handler.handle_web_request(request, on_connection)
return answer  # Returns immediately, pipeline runs in background
```

### 11. build_pipeline uses keyword-only signature

**Symptom**: `TypeError: build_pipeline() takes 0 positional arguments but 1 was given`

**Root cause**: `def build_pipeline(*, transport: BaseTransport, ...)` uses
keyword-only args (bare `*`).

**Fix**: call with `transport=transport` keyword.

### 12. /start must return sessionId + iceConfig (camelCase)

**Symptom**: `/start` returns `{"pc_id": "..."}`, client stops at
"authenticating" and never calls `/api/offer`.

**Root cause**: PrebuiltUI client's `startBot()` reads `sessionId` (camelCase)
and `iceConfig.iceServers`. Missing these means client never proceeds.

**Fix**:
```python
@app.post("/start")
async def start_bot():
    return {
        "sessionId": str(uuid.uuid4()),
        "iceConfig": {"iceServers": [{"urls": ["stun:stun.l.google.com:19302"]}]},
    }
```

### 13. Client uses /sessions/{id}/api/offer — register BOTH paths

**Symptom**: Client shows "connecting" but server log shows
`POST /sessions/.../api/offer 404`.

**Root cause**: SmallWebRTCTransport constructs offer URL as
`/sessions/${sessionId}/api/offer`.

**Fix**: register both `/api/offer` and `/sessions/{session_id}/api/offer`
for POST (offer) and PATCH (ICE candidates).

### 14. RTVIProcessor 不要放 pipeline 列表里 — WorkerRunner 内置

**Symptom**: pipeline 卡住、自定义 FrameProcessor 收不到帧、`RTVIProcessor: Error processing frame`。

**根因**: 官方 pipecat-examples（`small-webrtc-prebuilt/test/bot.py`、`pipecat-examples/simple-chatbot/server/bot-openai.py`）的 pipeline 列表**不包含 RTVIProcessor**。它被 `WorkerRunner` 自动集成到 `PipelineWorker`，通过 `worker.rtvi` 访问。手动添加会导致 RTVIProcessor 被 pipeline 当做普通处理器路由帧，破坏数据流。

**官方 pipeline 结构** (bot.py:106-113):
```python
pipeline = [
    transport.input(),
    user_aggregator,
    llm,
    transport.output(),
    assistant_aggregator,
]
```

**正确用法 — 通过 `worker.rtvi.event_handler` 访问 RTVI:**
```python
worker = PipelineWorker(pipeline, observers=[RTVIObserver()], ...)

@worker.rtvi.event_handler("on_client_ready")
async def on_client_ready(rtvi):
    await worker.queue_frames([LLMRunFrame()])
```

**不要做**:
```python
# ❌ 不要把 RTVIProcessor() 加进 processors 列表
processors = [transport.input(), RTVIProcessor(), ...]
```

### 15. OpenAILLMService model parameter deprecated

**Symptom**: `DeprecationWarning: The 'model' parameter is deprecated. Use 'settings=OpenAILLMService.Settings(model=...)' instead.`

**Root cause**: Passing `model` and `temperature`/`max_tokens` as top-level kwargs to `OpenAILLMService()`.

**Fix — use Settings dataclass:**
```python
# ❌ Deprecated:
llm = OpenAILLMService(
    base_url=...,
    api_key=...,
    model="deepseek-v4-flash",      # ← deprecated
    temperature=0.7,                # ← deprecated
    max_tokens=512,                 # ← deprecated
)

# ✅ Correct:
from pipecat.services.openai.llm import OpenAILLMService

llm = OpenAILLMService(
    base_url=...,
    api_key=...,
    settings=OpenAILLMService.Settings(
        model="deepseek-v4-flash",
        temperature=0.7,
        max_tokens=512,
    ),
)
```

`OpenAILLMService.Settings` is an alias for `BaseOpenAILLMService.Settings` / `OpenAILLMSettings`. Fields include: `model`, `temperature`, `top_p`, `max_tokens`, `max_completion_tokens`, `frequency_penalty`, `presence_penalty`, `seed`, `system_instruction`. Defaults for temperature/max_tokens/etc are `NOT_GIVEN` (openai sentinel), not a numeric default — so if you set only `model` in Settings, temperature stays as the upstream API's default.

### 16. Custom TTSSettings MUST set model and language to None

**Symptom**: `EdgeTTSSettings: the following fields are NOT_GIVEN: model, language`

**Root cause**: `TTSSettings` (from `pipecat.services.settings`) declares `model` and `language` as required fields. Custom settings classes that omit them inherit `NOT_GIVEN`, triggering Pipecat's validation.

**Fix — add explicit None defaults:**
```python
@dataclass
class EdgeTTSSettings(TTSSettings):
    model: str | None = None
    language: str | None = None
    voice: str = "zh-CN-XiaoxiaoNeural"
    # ... custom fields
```

(Pyright may report `reportIncompatibleVariableOverride` because the base type is `str | Language | _NotGiven | None` — this is a harmless type variance warning, not a runtime error.)

### 17. DeepSeek models through LiteLLM — reasoning_content bug

**Symptom**: LLM runs (completion tokens counted in metrics) but `[BotText]` or `[Frame:after-llm]` shows NO `LLMTextFrame`, `LLMFullResponseStartFrame`, or `LLMFullResponseEndFrame`. No TTS output. Logs show the API call succeeded but text never reaches downstream processors.

**Root cause**: LiteLLM/Headroom proxy transforms the API response. DeepSeek models (v3, v4-flash, R1) output their text in `delta.reasoning_content` instead of `delta.content`. Pipecat's `BaseOpenAILLMService._process_context()` ONLY checks `chunk.choices[0].delta.content`, so all text is silently dropped.

**Diagnosis** — curl to confirm:
```bash
curl -s -X POST "http://your-litellm-url/chat/completions" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $KEY" \
  -d '{"model":"deepseek-v4-flash","messages":[{"role":"user","content":"hi"}],"stream":true,"max_tokens":50}' \
  | grep -o '"delta":{[^}]*}'
```
If every chunk uses `reasoning_content` and never `content`, you have this bug.

**Fix — three options, pick one:**

#### Option A (cleanest): subclass `OpenAILLMService` and override `get_chat_completions`

This is what `pipecat.services.nvidia.llm.NvidiaLLMService` does internally.
Make it your default for Headroom/LiteLLM-backed DeepSeek models:

```python
from collections.abc import AsyncIterator
from openai.types.chat import ChatCompletionChunk
from pipecat.frames.frames import LLMThoughtStartFrame, LLMThoughtTextFrame, LLMThoughtEndFrame
from pipecat.services.openai.llm import OpenAILLMService


class HeadroomLLMService(OpenAILLMService):
    """OpenAILLMService subclass that handles `reasoning_content` as the
    actual content stream. Emits LLMThought*Frame so the reasoning shows up
    in RTVI's thought panel but doesn't go to TTS.
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._has_reasoning = False

    async def get_chat_completions(self, context):
        stream = await super().get_chat_completions(context)
        return self._handle_reasoning(stream)

    async def _handle_reasoning(self, stream):
        try:
            async for chunk in stream:
                if chunk.choices and chunk.choices[0].delta:
                    delta = chunk.choices[0].delta
                    rc = getattr(delta, "reasoning_content", None) \
                        or getattr(delta, "reasoning", None)
                    if rc:
                        if not self._has_reasoning:
                            self._has_reasoning = True
                            await self.push_frame(LLMThoughtStartFrame())
                        await self.push_frame(LLMThoughtTextFrame(text=rc))
                    elif self._has_reasoning and delta.content:
                        await self.push_frame(LLMThoughtEndFrame())
                        self._has_reasoning = False
                yield chunk
        finally:
            if self._has_reasoning:
                await self.push_frame(LLMThoughtEndFrame())
```

Usage:
```python
llm = HeadroomLLMService(
    base_url=LLM_BASE_URL,
    api_key=LLM_API_KEY,
    settings=OpenAILLMService.Settings(model=LLM_MODEL, ...),
)
```

**Why Option A is best**:
- Lives in your code (`src/services/llm.py`), not venv site-packages
- Survives `uv sync` / pip reinstall
- Emits `LLMThought*Frame` so reasoning shows in PrebuiltUI's thought panel
- Falls back to `delta.content` for non-reasoning models — drop-in replacement

#### Option B: monkey-patch venv site-packages

One-time per environment. Three patches applied to `openai/types/chat/chat_completion_chunk.py`
and `pipecat/services/openai/base_llm.py`. Full diff in
`references/deepseek-litellm-reasoning-content.md`. **Risk**: lost on `uv sync` —
wrap in a script and re-run after every venv rebuild.

#### Option C: configure the LiteLLM proxy to merge fields

Serverhome side. No pipecat changes. Most robust long-term. Ask the proxy
admin to add a response transform: copy `reasoning_content` into `content`
before returning to the client.

**Caveat (Options A and B)**: If the model emits BOTH `reasoning_content`
(thinking) and `content` (final answer), the reasoning reaches TTS and the
bot speaks its internal monologue. For DeepSeek-via-LiteLLM through Headroom,
this is usually not a problem because the proxy merges the fields — verify
with a curl trace first.

**Models verified affected**: `deepseek-v4-flash`, `minimax` (and other
DeepSeek family served through LiteLLM/Headroom proxy).

### 18. DebugFrameProcessor — trace data flow through pipeline

**Symptom**: Bot doesn't speak, transcripts don't appear, audio isn't played. You need to know WHAT frames reach which processor.

**Fix**: Insert a generic FrameProcessor that logs every frame by type:

```python
from loguru import logger
from pipecat.frames.frames import Frame
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

class DebugFrameProcessor(FrameProcessor):
    """Log every frame type that passes through."""

    def __init__(self, name: str = "debug"):
        super().__init__()
        self._name = name
        self._FrameProcessor__started = True  # name-mangled for pipecat

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        frame_type = type(frame).__name__
        text = ""
        if hasattr(frame, "text") and frame.text:
            text = f" text={frame.text[:80]}"
        elif hasattr(frame, "audio") and frame.audio:
            text = f" audio={len(frame.audio)}B"
        logger.debug(f"[Frame:{self._name}] {frame_type}{text}")
        await self.push_frame(frame, direction)

# In pipeline:
processors = [
    transport.input(), ...,
    llm,
    DebugFrameProcessor(name="after-llm"),     # ← see what LLM outputs
    BotTextProcessor(),
    tts,
    DebugFrameProcessor(name="after-tts"),     # ← see what TTS outputs
    transport.output(), ...
]
```

**Key frame types to watch for**:
| Frame type | Meaning | Expected after |
|---|---|---|
| `LLMTextFrame` | LLM generated text response | LLM |
| `TTSTextFrame` | Text sent to TTS engine | BotTextProcessor (forward) |
| `TTSStartedFrame` | TTS began synthesizing | TTS |
| `TTSAudioRawFrame` | TTS produced audio data | TTS |
| `TTSStoppedFrame` | TTS finished | TTS |
| `OutputAudioRawFrame` | Audio sent to transport output | transport.output() |
| `OutputTransportMessageFrame` | Text sent to client via data channel | BotTextProcessor (upstream) |
| `TranscriptionFrame`/`InterimTranscriptionFrame` | STT recognized speech | STT |

If you see `LLMTextFrame` at `after-llm` but no `TTSTextFrame` at `after-tts`, the BotTextProcessor isn't forwarding. If you see `TTSTextFrame` at `after-tts` but no `TTSAudioRawFrame`, the TTS is failing. If you see `TTSAudioRawFrame` but no `OutputAudioRawFrame`, the output transport is the problem.

### 25. Only keep ONE route when session/non-session are identical

**Symptom**: Bare `/api/offer` and `/sessions/{id}/api/offer` both registered for
POST (SDP Offer) and PATCH (ICE Candidates) — four endpoints doing the same thing.

**Root cause**: The older pipecat protocol used `/api/offer` without session_id.
The newer SmallWebRTC client (`@pipecat-ai/small-webrtc-transport`) constructs
`/sessions/{sessionId}/api/offer` from the `/start` response and never calls the
bare path. The old endpoints only exist for backward compatibility with pre-1.10
clients.

**Fix — delete the bare versions, keep only `/sessions/{id}`**:

```python
# ❌ Four endpoints, half never called:
@app.post("/api/offer")                     # dead
@app.post("/sessions/{session_id}/api/offer")  # live
@app.patch("/api/offer")                    # dead
@app.patch("/sessions/{session_id}/api/offer") # live

# ✅ Two endpoints — both live:
@app.post("/sessions/{session_id}/api/offer")
async def offer_session(...):
    ...

@app.patch("/sessions/{session_id}/api/offer")
async def ice_candidate_session(...):
    return await webrtc_handler.handle_patch_request(request)
```

**Verification**: grep the client library (`@pipecat-ai/small-webrtc-transport`)
for the URL construction pattern — it replaces `/start` with `/sessions/{id}/api/offer`.
No bare-path references. If the client ever needs the old path, you'll see 404s
in the server log.

### 28. Dynamic host redirect — never hardcode localhost for cross-machine access

**Symptom**: `GET /` redirects to `http://localhost:5173/`. From helix
(Tailscale IP `100.66.66.102`), the browser interprets `localhost` as the
CLIENT machine (helix), not the SERVER (x1tablet). User gets connection refused.

**Fix — use `request.url.hostname`**:

```python
from fastapi import Request

@app.get("/")
async def index(request: Request):
    redirect_url = f"{request.url.scheme}://{request.url.hostname}:5173/"
    return RedirectResponse(url=redirect_url)
```

Now:
- `http://localhost:7860/` → `http://localhost:5173/` ✅
- `http://100.66.66.249:7860/` → `http://100.66.66.249:5173/` ✅
- `http://192.168.1.249:7860/` → `http://192.168.1.249:5173/` ✅

### 29. Check framework code before writing custom implementations

**Symptom**: You write a custom `_handle_ice_patch()` that adds ICE candidates
to the PeerConnection — 19 lines that exactly duplicate
`SmallWebRTCRequestHandler.handle_patch_request()`. Or you write a
`BotTextProcessor` that forwards `TTSTextFrame` — but `LLMTextProcessor`
from the framework already does this.

**Discipline**: Before writing any FrameProcessor, service wrapper, or HTTP
handler, search the framework for existing implementations:

```bash
# Search the installed pipecat package:
grep -r "def handle_patch_request" ~/workspace/pipecat/.venv/lib/python3.11/site-packages/pipecat/
grep -r "class.*FrameProcessor" ~/workspace/pipecat/.venv/lib/python3.11/site-packages/pipecat/
grep -r "def process_frame" ~/workspace/pipecat/.venv/lib/python3.11/site-packages/pipecat/ | head -20
```

**Known framework-provided processors (check these first)**:

| What you want | Framework class | Location |
|---|---|---|
| ICE candidate handler | `SmallWebRTCRequestHandler.handle_patch_request()` | `transports/smallwebrtc/request_handler.py` |
| LLM text → AggregatedTextFrame | `LLMTextProcessor` | `processors/aggregators/llm_text_processor.py` |
| LLM context assembly | `LLMContextAggregatorPair` | `processors/aggregators/llm_response_universal.py` |
| RTVI observer + messages | `RTVIObserver` | `processors/frameworks/rtvi/observer.py` |
| SDP Offer handling | `SmallWebRTCRequestHandler.handle_web_request()` | `transports/smallwebrtc/request_handler.py` |

### 30. Fail loud on missing dependencies — never silent-degrade

**Symptom**: Test audio generator falls back to a 0.3s 220Hz sine wave when
edge-tts or ffmpeg is missing. VAD can't detect speech from a tone → no STT →
no LLM → user thinks the pipeline is broken. The fallback *silently* masks the
real problem: the TTS tools aren't installed.

**Fix — raise immediately**:

```python
def get_test_audio() -> bytes:
    if not PCM_PATH.exists():
        try:
            _ensure_true_tts()  # edge-tts + ffmpeg
        except (ImportError, FileNotFoundError) as e:
            raise FileNotFoundError(
                f"Cannot generate test audio: {e}\n"
                f"Install: pip install edge-tts && apt install ffmpeg"
            )
```

**Rule**: A missing dependency should fail at the first call, not silently
produce unusable data. The error message tells the user exactly what to install
and why. No sine waves, no empty stubs, no degraded behavior — either the full
feature works or it raises with a clear diagnosis.

**Symptom**: You need to inject test audio into the pipeline, so you maintain a
global `_active_inbounds: dict[str, transport.input()]` with `register()` /
`unregister()` calls. This duplicates what the framework already tracks.

**Fix — stash a lightweight attribute on the `SmallWebRTCConnection`**:

The framework's `request_handler._pcs_map` (`pc_id → SmallWebRTCConnection`)
already tracks all active connections. Your pipeline startup runs inside
`_run_pipeline(connection, session_id)` where `connection` is the exact object
stored in `_pcs_map`. Attach your injection target directly:

```python
# In _run_pipeline:
connection._inject_inbound = transport.input()   # ← light attribute, GC'd with connection

# In inject endpoint:
keys = list(webrtc_handler._pcs_map.keys())
conn = webrtc_handler._pcs_map[keys[-1]]
inbound = getattr(conn, "_inject_inbound", None)
```

**Why this works**: Python lets you attach arbitrary attributes to any object.
The `SmallWebRTCConnection` has the same lifecycle as the pipeline — when the
connection is GC'd (connection drops), the injected attribute goes with it.
No separate registry, no `register()`/`unregister()` calls, no `WeakValueDictionary`.

**Keep the registry lookup in the injector class** by passing a reference to
the handler's `_pcs_map`:

```python
# test_audio.py
class TestAudioInjector:
    def set_handler(self, handler):
        self._pcs_map = handler._pcs_map  # just a reference, no lifecycle mgmt

    async def inject_latest(self):
        keys = list(self._pcs_map.keys())
        conn = self._pcs_map[keys[-1]]
        inbound = getattr(conn, "_inject_inbound", None)
        ...
```

The injector never creates or destroys state — it only reads from the
framework's existing tracking. Deletion = remove the module + 2 call sites.

### 27. Don't add empty stub functions for deleted code — delete the callers too

**Symptom**: After removing custom event processors, `/events` endpoint imports `pop_events` that no longer exists. Adding `def pop_events(): return []` stub in pipeline.py keeps the endpoint alive but returns useless data.

**Fix — delete the dead endpoint AND its polling JS code:**
- Delete `@app.get("/events")` endpoint
- Delete `setInterval` polling in `app.js`
- Delete the stub function (if any)
- Do NOT add empty functions to keep dead imports alive — kill the import site too

**Rationale**: Every polling request costs tokens (server response, JS parse). An always-empty endpoint generates infinite no-op requests. The Events panel still shows connection events from the JS client's own `addEvent()`.

### 26. Test audio injection — isolate mic issues from pipeline issues

**Symptom**: Client connects, no audio/transcript appears. Is the microphone broken, or is the pipeline (STT → LLM → TTS) broken?

**Fix**: Inject test audio directly into the pipeline via an HTTP endpoint. This bypasses the browser's mic entirely.

**CRITICAL — use `_audio_in_queue` not `push_frame`**: When you push audio via
`inbound.push_frame(InputAudioRawFrame(...))`, the frame goes through
`FrameProcessor.__internal_push_frame()` → `self._next.queue_frame()` — this
adds it to the PIPELINE queue (RTVIProcessor → ...), which may be backed up
by LLM response frames. The LLM greeting takes 5-15s to complete, and audio
pushed during that time sits in the queue until after the greeting finishes,
by which point VAD has already timed out.

**Correct injection path — use `_audio_in_queue`** (same queue as real WebRTC
audio frames, processed by `_audio_task_handler` which is independent of the
pipeline queue):

```python
async def inject_test_audio_into_session(inbound, data: bytes):
    """Inject PCM audio via _audio_in_queue (bypasses pipeline backpressure)."""
    chunk_size = 640
    for i in range(0, len(data), chunk_size):
        chunk = data[i:i + chunk_size]
        frame = InputAudioRawFrame(audio=chunk, sample_rate=16000, num_channels=1)
        if hasattr(inbound, "_audio_in_queue") and inbound._audio_in_queue:
            await inbound._audio_in_queue.put(frame)  # bypass pipeline queue
        else:
            await inbound.push_frame(frame)            # fallback
```

**USE REAL TTS AUDIO, NOT A SINE WAVE.** VAD (SileroVADAnalyzer) needs actual
speech energy (`max_amp > 5000`) to detect speech. A 0.3s 220Hz sine wave at
amp=300 is effectively silent — no speech detected, no STT triggered, no LLM
response. Generate a proper 2-3s Chinese TTS audio file:

```python
from edge_tts import Communicate
import asyncio, subprocess

async def gen():
    await Communicate("你好，今天天气不错。", voice="zh-CN-XiaoxiaoNeural").save("/tmp/test.mp3")
    subprocess.run([
        "ffmpeg", "-y", "-i", "/tmp/test.mp3",
        "-acodec", "pcm_s16le", "-f", "s16le",
        "-ac", "1", "-ar", "16000",
        "/tmp/test_speech.pcm",
    ], check=True, capture_output=True)
asyncio.run(gen())
# Verify: struct.unpack('<h' * (filesize//2), data) → max abs(sample) > 5000
```

**Module isolation pattern**: Extract test-audio logic into a standalone
`src/test_audio.py` exporting `ensure_test_audio()` (generate/cache),
`inject_into_session(inbound)` (inject via `_audio_in_queue`), and
`send_event(inbound, message)` (app-message to Events panel). Production
code imports the module; removing test infra = one `rm` + 3 endpoints.

**Server-side pattern — store pipeline input reference:**

```python
# Global dict to reference active pipelines
_active_inbounds: dict[str, object] = {}

async def _run_pipeline(connection, session_id):
    transport = SmallWebRTCTransport(...)
    # ...
    # Register input for test injection
    _active_inbounds[session_id] = transport.input()
    try:
        await runner.run(auto_end=False)
    finally:
        _active_inbounds.pop(session_id, None)


@app.get("/test.pcm")
async def serve_test_audio():
    """Return a synthetic test tone (1.5s, 220Hz→sweep, 16kHz 16-bit mono PCM)."""
    import math, struct
    sr = 16000
    buf = bytearray()
    for i in range(int(sr * 0.3)):
        buf += struct.pack("<h", int(math.sin(2 * math.pi * 220 * i / sr) * 300))
    for i in range(int(sr * 0.8)):
        f = 280 + 500 * math.sin(2 * math.pi * 2 * i / sr)
        buf += struct.pack("<h", int(math.sin(2 * math.pi * f * i / sr) * (14000 + 2000 * math.sin(2 * math.pi * 3 * i / sr))))
    for i in range(int(sr * 0.4)):
        buf += struct.pack("<h", 0)
    return Response(content=bytes(buf), media_type="application/octet-stream")


@app.post("/inject_test_audio")
async def inject_test_audio():
    """Push test audio into the most recently created active pipeline."""
    if not _active_inbounds:
        return {"error": "no active sessions"}
    session_id = list(_active_inbounds.keys())[-1]
    inbound = _active_inbounds[session_id]
    data = _get_test_audio()  # cache the generated bytes
    from pipecat.frames.frames import InputAudioRawFrame
    frame = InputAudioRawFrame(audio=data, sample_rate=16000, num_channels=1)
    await inbound.push_frame(frame)
    return {"status": "ok", "bytes": len(data)}
```

**Client-side — floating test button (app.js) — fix: use `VITE_BOT_START_URL` not hardcoded localhost:**

```diff
- const resp = await fetch('/inject_test_audio', { method: 'POST' });
+ const botBaseUrl = (import.meta.env.VITE_BOT_START_URL || 'http://localhost:7860/start').replace('/start', '');
+ const resp = await fetch(`${botBaseUrl}/inject_test_audio`, { method: 'POST' });
```

From helix (or any other machine), `localhost` points to the CLIENT machine, not the server.
Always derive the base URL from `VITE_BOT_START_URL` (which contains the Tailscale IP).

```javascript
const testBtn = document.getElementById('test-audio-btn');
if (testBtn) {
  testBtn.addEventListener('click', async () => {
    if (!this.isConnected) return;
    testBtn.disabled = true;
    try {
      const resp = await fetch('/inject_test_audio', { method: 'POST' });
      const result = await resp.json();
      this.addEvent('test-audio', `Sent ${result.bytes}B test audio`);
    } catch (err) {
      this.addEvent('error', `Test audio failed: ${err.message}`);
    } finally {
      testBtn.disabled = false;
    }
  });
}
```

Add a floating button in `index.html`:
```html
<button id="test-audio-btn" class="test-audio-btn">🎧 测试语音</button>
```

With CSS:
```css
.test-audio-btn {
  position: fixed; bottom: 24px; right: 24px; z-index: 1000;
  padding: 12px 20px; border-radius: 50px;
  background: linear-gradient(135deg, #f59e0b, #d97706);
  color: #000; font-weight: 600; cursor: pointer;
  box-shadow: 0 4px 15px rgba(245, 158, 11, 0.4);
}
```

**What to look for in server logs after clicking the button:**

| Log output | Meaning | Fix |
|---|---|---|
| `[Frame:after-llm] LLMTextFrame text=...` followed by `[Frame:after-tts] TTSAudioRawFrame audio=640B` | Pipeline healthy — mic is the problem | Check browser mic permissions, mic device selection |
| `[Frame:after-llm] LLMTextFrame text=...` but NO `[Frame:after-tts]` | TTS failing | Check EdgeTTS/your TTS service logs, ffmpeg availability |
| NO `[Frame:after-llm]` at all | LLM not responding | Check `LLM_BASE_URL`, API key, model name |
| `InputAudioRawFrame` logged but no `TranscriptionFrame` | STT (Whisper) failing | Check Whisper model loading, ffmpeg, audio format |

**Critical limitation — injected audio is chunked into 20ms frames by the pipeline**: When you push a single `InputAudioRawFrame` with 82KB of speech audio via `inbound.push_frame()`, the pipeline passes it through. But the `STTService.process_audio_frame()` calls `run_stt(frame.audio)` on EVERY individual frame — the 82KB frame is treated as one atomic chunk and transcribed correctly if the audio is well-formed. However, if the transport or VAD layer chunks the audio (as happens with real WebRTC streams), each 640B (~20ms) chunk is sent to Whisper independently. Whisper with `vad_filter=True` will:
- Filter out 20ms chunks as non-speech (too short)
- Return no transcription

**Why direct whisper test works but the pipeline doesn't**: Calling `WhisperSTTService.run_stt(full_audio_82KB)` directly works perfectly (verified: returns `"你好，今天天气不错。"`). But going through the pipeline, the STT service processes each received `InputAudioRawFrame` individually without accumulation. Real microphone streams are handled differently — the WebRTC transport's audio reception loop chunks the stream into small frames, but the STT service relies on VAD signals from the downstream `LLMUserAggregator` to know when to accumulate.

**Workaround for testing STT in the pipeline**: Instead of injecting through the transport `input()`, push directly to the STT service:
```python
stt_service = _active_stt_services.get(session_id)
if stt_service:
    frame = InputAudioRawFrame(audio=full_pcm, sample_rate=16000, num_channels=1)
    await stt_service.process_frame(frame, FrameDirection.DOWNSTREAM)
```

**Better approach — test STT standalone**:
```bash
cd ~/workspace/pipecat
PATH="$HOME/.hermes/bin:$PATH" uv run python3 -c "
import asyncio
from src.services.whisper_stt import WhisperSTTService
stt = WhisperSTTService(model_size='small')
with open('/tmp/test_speech.pcm', 'rb') as f: audio = f.read()
async def test():
    async for frame in stt.run_stt(audio):
        if frame and hasattr(frame, 'text'):
            print(f'STT: {frame.text}')
asyncio.run(test())
"
```

This bypasses the entire pipeline and confirms STT works. If it does, the issue is in the pipeline integration (VAD triggering, audio accumulation, frame routing), not Whisper itself.

**Symptom**: Vite dev server (`:5173`) POSTs to Python server (`:7860`). Browser shows OPTIONS preflight with `405 Method Not Allowed`. Client never connects.

**Root cause**: Browsers enforce CORS for cross-origin POST requests. Different ports count as different origins.

**Fix**:
```python
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],       # wide open for development
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
```

This must be added BEFORE any route decorators (FastAPI processes middleware by registration order).

### 20. STT audio accumulation — WhisperSTTService must extend SegmentedSTTService

**Symptom**: `[WHISPER-STT] run_stt called with 640B audio` logged hundreds of times per
second, but NO `TranscriptionFrame` or `InterimTranscriptionFrame` ever appears.
The pipeline receives audio (VAD detects speech start/stop) but Whisper never outputs
transcribed text.

**Root cause**: `WhisperSTTService` (and any custom STT class) must extend
`SegmentedSTTService` (not `STTService`) for audio accumulation to work in the
SmallWebRTC pipeline. Here is the critical chain:

1. WebRTC audio arrives as 640B chunks (~20ms @ 16kHz) — each chunk is an
   `InputAudioRawFrame` processed individually by the pipeline.
2. `STTService.process_audio_frame()` calls `run_stt(frame.audio)` on EVERY frame —
   each call gets 640B (20ms) of audio. Whisper with `vad_filter=True` filters these
   out as non-speech because 20ms is far too short.
3. `SegmentedSTTService.process_audio_frame()` accumulates audio into
   `self._audio_buffer` and does NOT call `run_stt` on individual frames. Instead it
   waits for `VADUserStoppedSpeakingFrame` which triggers
   `_handle_user_stopped_speaking()` → writes the full buffer to a WAV → calls
   `run_stt(full_audio)` with all accumulated audio at once.

**Architectural issue**: VAD is configured in `LLMUserAggregatorParams(vad_analyzer=...)`
which is placed AFTER STT in the pipeline:
```
input() → RTVI → STT → user_agg → LLM → ...
```
The `VADUserStoppedSpeakingFrame` must travel UPSTREAM from user_agg to reach the STT
service. When the VAD fires inside user_agg, it broadcasts this frame via
`broadcast_frame()` which sends it both UPSTREAM and DOWNSTREAM. The UPSTREAM copy
reaches the STT processor (since STT is `user_agg._prev`). The STT then calls
`_handle_vad_user_stopped_speaking()` which flushes the accumulated buffer.

**Fix — change the base class**:
```python
# In your custom STT service:
from pipecat.services.stt_service import SegmentedSTTService

# ❌ Wrong (no accumulation):
class WhisperSTTService(STTService):
    ...

# ✅ Correct (accumulates + VAD-triggered):
class WhisperSTTService(SegmentedSTTService):
    ...
```

**Fix — also handle the NOT_GIVEN settings warning for STTSettings**:
```python
# In WhisperSTTService.__init__, pass model and language to suppress the
# "STTSettings: model, language fields are NOT_GIVEN" warning:
class WhisperSTTSettings(STTSettings):
    model: str | None = None
    language: str | None = None
    # ... other fields
```

**Verification** — after the fix, the server log should show:
- Audio frames flowing (640B each) accumulated into buffer
- When user stops speaking: `_handle_user_stopped_speaking` triggers
- Then: `[WHISPER-STT] run_stt called with 82000+B audio` (full accumulated audio)
- Then: `TranscriptionFrame` or `InterimTranscriptionFrame` with transcribed text

**Direct STT test** (bypasses pipeline entirely, use this as the first diagnostic step):
```bash
uv run python3 -c "
import asyncio
from src.services.whisper_stt import WhisperSTTService
stt = WhisperSTTService(model_size='small')
with open('/tmp/test_speech.pcm','rb') as f: audio = f.read()
async def t():
    async for frame in stt.run_stt(audio):
        if frame and hasattr(frame,'text'): print(f'STT: {frame.text}')
asyncio.run(t())
"
```

### 21. CDP browser_console session dies after Target.closeTarget

**Symptom**: After using `browser_cdp` with `Target.closeTarget` or
`Target.attachToTarget`, the `browser_console` tool returns:
```
RuntimeError: CDP error on id=N: {'code': -32001, 'message': 'Session with given id not found.'}
```
This persists even after navigating to new pages. `browser_navigate` and
`browser_snapshot` still work, but console execution is permanently broken for the
rest of the session.

**Root cause**: The `browser_console` tool maintains its own CDP session mapping
internally. When you directly call Target methods via `browser_cdp`, you open/adopt
a *different* CDP session than the one browser_console uses. Closing the DevTools
target (which browser_console was attached to) invalidates that session, and the tool
cannot reconnect.

**Prevention**: Do NOT use `browser_cdp` method `Target.closeTarget` or
`Target.attachToTarget` when you still need `browser_console`. The browser_console
tool uses a fixed session that is established at tool-initialization time and may
target the DevTools page rather than the Vite/application page.

**Workaround if already broken**:
1. Navigate to `about:blank` — creates a fresh page target
2. Navigate back to the application URL
3. The browser_console may re-attach to the new page
4. If it does not, the CDP supervisor root session must be restarted (not possible
   from within the agent — the user needs to `/browser close` + `/browser connect`)

**Alternative — avoid browser_console entirely**: Execute JavaScript through
`browser_navigate` with a `javascript:` URL:
```
browser_navigate(url="javascript:alert('injected')")
```
But `javascript:` URLs cannot return values. For logging, use:
```
browser_navigate(url="javascript:console.log('test');void(0)")
```
Then check output via `browser_console(clear=True)` (after confirming the tool still
works) or `browser_cdp` with the appropriate session/target.

**Best practice for CDP testing**: Write a standalone script (Python + aiortc for
WebRTC, Node.js + playwright/puppeteer for browser automation) rather than relying
on the session-scoped CDP browser tools for complex multi-step flows. The stand-alone
script survives disconnects and is rerunnable without session state.

Without an explicit trigger, the bot stays silent until the user speaks first.
To have the bot start the conversation, register an `on_client_ready` handler
on the worker's RTVI observer (NOT on the transport):

```python
@worker.rtvi.event_handler("on_client_ready")
async def on_client_ready(rtvi):
    context.add_message({"role": "developer", "content": "Start by introducing yourself."})
    await worker.queue_frames([LLMRunFrame()])
```

The difference between `@transport.event_handler` and `@worker.rtvi.event_handler`:
- **`@transport.event_handler("on_client_ready")`** — called by the transport when a client (WebSocket/WebRTC) connects. **Not** fired by SmallWebRTC — the Pipecat framework only calls this for Daily transport.
- **`@worker.rtvi.event_handler("on_client_ready")`** — called when the RTVIProcessor receives the client's `client-ready` RTVI message over the data channel. This is the correct pattern for SmallWebRTC transport.

For SmallWebRTC without RTVIProcessor, queue the initial `LLMRunFrame` in the `on_connection` callback after the pipeline starts.

Reference: `pipecat-ai/pipecat-examples/simple-chatbot/server/bot-openai.py` — see
the full `on_client_ready` → `LLMRunFrame()` pattern in `run_bot()`.

### 22. PrebuiltUI 文字渲染 — 不要用 BotTextProcessor，不要自定义 FrameProcessor

**中文版**（用户强调）：

1. **不要**自定义 FrameProcessor 去拦截 TTSTextFrame 推 OutputTransportMessageFrame
2. **不要**把 RTVIProcessor() 放进 processors 列表（WorkerRunner 内置）
3. **PrebuiltUI 的对话消息通过 RTVIObserver 的 BotOutput 事件渲染**，不是 bot-transcription
4. RTVIObserver 自动处理 AggregatedTextFrame + BotOutputMessage → data channel → PrebuiltUI 渲染
5. 如果确实需要自定义操作，用 `worker.rtvi.event_handler` 注册事件处理器

**数据流**（2026-06-30 从源码确认）:
```
LLM → LLMTextFrame
  → assistant_agg 聚合为 AggregatedTextFrame
  → 向上游回穿 transport.output()
  → RTVIObserver 捕获（src is BaseOutputTransport）
  → 发 RTVI BotOutputMessage → data channel → PrebuiltUI 渲染
```

**PrebuiltUI 注册的回调**（从 minified bundle 确认）:
- `onBotConnected` ✓
- `onBotDisconnected` ✓
- BotOutput（通过 React hook 订阅）✓
- `onBotTranscript` ❌ （没注册）

**参考**: `references/prebuiltui-bot-output-mechanism.md`

### 23. `observers=[RTVIObserver()]` 传空 rtvi → 所有消息静默丢弃

**Symptom**: server log 看不到 `send_rtvi_message` 或 BotOutput 相关日志。RTVIObserver
配置了（默认参数都是 True），但 PrebuiltUI 收不到任何 RTVI 事件（BotOutput、BotLLMText、
BotTTSStarted 等）。但 pipeline 正常跑（LLM 回应、TTS 说话）。

**Root cause** (`observer.py:205-220` + `worker.py:385-405`):

```python
# observer.py
def __init__(self, rtvi: Optional["RTVIProcessor"] = None, ...):
    self._rtvi = rtvi        # ← 未传时 self._rtvi = None

async def send_rtvi_message(self, model, ...):
    if self._rtvi:           # ← False! 静默丢弃
        await self._rtvi.push_transport_message(model, exclude_none)
```

```python
# worker.py — PipelineWorker 自动创建连线 observer
if 外部没有传 RTVIObserver:        # line 385-405
    observers.append(self._rtvi.create_rtvi_observer(...))  # ✅ rtvi 正确连线
else:
    # 使用外部 observer — rtvi=None，所有消息静默丢弃 ❌
```

当用户传 `observers=[RTVIObserver()]` 时，PipelineWorker 认为用户已提供自定义
observer，跳过默认创建。但用户的 observer 没有 `rtvi` 引用，消息全部丢弃。

**Correct pattern**（官方 `code-helper/server/bot.py:188-198`）:

```python
# ❌ Wrong:
worker = PipelineWorker(
    pipeline,
    params=PipelineParams(...),
    observers=[RTVIObserver()],          # rtvi=None → 静默丢弃所有消息
)

# ✅ Correct:
worker = PipelineWorker(
    pipeline,
    params=PipelineParams(...),
    # 不传 observers=, 让 PipelineWorker 自动创建连线 observer
    # 可选传 rtvi_observer_params=RTVIObserverParams(...) 自定义行为
)
```

**官方参考**: `code-helper/server/bot.py:188-198` — 用 `rtvi_observer_params=` 替代
`observers=`。PipelineWorker 自动调用 `self._rtvi.create_rtvi_observer(params=...)`。

### 24. SmallWebRTC `is_connected()` 3秒 ping 窗口导致 "Client not connected"

**Symptom**: 浏览器显示 "Client READY, Agent CONNECTED"，发送文字消息后 server log 出现
`Client not connected. Queuing app-message.` 且消息永远不被处理。或首次连接后第一条消息
被排队，之后所有消息都被排队。

**Root cause** (`connection.py:656-672`):

```python
def is_connected(self) -> bool:
    if not self._connect_invoked:
        return False
    if self._last_received_time is None:
        return self._pc.connectionState == "connected"
    # Checks if the last received ping was within the last 3 seconds.
    return (time.time() - self._last_received_time) < 3   # ← 3秒窗口！
```

一旦收到第一个 ping 消息，`_last_received_time` 被设值（`on_message` line 346），
`is_connected()` 就不再检查真实的 WebRTC 连接状态，只查最近 3 秒内有没有 ping。
如果客户端 3 秒以上没发 ping，`is_connected()` 返回 False，数据通道消息被排队。

**DTLS 握手超时在 Cloud 浏览器环境**（第二次根本原因）:
aiortc 的 `connectionState` 在 ICE 成功后可能不变成 "connected"，卡在 "connecting"。

```
22:29:28.334 | ICE connection state is checking, connection is connecting
22:29:28.406 | ICE connection state is completed, connection is connecting
                                                       ^^^^^^^^^^ 一直 "connecting"！
```

`_last_received_time` 为 None 时 `is_connected()` 检查 `connectionState == "connected"`，
但 aiortc 的 connectionState 可能被 DTLS 握手卡住，即使 ICE 已完成。消息被排队后
`connect()` 也检查 `is_connected()` → 返回 False → 排队消息永远不刷新。

**Fix — 手动刷新 pending app-messages**（server_prebuilt.py `_run_pipeline`）:

```python
async def _run_pipeline(connection: SmallWebRTCConnection, session_id: str):
    transport = SmallWebRTCTransport(...)
    worker, _context = build_pipeline(transport=transport, ...)

    from pipecat.workers.runner import WorkerRunner
    runner = WorkerRunner()
    await runner.add_workers(worker)
    logger.info(f"[{session_id}] pipeline starting")

    # ⭐ 手动刷新 pending app-messages（aiortc DTLS 握手可能未完成）
    pending = getattr(connection, "_pending_app_messages", [])
    if pending:
        logger.info(f"[{session_id}] flushing {len(pending)} queued app-messages")
        for msg in list(pending):
            await connection._call_event_handler("app-message", msg)
        pending.clear()

    try:
        await runner.run(auto_end=False)
    ...
```

**参考**: `references/cloud-browser-dtls-race.md`

### 26. RTVIObserver AggregatedTextFrame src 守卫 — 分离 TTS 不兼容

**Symptom**: LLM 正常回应（prompt tokens + completion tokens 可见），TTS 也说
话（Bot started speaking），但 PrebuiltUI 对话窗口不显示 assistant 回复文本。
server log 中无 BotOutput、`_handle_aggregated_llm_text` 调用。

**Root cause**: `RTVIObserver.on_frame()` 在 `observer.py:483-495` 处理 
`AggregatedTextFrame` 前检查 `isinstance(src, BaseOutputTransport)`。
当 AggregatedTextFrame 由独立 TTS 服务产生并推送时，`src` 是 
`EdgeTTSService`（或 `TTSService`），不是 `BaseOutputTransport`，observer 跳过。

```python
elif isinstance(frame, AggregatedTextFrame) and (...):
    if not isinstance(src, BaseOutputTransport):
        mark_as_seen = False       # ← 跳过！不调 _handle_aggregated_llm_text
    else:
        await self._handle_aggregated_llm_text(frame)  # ← 发 BotOutput
```

**AggregatedTextFrame 产生来源**:
| 来源 | 文件位置 | src | observer 处理? |
|---|---|---|---|
| TTS 基类 (`run_tts` 完成后) | `tts_service.py:971` | `EdgeTTSService` | ❌ 跳过 |
| `LLMTextProcessor` 聚合 | `llm_text_processor.py:86` | `LLMTextProcessor` | ❌ 跳过 |
| GeminiLiveLLMService 直接输出 | 内置 | `GeminiLiveLLMService` | ❌ 跳过 |
| `assistant_agg` 向上游回穿 `output()` | `llm_response_universal.py` | `BaseOutputTransport` | ✅ 处理 |

**唯一被处理的路径**: `assistant_agg` 生成 `AggregatedTextFrame` 后向上游 push，
流经 `transport.output()`。observer 收到时的 `src` 是 `output()`（即 
`BaseOutputTransport`），通过检查 → 发 BotOutput。

**影响**: 官方示例 `bot.py` 用 GeminiLiveLLMService（无分离 TTS），可以正常工作。
使用分离 STT + LLM + TTS 的 pipeline 时，AggregatedTextFrame 从 TTS 产出，
observer 不处理。

**已知官方轮子（仍不解决 src 守卫问题）**:
- `LLMTextProcessor` (`pipecat.processors.aggregators.llm_text_processor`) — 
  把 `LLMTextFrame` 转 `AggregatedTextFrame` 并推下游。但 observer 同样跳过
  （因为 src=LLMTextProcessor，不是 BaseOutputTransport）。

**此问题暂无官方轮子解决。** 分离 TTS 的 pipeline 需要自定义方法发 BotOutput。

**参考**: `references/prebuiltui-bot-output-mechanism.md` 的"关键坑"章节。

**中文版**（用户强调）：

1. **不要**自定义 FrameProcessor 去拦截 TTSTextFrame 推 OutputTransportMessageFrame
2. **不要**把 RTVIProcessor() 放进 processors 列表（WorkerRunner 内置）
3. **PrebuiltUI 的对话消息通过 RTVIObserver 的 BotOutput 事件渲染**，不是 bot-transcription
4. RTVIObserver 自动处理 AggregatedTextFrame + BotOutputMessage → data channel → PrebuiltUI 渲染
5. 如果确实需要自定义操作，用 `worker.rtvi.event_handler` 注册事件处理器

**数据流**（2026-06-30 从源码确认）:
```
LLM → LLMTextFrame
  → assistant_agg 聚合为 AggregatedTextFrame
  → 向上游回穿 transport.output()
  → RTVIObserver 捕获（src is BaseOutputTransport）
  → 发 RTVI BotOutputMessage → data channel → PrebuiltUI 渲染
```

**PrebuiltUI 注册的回调**（从 minified bundle 确认）:
- `onBotConnected` ✓
- `onBotDisconnected` ✓
- BotOutput（通过 React hook 订阅）✓
- `onBotTranscript` ❌ （没注册）

**参考**: `references/prebuiltui-bot-output-mechanism.md`

## References

| Symptom | Root cause | Fix |
|---|---|---|
| Browser "authenticating" → "disconnected" | /start response missing sessionId or iceConfig | Return camelCase `{"sessionId": ..., "iceConfig": {...}}` |
| `InterimTranscriptionFrame.__init__() missing 'timestamp'` | pipecat 1.4.0 API change | Add `user_id` + `timestamp` args |
| `/api/offer` returns 404 | Client POSTs to `/sessions/{id}/api/offer` | Register dual routes |
| `takes 0 positional arguments but 1 was given` | build_pipeline uses keyword-only args | `build_pipeline(transport=transport)` |
| HTTP handler hangs, curl timeout | `await runner.run()` blocks the response | Use `background_tasks.add_task()` |
| Audio stutter with EdgeTTS | 24kHz → 16kHz resampling | Don't set audio_out_sample_rate |
| Pipeline starting then crashed | Missing RTVIProcessor or RTVIObserver | Add both to pipeline workers |
| BotTextProcessor `_check_started` error | `self.__started` name-mangles to `_FrameProcessor__started` — setting `self._started = True` doesn't help | Set `self._FrameProcessor__started = True` in `__init__` |
| BotTextProcessor never fires, no text in chat | Placed AFTER TTS but TTSTextFrame is consumed by TTS | Place BotTextProcessor BETWEEN LLM and TTS |
| BotTextProcessor stalls pipeline (no TTS audio) | Push order reversed — OutputTransportMessageFrame before original TTSTextFrame | Push original TTSTextFrame downstream FIRST, then push OutputTransportMessageFrame upstream |
| LLM responds (DEEPSEEK-REASONING + LLM-CONTENT logs visible) but BotText sees only MetricsFrame; no LLMTextFrame, no TTS | WebRTC ICE closes → pipeline receives EndFrame → queue processor stops — BUT the LLM's HTTP streaming (started via `_process_context → get_chat_completions`) continues asynchronously. LLMTextFrames pushed during streaming go into BotText's input queue but are never dequeued | Two mitigations: (1) add `enable_direct_mode=True` to BotTextProcessor's `__init__` to process frames synchronously (no queue); (2) keep the pipeline alive until all pending LLM HTTP responses complete by delaying EndFrame propagation |
| TTS never called despite LLM responding | LLM outputs text but BotTextProcessor or pipeline ordering drops TTSTextFrame | Add DebugFrameProcessor before and after TTS; verify LLMTextFrame → TTSTextFrame → TTSAudioRawFrame chain |

## References

  - `references/deepseek-litellm-reasoning-content.md` — Triple-patch fix for DeepSeek models through LiteLLM (reasoning_content vs content bug)
  - `references/pipecat-1.4.0-api-gotchas.md` — comprehensive pitfall catalog (VAD, RTVI, background_tasks, routes, camelCase, BotTextProcessor, etc.)
  - `references/aiortc-cli-browser-test.md` — aiortc e2e verification script
  - `references/pipecat-llm-pipeline-shutdown-race.md` — Pipeline shutdown vs LLM HTTP streaming race (enable_direct_mode workaround)
  - `references/websocket-test-page-and-autoplay.md` — browser test page build, AudioContext resume pattern, sendTest() standalone test button
  - `references/events-panel-integration.md` — Chinese marker events for STT/LLM/TTS in browser Events panel
  - `references/prebuiltui-bot-output-mechanism.md` — PrebuiltUI BotOutput 消息机制（替代 BotTextProcessor，RTVIObserver 内置）
  - `references/vite-host-binding.md` — Vite dev server host binding (0.0.0.0) for cross-machine access
  - `references/test-audio-module-isolation.md` — standalone `src/test_audio.py` module pattern (real TTS generation, `_audio_in_queue` injection, event helper, one-delete removal)
  - `references/comparison-driven-cleanup.md` — find a known-good reference (e.g. `bot_js_client.py`) and diff your in-flight code against it to delete redundant wheels
  - `scripts/webrtc-test-client.py` — aiortc-based WebRTC test client (connects, sends audio, monitors STT)

### 31. `_gen_tts()` must be async — `asyncio.run()` fails from FastAPI endpoint

**Symptom**: `RuntimeError: asyncio.run() cannot be called from a running event loop`
when clicking the test-audio button from inside a FastAPI async endpoint.

**Fix — make `_gen_tts` itself async and `await` it**:

```python
# ❌ Wrong (sync wrapper around async):
def _gen_tts(self) -> bytes:
    async def _gen():
        await Communicate(...).save(mp3_path)
    return asyncio.run(_gen())          # RuntimeError! Already in event loop

# ✅ Correct (fully async):
async def _gen_tts(self) -> bytes:
    await Communicate(...).save(mp3_path)   # await directly
```

The `except RuntimeError: loop.run_until_complete()` catch also fails — you
can't call `run_until_complete` from inside a coroutine running in the same
loop. Remove both and just `await` directly.

### 32. Session_id from POST /start is client-only — remove from internal code

**Symptom**: `_run_pipeline(connection, session_id)` passes a UUID only used
for log messages.

**Fix**: Drop the parameter, use `connection.pc_id` for logging instead:

```python
async def _run_pipeline(connection):                        # no session_id param
    logger.info(f"[{connection.pc_id}] pipeline starting")  # pc_id, not uuid
    ...
    connection._inject_inbound = transport.input()           # stashed for inject
```

`connection.pc_id` (e.g. `SmallWebRTCConnection#0-abc123`) is shorter, more
readable, and directly maps to `_pcs_map.keys()`. The `POST /start` endpoint
still returns `sessionId` (required by SmallWebRTC protocol), but our internal
code never touches it.

Also simplify SDP Offer + ICE Candidate routes: keep only
`/sessions/{session_id}/api/offer` (the bare `/api/offer` versions are
never called by `@pipecat-ai/small-webrtc-transport`).

### 33. Comparison-driven cleanup — find the canonical reference, diff against it

**Symptom**: After several iteration rounds, your `server_prebuilt.py` has
accumulated custom code that exactly duplicates framework internals — a
`_handle_ice_patch` that copies `request_handler.py:handle_patch_request`,
a custom `BotTextProcessor` that copies `LLMTextProcessor`, a `_handle_offer`
that re-plumbs what `webrtc_handler.handle_web_request` already accepts.
You're not sure which is dead code and which is load-bearing.

**Discipline — find a working reference and diff against it:**

```bash
# In the workspace you have two example servers (one known-good, one in-flux):
ls src/                                 # likely has bot_js_client.py and server_prebuilt.py
ls pipecat-examples/simple-chatbot/server/   # upstream reference
ls small-webrtc-prebuilt/test/                # upstream reference
```

Pick the one that's the most-known-good, smaller surface, and that does the
same job. Open both side-by-side. For each custom function in your version,
ask: **"Does the reference do this? If yes, what does it use?"**

**Checklist to run when comparing:**

1. **Route proliferation** — does the reference register both `/api/offer`
   and `/sessions/{id}/api/offer`? If not, the duplicate is dead code.
   (Confirmed for `@pipecat-ai/small-webrtc-transport >=1.10`: only the
   `/sessions/{id}/...` path is ever called.)

2. **Handler reimplementation** — does the reference define a custom
   `_handle_ice_patch()` that calls `candidate_from_sdp` directly? If not,
   it's likely `handler.handle_patch_request()` already does the same thing.

3. **Logging plumbing** — does the reference carry a UUID through every
   function just to log it? If not, log against `connection.pc_id` instead.

4. **Middleware** — does the reference need CORS? Only if the client runs
   on a different origin. PrebuiltUI mounted on the same FastAPI does not
   need CORSMiddleware; the JS client running on port 5173 with bot on
   port 7860 does.

5. **Logging config** — does the reference call `logging.basicConfig()`?
   If you're already using loguru (look for `from loguru import logger`),
   the stdlib config is redundant noise — loguru takes over.

6. **Imports** — are WorkerRunner and other module-level imports buried
   inside the function? Move them to module top.

**Outcome**: User explicitly demanded "对照着 bot_js_client.py 把多余轮子删掉"
in a pipecat-ai-prebuilt session. After diffing:

| Custom code | Reference behavior | Action |
|---|---|---|
| `_handle_ice_patch()` (17 lines) | `webrtc_handler.handle_patch_request()` | Delete |
| `/api/offer` direct routes (2 endpoints) | Reference only has `/sessions/{id}/` | Delete |
| `_handle_offer(session_id=...)` | Reference uses `connection.pc_id` for logs | Drop param |
| `import logging; logging.basicConfig(...)` | Reference has neither (loguru only) | Delete |
| `CORSMiddleware` block | Same-origin PrebuiltUI doesn't need it | Delete |
| `from pipecat.workers.runner import WorkerRunner` inside function | Reference imports at top | Move |

Result: `server_prebuilt.py` 197 → 134 lines (-32%). All endpoints work;
no regression in LLM/TTS/STT/RTVI path. Verified by browser connection +
text send + assistant reply in conversation panel.

**When NOT to delete**: When the custom code is for a feature the
reference doesn't have (e.g. inject-test-audio, opening greeting via
`on_client_ready` → `LLMRunFrame()`, CORS for cross-port client). Those
features only exist in YOUR version and are legitimate.
