# aiortc as a CLI proxy for browser verification

## When to use this

You need to verify a WebRTC voice server end-to-end but you can't open a real browser. Common reasons:

- `/tmp` is full and Chrome won't spawn (browser_navigate returns code 101)
- CI/headless environment without Chrome
- You want to assert specific SDP/ICE behavior in a test
- You want to run verification in a script that must be re-runnable

aiortc can do a full WebRTC handshake (SDP exchange + ICE + audio track subscription) against your own server. This catches ~80% of bugs a real browser would catch — without needing mic permission or GPU.

## What it does NOT catch

- Actual microphone audio capture (it sends no real audio frames)
- Browser-specific codec negotiation edge cases (Chrome's Opus DTX quirks, Safari's audio worklets)
- UI rendering bugs in the PrebuiltUI React app
- RTVI data channel control messages

For those, you still need a real browser at the end.

## The script

```python
"""e2e_webrtc_probe.py — verify a SmallWebRTC + PrebuiltUI server end-to-end.

Usage: python e2e_webrtc_probe.py [--port 8766] [--hold 8]
"""
import asyncio
import json
import urllib.request

from aiortc import (
    RTCConfiguration,
    RTCPeerConnection,
    RTCSessionDescription,
)


async def probe(port: int = 8766, hold: int = 8) -> bool:
    base = f"http://localhost:{port}"
    pc = RTCPeerConnection(configuration=RTCConfiguration(iceServers=[]))
    pc.addTransceiver("audio", direction="sendrecv")

    # 1. SDP offer
    offer = await pc.createOffer()
    await pc.setLocalDescription(offer)
    req = urllib.request.Request(
        f"{base}/api/offer",
        data=json.dumps({"sdp": pc.localDescription.sdp, "type": "offer"}).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=15) as r:
        answer = json.loads(r.read().decode())
    pc_id = answer.get("pc_id", "")
    print(f"[1] SDP answer received, pc_id={pc_id[:40]}")
    await pc.setRemoteDescription(
        RTCSessionDescription(sdp=answer["sdp"], type=answer["type"])
    )

    # 2. Wait for ICE
    connected = asyncio.Event()

    @pc.on("iceconnectionstatechange")
    async def on_state():
        print(f"    ICE: {pc.iceConnectionState}")
        if pc.iceConnectionState in ("connected", "completed"):
            connected.set()

    try:
        await asyncio.wait_for(connected.wait(), timeout=10)
        print("[2] ICE connected")
    except asyncio.TimeoutError:
        print(f"[2] ICE timeout, state={pc.iceConnectionState}")
        return False

    # 3. Hold connection open to observe server-side pipeline
    print(f"[3] Hold {hold}s, observing server-side pipeline...")
    await asyncio.sleep(hold)
    print(f"[4] ICE={pc.iceConnectionState}, connState={pc.connectionState}")

    # 4. Verify audio track subscription
    receivers = pc.getReceivers()
    print(f"[5] Receivers: {len(receivers)}")
    audio_tracks = [r for r in receivers if r.track and r.track.kind == "audio"]
    for i, r in enumerate(receivers):
        print(f"    receiver[{i}]: track={r.track.kind if r.track else None}")

    await pc.close()
    print("[6] Done.")

    # Pass criteria: ICE connected, server pushed at least 1 audio track
    return pc.iceConnectionState in ("connected", "completed") and len(audio_tracks) >= 1


if __name__ == "__main__":
    ok = asyncio.run(probe())
    raise SystemExit(0 if ok else 1)
```

## Why this works

- aiortc generates a valid SDP offer with audio sendrecv
- The server's `/api/offer` returns a valid SDP answer (verifying SDP parsing + connection setup)
- aiortc + server do ICE via host candidates (no STUN needed for localhost)
- Once ICE is `completed`, the server's `transport.output()` subscribes an audio track — aiortc can see it via `pc.getReceivers()`
- 8s hold time gives the server pipeline a chance to start or fail

## Reading the output

```
[1] SDP answer received, pc_id=SmallWebRTCConnection#0-...   ← /api/offer works
[2] ICE connected                                            ← WebRTC layer wired
[3] Hold 8s, observing server-side pipeline...
[4] ICE=completed, connState=connected                       ← stable
[5] Receivers: 1
    receiver[0]: track=audio                                ← transport.output() works
[6] Done.
```

**All five checkpoints pass = the server is correctly wired end-to-end at the protocol level.** What remains to verify in a real browser:
- Does the PrebuiltUI React UI actually render?
- Does the user's mic stream reach the pipeline and get transcribed?
- Does the LLM/TTS round-trip work in audio terms?

## Common failures and what they mean

| Failure | Likely cause |
|---|---|
| `HTTPError 500` on POST /api/offer | Bug in your `on_connection` callback (import error, missing service) — check server stderr |
| `ICE timeout, state=failed` | Server's ICE config missing STUN, or both sides in different networks |
| `Receivers: 0` | Server pipeline didn't subscribe audio output (transport params wrong) |
| `ImportError` in server stderr | Bad class name (e.g. `SmallWebRTCTransportParams` instead of `TransportParams`) |
