# Prebuilt Package Decision: `pipecat-ai-prebuilt` vs `pipecat-ai-small-webrtc-prebuilt`

Two PyPI packages mount a prebuilt UI client at `/client`.
Only one works with SmallWebRTC.

---

## Decision

**Use `pipecat-ai-prebuilt` (≥ 1.0.3).**  
**Delete `pipecat-ai-small-webrtc-prebuilt` (≥ 2.5.0) if it happened to be installed.**

---

## Comparison

| Aspect | `pipecat-ai-prebuilt` (✅) | `pipecat-ai-small-webrtc-prebuilt` (❌) |
|---|---|---|
| PyPI | v1.0.3 | 2.5.0 |
| JS bundle confirms? | `startBot` = 3+ hits, `PipecatClient` = 2 hits | `startBot` = 0, `PipecatClient` = 0 |
| Calls `/start`? | ✅ via `startBot()` | ❌ |
| Calls `/api/offer`? | ✅ (via `SmallWebRTCTransport`) | ❌ (uses Daily protocol) |
| Data channel (RTVI)? | ✅ `createDataChannel` in module JS | ❌ |
| Works with our server? | ✅ Full flow | ❌ "一直 loading" forever |

## How to diagnose which is installed

```bash
# Check which packages exist
pip list | grep prebuilt

# Grep the JS bundle for the protocol markers
find .venv -path '*/client/dist/assets/index-*.js' \
  -exec grep -l 'startBot\|PipecatClient' {} \;
# If only the wrong package is present → no matches
```

## Symptoms of the wrong package

1. Browser shows "一直 loading" (perpetual loading spinner)
2. RTVI console log shows:
   ```
   Transport state: initialized
   Track started: audio for participant ...
   Transport state: authenticating
   Transport state: disconnected
   ```
3. Server logs show `POST /start` is never called (client doesn't even try)

## How to fix

```bash
# Install the correct package
uv pip install pipecat-ai-prebuilt

# Remove the broken package
uv pip uninstall pipecat-ai-small-webrtc-prebuilt
```

## Why two packages?

`pipecat-ai-small-webrtc-prebuilt` (2.5.0) was an **earlier, Daily-based** 
prebuilt client that happened to mount at the same path.  Its JS bundle is
essentially a Daily Call Machine demo UI — it has no `/start` or
`/api/offer` protocol.  The `pipecat-ai-prebuilt` package is the upstream
`pipecat-ai/small-webrtc-prebuilt` client with all 4 transports.

---

## Code import

```python
# Correct
from pipecat_ai_prebuilt.frontend import PipecatPrebuiltUI

# Does not exist (correct package is not pipecat_ai_small_webrtc_prebuilt):
# from pipecat_ai_small_webrtc_prebuilt.frontend import SmallWebRTCPrebuiltUI
```
