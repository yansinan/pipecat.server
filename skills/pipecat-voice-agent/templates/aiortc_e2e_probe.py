"""
aiortc e2e probe for a Pipecat SmallWebRTC + PrebuiltUI server.

Simulates what the PrebuiltUI browser client does, headlessly. Use this
when browser tooling is unavailable (Chrome CDP down, /tmp full, headless
servers) to verify the server is wired correctly end-to-end:

  1. /start  → mints pc_id
  2. SDP offer exchange on /api/offer
  3. ICE completes
  4. Server pushes at least one audio track to the client
  5. Server pipeline survives 8s without crashing

Usage:
  PORT=8766 uv run --project . python -m src.server_prebuilt   # in one terminal
  uv run --project . python templates/aiortc_e2e_probe.py     # in another

Edit BASE_URL and PORT to match your server. The probe sends no real
audio, but a working server still subscribes its output audio track,
which is enough to confirm the WebRTC plumbing.
"""
import asyncio
import json
import sys
import urllib.request

from aiortc import (
    RTCConfiguration,
    RTCPeerConnection,
    RTCSessionDescription,
)

BASE_URL = "http://localhost:8766"
ICE_TIMEOUT_S = 10.0
OBSERVE_S = 8.0


def _post(url: str, body: dict, timeout: float = 15.0) -> dict:
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


async def main() -> int:
    # 1. startBot → /start
    try:
        start = _post(f"{BASE_URL}/start", {})
    except Exception as e:
        print(f"[FAIL] /start unreachable: {e}")
        return 1
    pc_id = start.get("pc_id")
    if not pc_id:
        print(f"[FAIL] /start returned no pc_id: {start}")
        return 1
    print(f"[1] /start: pc_id={pc_id}")

    # 2. aiortc creates an offer
    pc = RTCPeerConnection(configuration=RTCConfiguration(iceServers=[]))
    pc.addTransceiver("audio", direction="sendrecv")
    offer = await pc.createOffer()
    await pc.setLocalDescription(offer)
    print(f"[2] SDP offer created, len={len(pc.localDescription.sdp)}")

    # 3. POST /api/offer with pc_id
    try:
        answer = _post(
            f"{BASE_URL}/api/offer",
            {"sdp": pc.localDescription.sdp, "type": "offer", "pc_id": pc_id},
        )
    except urllib.error.HTTPError as e:
        body = e.read().decode()[:300]
        print(f"[FAIL] /api/offer HTTP {e.code}: {body}")
        return 1
    except Exception as e:
        print(f"[FAIL] /api/offer: {e}")
        return 1
    print(f"[3] /api/offer: pc_id={answer.get('pc_id', '?')[:40]}")

    # 4. set remote description
    await pc.setRemoteDescription(
        RTCSessionDescription(sdp=answer["sdp"], type=answer["type"])
    )

    # 5. wait for ICE
    connected = asyncio.Event()

    @pc.on("iceconnectionstatechange")
    async def _on_state():
        print(f"    ICE: {pc.iceConnectionState}")
        if pc.iceConnectionState in ("connected", "completed"):
            connected.set()

    try:
        await asyncio.wait_for(connected.wait(), timeout=ICE_TIMEOUT_S)
        print("[4] ICE connected")
    except asyncio.TimeoutError:
        print(f"[FAIL] ICE did not complete within {ICE_TIMEOUT_S}s")
        await pc.close()
        return 1

    # 6. observe server-side pipeline survives
    print(f"[5] Hold {OBSERVE_S}s observing pipeline...")
    await asyncio.sleep(OBSERVE_S)
    print(
        f"[6] ICE={pc.iceConnectionState}, connState={pc.connectionState}"
    )

    # 7. server pushed at least one audio track
    receivers = pc.getReceivers()
    audio_tracks = [r for r in receivers if r.track and r.track.kind == "audio"]
    if not audio_tracks:
        print(f"[FAIL] no audio track received from server (receivers={len(receivers)})")
        await pc.close()
        return 1
    print(f"[7] ✓ {len(audio_tracks)} audio track(s) from server")

    await pc.close()
    print("[8] Done — all probes passed.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
