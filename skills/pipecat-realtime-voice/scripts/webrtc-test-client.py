#!/usr/bin/env python3
"""WebRTC test client — connects to pipecat bot via aiortc, sends test audio.

Reliable approach: use MediaPlayer with a WAV file (not custom AudioTrack).
The custom AudioTrack creates a track that the server receives but no audio
frames arrive (server log shows 'Timeout: No audio frame received').

Usage:
  1. Generate test speech PCM:
     uv run python3 -c "
     import edge_tts, asyncio
     async def g():
         c = edge_tts.Communicate('你好，今天天气不错。', voice='zh-CN-XiaoxiaoNeural')
         mp3 = b''
         async for chunk in c.stream():
             if chunk['type'] == 'audio': mp3 += chunk['data']
         with open('/tmp/test_speech.mp3','wb') as f: f.write(mp3)
     asyncio.run(g())
     "
     ffmpeg -y -i /tmp/test_speech.mp3 -f s16le -acodec pcm_s16le -ar 16000 -ac 1 /tmp/test_speech.pcm
     uv run python3 -c "
     import wave; pcm=open('/tmp/test_speech.pcm','rb').read()
     with wave.open('/tmp/test_speech.wav','wb') as w:
         w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000); w.writeframes(pcm)
     "
  2. uv run python3 scripts/webrtc-test-client.py
  3. Check server process log for STT output

Connects to http://127.0.0.1:7860 by default. Set BOT_URL env to override.
"""
import asyncio, os
import httpx
from aiortc import RTCPeerConnection, RTCSessionDescription
from aiortc.contrib.media import MediaPlayer

BOT_URL = os.environ.get("BOT_URL", "http://127.0.0.1:7860")

async def main():
    wav_path = "/tmp/test_speech.wav"
    if not os.path.exists(wav_path):
        # Fallback: create from PCM
        pcm_path = "/tmp/test_speech.pcm"
        if os.path.exists(pcm_path):
            import wave
            with open(pcm_path, "rb") as f:
                pcm = f.read()
            with wave.open(wav_path, "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(16000)
                wf.writeframes(pcm)
        else:
            print("[TEST] No test audio found. Generate one first.")
            return

    player = MediaPlayer(wav_path, loop=False)
    print(f"[TEST] Audio: WAV={wav_path} ({os.path.getsize(wav_path)}B)")

    pc = RTCPeerConnection()
    pc.addTrack(player.audio)
    pc.on("iceconnectionstatechange", lambda: print(f"[TEST] ICE: {pc.iceConnectionState}"))
    pc.on("track", lambda t: print(f"[TEST] Receiving track: {t.kind}"))

    async with httpx.AsyncClient(timeout=30.0) as client:
        r = await client.post(f"{BOT_URL}/start", json={})
        r.raise_for_status()
        sess = r.json()
        session_id = sess["sessionId"]
        print(f"[TEST] Session: {session_id}")

        offer = await pc.createOffer()
        await pc.setLocalDescription(offer)
        r2 = await client.post(
            f"{BOT_URL}/sessions/{session_id}/api/offer",
            json={"sdp": offer.sdp, "type": "offer",
                  "iceServers": sess.get("iceConfig", {}).get("iceServers", [])},
        )
        r2.raise_for_status()
        ans = r2.json()
        answer = RTCSessionDescription(sdp=ans["sdp"], type=ans["type"])
        await pc.setRemoteDescription(answer)

        @pc.on("icecandidate")
        async def on_ice(cand):
            if cand:
                await client.patch(
                    f"{BOT_URL}/sessions/{session_id}/api/offer",
                    json={"pc_id": ans.get("pcId",""),
                          "candidates": [{"candidate": cand.candidate,
                                          "sdpMid": cand.sdpMid,
                                          "sdpMLineIndex": cand.sdpMLineIndex}]},
                )

        # Let audio flow — MediaPlayer streams the WAV automatically
        await asyncio.sleep(10)
        print("[TEST] Done")
        await pc.close()

if __name__ == "__main__":
    asyncio.run(main())
