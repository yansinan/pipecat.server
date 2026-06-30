# WebSocket Test Page and Autoplay Pitfalls

## Diagnostic test page techniques

When debugging browser ↔ server audio flow, add these to the inline page:

### Version bar + message counter

Place a visible version timestamp at the page top so the user can confirm
they're seeing the latest code:

```html
<div style="background:#ffc;padding:4px 10px;font-size:13px;font-family:monospace;border-bottom:2px solid #fa0;">
Pipecat <b>v8</b> | <span id="st">未连接</span> | 消息:<span id="ct">0</span>
</div>
```

Update from JS:
```javascript
function st(s){$("st").textContent=s}
// in ws.onmessage: $("ct").textContent = parseInt($("ct").textContent) + 1;
```

### Console.log at every step

The page log (`lg()`) writes to DOM and has second-resolution timestamps.
Console.log has millisecond accuracy and works even if the DOM handler throws:

```javascript
console.log("TEST CLICKED");
console.log("WS OPEN");
console.log("GREETING RECEIVED", chunkSize);
console.log("SEND PROGRESS "+pct+"%");
console.log("WS MSG type="+(e.data instanceof ArrayBuffer?"binary":"text"));
```

Ask the user to open F12 → Console and read the output there, not the page log.

### Use `var` not `let` when CDP debugging

CDP `Runtime.evaluate` cannot read `let` variables (lexical scope). Use `var`
for debugging visibility:

```javascript
// ❌ CDP can't see this:
let ws=null, ta=null;
// ✅ CDP can see window.ws / window.ta:
var ws=null, ta=null;
```

### Wrap audio playback in try/catch

AudioContext operations in `ws.onmessage` can throw (suspended AudioContext,
missing audio hardware). Wrap them:

```javascript
try{
    var b=ac.createBuffer(1,f32.length,SR);
    b.copyToChannel(f32,0);
    var s=ac.createBufferSource();s.buffer=b;s.connect(ac.destination);
    s.start(nt);nt+=b.duration;
}catch(e){console.log("AUDIO PLAY ERR",e.message)}
```

The error message tells you exactly why audio doesn't play.

## AudioContext autoplay policy

When creating AudioContext OUTSIDE a user gesture event handler
(e.g. in `ws.onmessage` callback), the browser suspends it. Subsequent
`source.start()` calls queue audio silently.

**Rule of thumb:** the AudioContext must be created in the same call stack as
the user's click/tap. Creating it inside `ws.onopen` (which is an asynchronous
callback) does NOT count as a user gesture.

### Fixes that work

1. **Create AudioContext in the click handler** — `ac = new
   AudioContext({sampleRate:SR})` inside the button's `onclick` function.
   Immediately `.resume()` it:
   ```javascript
   ac = new AudioContext({sampleRate:SR});
   ac.resume();  // ensures running state
   ```

2. **Reuse a single AudioContext** — create once in `toggle()`, use for all
   playback in both `toggle()` and `sendTest()`. Don't create a second one.

3. **Auto-resume in onmessage** — before playing audio in `ws.onmessage`,
   check and resume:
   ```javascript
   if(ac && ac.state === 'suspended') await ac.resume();
   ```

### What doesn't work

- Creating `ac` inside `ws.onopen` or `ws.onmessage` — these run in async
  callbacks, not user gestures. Browser suspends the new context.
- Calling `ac.resume()` from `setTimeout` — same problem, no user gesture.
- Multiple AudioContexts — only the one created in the user gesture will
  play. Others are silently suspended.
- `source.start(0)` or `source.start()` without scheduling — AudioContext
  time doesn't advance while suspended, so scheduling can break.
- Using `await` between the user gesture and `ac = new AudioContext()` —
  even one `await` breaks the gesture chain.

### Debugging checklist (in order)

1. ✅ Does the page have a visible version identifier? (confirm cache)
2. ✅ Do `console.log` messages appear in F12 Console?
3. ✅ Does the WebSocket connect? (`WS OPEN` in console)
4. ✅ Does the greeting arrive? (`GREETING RECEIVED`)
5. ✅ Is test audio loaded? (`TA LOADED NNNNN`)
6. ✅ Is audio sent? (`SEND DONE` / `sent complete`)
7. ✅ Does WS receive reply? (`WS MSG type=binary size=NNNN`)
8. ✅ Does audio play? (hear sound + no `AUDIO PLAY ERR` in console)

If 1-6 pass but 7 fails: the server isn't sending a reply to the browser's
audio. Check the server log and test with a Python WebSocket client to
confirm the server pipeline works independently.
