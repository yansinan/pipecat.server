#!/usr/bin/env python3
"""
Re-runnable e2e probe for a pipecat SmallWebRTC + PrebuiltUI server.

Usage:
  python scripts/verify_server.py [--port 8766] [--hold 8]

Exit codes:
  0  server is correctly wired (SDP, ICE, audio track subscription all pass)
  1  probe failed (see output for which checkpoint)

Requires: aiortc installed in the active environment
  uv pip install aiortc
  # or: uv add --dev aiortc
"""
import argparse
import asyncio
import json
import sys
import urllib.request

try:
    from aiortc import (
        RTCConfiguration,
        RTCPeerConnection,
        RTCSessionDescription,
    )
except ImportError:
    print("aiortc not installed. Install with: uv pip install aiortc", file=sys.stderr)
    sys.exit(2)


async def probe(port: int, hold: int) -> bool:
    base = f"http://localhost:{port}"
    pc = RTCPeerConnection(configuration=RTCConfiguration(iceServers=[]))
    pc.addTransceiver("audio", direction="sendrecv")

    # 1. SDP exchange
    offer = await pc.createOffer()
    await pc.setLocalDescription(offer)
    try:
        req = urllib.request.Request(
            f"{base}/api/offer",
            data=json.dumps({"sdp": pc.localDescription.sdp, "type": "offer"}).encode(),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=15) as r:
            answer = json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        print(f"[1] FAIL: POST /api/offer returned HTTP {e.code}: {e.read().decode()[:200]}")
        return False
    pc_id = answer.get("pc_id", "")
    print(f"[1] PASS: SDP answer received, pc_id={pc_id[:40]}")
    await pc.setRemoteDescription(
        RTCSessionDescription(sdp=answer["sdp"], type=answer["type"])
    )

    # 2. ICE
    connected = asyncio.Event()

    @pc.on("iceconnectionstatechange")
    async def on_state():
        if pc.iceConnectionState in ("connected", "completed"):
            connected.set()

    try:
        await asyncio.wait_for(connected.wait(), timeout=10)
        print("[2] PASS: ICE connected")
    except asyncio.TimeoutError:
        print(f"[2] FAIL: ICE timeout, state={pc.iceConnectionState}")
        return False

    # 3. Hold
    print(f"[3] Hold {hold}s, observing server-side pipeline...")
    await asyncio.sleep(hold)

    # 4. Audio track subscription
    receivers = pc.getReceivers()
    audio_tracks = [r for r in receivers if r.track and r.track.kind == "audio"]
    if audio_tracks:
        print(f"[4] PASS: server pushed {len(audio_tracks)} audio track(s)")
    else:
        print(f"[4] FAIL: server pushed no audio tracks (got {len(receivers)} receivers)")
        await pc.close()
        return False

    await pc.close()
    return True


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8766)
    ap.add_argument("--hold", type=int, default=8, help="seconds to hold connection")
    args = ap.parse_args()
    ok = asyncio.run(probe(args.port, args.hold))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
