"""
Pipecat Voice Agent — FastAPI WebSocket transport + simple test page.

Uses RawPCMSerializer (16kHz mono PCM over WebSocket binary frames).
Serves test page at / for manual browser testing.

Usage:
    PORT=8765 ./run.sh
    open http://localhost:8765/
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from loguru import logger
import uvicorn

from pipecat.frames.frames import LLMRunFrame
from pipecat.workers.runner import WorkerRunner
from pipecat.transports.websocket.fastapi import (
    FastAPIWebsocketParams,
    FastAPIWebsocketTransport,
)

from src.pipeline import build_pipeline
from src.serializers.pcm import RawPCMSerializer

load_dotenv(override=True)

PORT = int(os.environ.get("PORT", "8765"))
HOST = os.environ.get("HOST", "0.0.0.0")

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"

app = FastAPI(title="Pipecat Voice Agent")


TEST_PAGE = """
<!DOCTYPE html>
<html lang="zh-CN">
<head><meta charset="utf-8"><title>Pipecat Test</title>
<style>
body { font-family: sans-serif; max-width:600px; margin:40px auto; padding:0 20px; }
#log { background:#f0f0f0; padding:12px; border-radius:6px; font-family:monospace; font-size:0.85rem; max-height:400px; overflow-y:auto; }
button { font-size:1rem; padding:10px 20px; cursor:pointer; }
</style></head>
<body>
<h2>Pipecat Voice Agent Test</h2>
<p>1. 点击下面的连接按钮（浏览器会弹出麦克风权限请求）</p>
<p>2. 对着麦克风说话</p>
<p>3. 看日志区</p>
<button id="btn" onclick="toggle()">连接</button>
<div id="status">未连接</div>
<div id="log"></div>
<script>
const SAMPLE_RATE=16000, FRAME_MS=20, FRAME_SAMPLES=SAMPLE_RATE*FRAME_MS/1000, FRAME_BYTES=FRAME_SAMPLES*2;
let ws=null, mediaStream=null, audioCtx=null, workletNode=null, playbackCtx=null, nextPlayTime=0;
const logDiv=document.getElementById('log'), btn=document.getElementById('btn'), statusDiv=document.getElementById('status');
function log(text) {
  const d=document.createElement('div'); d.textContent='['+new Date().toLocaleTimeString()+'] '+text;
  logDiv.appendChild(d); logDiv.scrollTop=logDiv.scrollHeight;
}
async function toggle() {
  if (ws) { ws.close(); btn.textContent='连接'; return; }
  try {
    statusDiv.textContent='请求麦克风…';
    mediaStream = await navigator.mediaDevices.getUserMedia({
      audio:{channelCount:1,sampleRate:SAMPLE_RATE,echoCancellation:true,noiseSuppression:true}
    });
    statusDiv.textContent='连接 WebSocket…';
    ws = new WebSocket('ws://'+location.host+'/conversation');
    ws.binaryType = 'arraybuffer';
    ws.onopen = async () => {
      statusDiv.textContent='已连接'; btn.textContent='断开';
      audioCtx = new AudioContext({sampleRate:SAMPLE_RATE});
      const workletCode = 'class P extends AudioWorkletProcessor{constructor(){super();this.b=new Float32Array('+FRAME_SAMPLES+');this.i=0;}process(i){const c=i[0];if(!c||!c[0])return true;for(let j=0;j<c.length;j++){this.b[this.i++]=c[j];if(this.i>=this.b.length){const i16=new Int16Array(this.b.length);for(let k=0;k<this.b.length;k++){const s=Math.max(-1,Math.min(1,this.b[k]));i16[k]=s<0?s*0x8000:s*0x7fff;}this.port.postMessage(i16.buffer,[i16.buffer]);this.b=new Float32Array('+FRAME_SAMPLES+');this.i=0;}}return true;}}registerProcessor("p",P);';
      const blob = new Blob([workletCode],{type:'application/javascript'});
      await audioCtx.audioWorklet.addModule(URL.createObjectURL(blob));
      workletNode = new AudioWorkletNode(audioCtx,'p');
      const source = audioCtx.createMediaStreamSource(mediaStream);
      workletNode.port.onmessage = ev => { if(ws && ws.readyState===1) ws.send(ev.data); };
      source.connect(workletNode);
      log('麦克风就绪，可以说话了');
    };
    ws.onmessage = async ev => {
      if (ev.data instanceof ArrayBuffer) {
        if (!playbackCtx) { playbackCtx = new AudioContext({sampleRate:SAMPLE_RATE}); nextPlayTime=0; }
        const i16 = new Int16Array(ev.data), f32 = new Float32Array(i16.length);
        for(let i=0;i<i16.length;i++) f32[i]=i16[i]/32768;
        const buf = playbackCtx.createBuffer(1,f32.length,SAMPLE_RATE);
        buf.copyToChannel(f32,0);
        const src = playbackCtx.createBufferSource(); src.buffer=buf; src.connect(playbackCtx.destination);
        const now = playbackCtx.currentTime; if(nextPlayTime<now) nextPlayTime=now;
        src.start(nextPlayTime); nextPlayTime += buf.duration;
      } else if (typeof ev.data==='string') {
        try { const msg=JSON.parse(ev.data); if(msg.type==='text') log('🤖 '+msg.text); }
        catch(e) {}
      }
    };
    ws.onclose = () => { statusDiv.textContent='已断开'; btn.textContent='连接'; resetAudio(); };
    ws.onerror = () => { statusDiv.textContent='错误'; };
  } catch(e) { statusDiv.textContent='错误: '+e.message; log('错误: '+e.message); }
}
function resetAudio() {
  if(workletNode){try{workletNode.disconnect();}catch(e){}workletNode=null;}
  if(audioCtx){audioCtx.close().catch(()=>{});audioCtx=null;}
  if(playbackCtx){playbackCtx.close().catch(()=>{});playbackCtx=null;}
  if(mediaStream){mediaStream.getTracks().forEach(t=>t.stop());mediaStream=null;}
}
</script></body></html>
"""


@app.get("/", response_class=HTMLResponse)
async def index():
    return HTMLResponse(TEST_PAGE)


@app.websocket("/conversation")
async def conversation(ws: WebSocket):
    await ws.accept()
    logger.info("WebSocket client connected")

    transport = FastAPIWebsocketTransport(
        websocket=ws,
        params=FastAPIWebsocketParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
            serializer=RawPCMSerializer(),
        ),
    )
    worker, context = build_pipeline(
        transport=transport,
        llm_model=os.environ.get("LLM_MODEL", "minimax"),
        whisper_model_size=os.environ.get("WHISPER_MODEL_SIZE", "small"),
        tts_voice=os.environ.get("TTS_VOICE", "zh-CN-XiaoxiaoNeural"),
    )

    context.add_message({
        "role": "developer",
        "content": "你好，我是你的语音助手。简短回答即可。",
    })

    runner = WorkerRunner()
    await runner.add_workers(worker)
    await worker.queue_frames([LLMRunFrame()])

    try:
        await runner.run()
    except WebSocketDisconnect:
        logger.info("Client disconnected")
    finally:
        await worker.cancel()


def main():
    logging.basicConfig(level=logging.WARNING)
    logger.info(f"Pipecat Voice Agent: http://{HOST}:{PORT}/")
    logger.info(f"  WebSocket: ws://{HOST}:{PORT}/conversation")
    uvicorn.run(app, host=HOST, port=PORT)


if __name__ == "__main__":
    main()