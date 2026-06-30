# Testing Pipecat PrebuiltUI in Browserbase Cloud Browser

## Tab management

Browserbase cloud browsers accumulate many tabs across sessions (Pipecat UI,
Voice UI Kit demo apps, GitHub, etc.). The CDP supervisor can get confused
when multiple tabs share similar URLs (`localhost:8766/client/`), leading to:

- `browser_click` / `browser_type` acting on the wrong tab
- `browser_snapshot` showing stale content from a different page
- Frame tree URL mismatch (showing `localhost:5175` instead of `localhost:8766`)
- Connection attempts using stale session IDs

### Prevention

Before a new test session, close all unnecessary tabs:

```javascript
// Via CDP — close irrelevant targets:
browser_cdp(method='Target.closeTarget', params={'targetId': '...'})
```

Keep only ONE Pipecat UI tab open.

### Session cache

The PrebuiltUI client stores session state in-memory (React state), not in
localStorage/sessionStorage/IndexedDB. Even after clearing all storage and
reloading, the client may reconnect to stale sessions.

**Symptoms:** Server log shows repeated `PATCH /sessions/<old-id>/api/offer 404`
because the browser uses cached session IDs from previous connections.

**Fix:** Open a completely new browser tab (via `Target.createTarget`) and
navigate it to the PrebuiltUI URL.

```python
browser_cdp(method='Target.createTarget', params={'url': 'http://localhost:8766/'})
```

### ICE/STUN connectivity

The Browserbase cloud browser requires STUN servers for WebRTC — the client
and server are on different machines. Empty `iceServers` causes the connection
to stall at `INITIALIZED` forever.

Always include at least one STUN server:

```python
webrtc_handler = SmallWebRTCRequestHandler(
    ice_servers=[IceServer(urls="stun:stun.l.google.com:19302")],
)
```

### Text input via browser_type or React props

The PrebuiltUI text input is a React controlled component. `browser_type` sends
native input events that React recognizes (the send button becomes enabled).

**Step by step:**
1. Connect the session (click Connect → wait for Client READY, Agent READY)
2. `browser_type(ref='@e21', text='你好')` — text appears in input
3. `browser_click(ref='@e22')` — send button is now enabled
4. Wait 15-20s for LLM + TTS to process
5. Check conversation panel for assistant reply

**Fallback when `browser_type` doesn't enable the send button** (e.g., duplicate
UI instances cause wrong ref mapping):

```javascript
// 1. Set value via native setter
const inp = document.querySelector('input[placeholder="Type message..."]');
const nativeSetter = Object.getOwnPropertyDescriptor(
  window.HTMLInputElement.prototype, 'value'
).set;
nativeSetter.call(inp, 'test');

// 2. Find and call React's onChange handler directly
let reactProps;
for (const key in inp) {
  if (key.startsWith('__reactProps')) { reactProps = inp[key]; break; }
}
reactProps.onChange({ target: inp, currentTarget: inp, preventDefault: ()=>{} });

// 3. Wait ~500ms for React to process the setState update
setTimeout(() => {
  const btn = inp.parentElement?.querySelector('button');
  if (btn && !btn.disabled) btn.click();
}, 500);
```
