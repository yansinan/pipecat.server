#!/usr/bin/env python3
"""
测试音频注入器 — 实时 TTS 生成 + 通过框架连接池查找注入目标。

用法：
  from src.helpers.test_audio import test_audio
  test_audio.set_handler(webrtc_handler)          # 启动时注册
  await test_audio.inject_latest()                # 向最新 session 注入

删除：删本文件 + bot_js_client 中 set_handler() + inject 端点共 2 处即可。
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile

from loguru import logger

from pipecat.frames.frames import InputAudioRawFrame, OutputTransportMessageFrame
from pipecat.processors.frame_processor import FrameDirection

CHUNK_SIZE = 640
"""推入 _audio_in_queue 的分块大小（字节），与 WebRTC 音频包一致。"""


class TestAudioInjector:
    """测试音频注入器。

    自己不做 session 追踪——通过 webrtc_handler._pcs_map 找最新连接。
    每个连接在 _run_pipeline 里挂了一个 _inject_inbound 属性指向 transport.input()。

    需要手动调用 set_handler() 传入 webrtc_handler 实例。
    """

    def __init__(self):
        self._pcs_map: dict | None = None
        """SmallWebRTCRequestHandler 内部的连接池 (pc_id → SmallWebRTCConnection)。
        由 set_handler() 注入引用，不持有生命周期。"""

    def set_handler(self, handler) -> None:
        """注入 SmallWebRTCRequestHandler 引用，用于查找活跃 session。"""
        self._pcs_map = handler._pcs_map

    # ── TTS 生成 ──

    async def _gen_tts(self) -> bytes:
        """调用 edge-tts + ffmpeg 生成 16kHz 16-bit mono PCM。"""
        from edge_tts import Communicate

        subprocess.run(["ffmpeg", "-version"], capture_output=True, check=True)

        text = "测试你是否在线，最简短的回复一下。"
        voice = "zh-CN-XiaoxiaoNeural"

        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
            mp3_path = f.name
        try:
            await Communicate(text, voice=voice).save(mp3_path)
            result = subprocess.run(
                [
                    "ffmpeg", "-y", "-i", mp3_path,
                    "-acodec", "pcm_s16le", "-f", "s16le",
                    "-ac", "1", "-ar", "16000",
                    "-loglevel", "error",
                    "-",
                ],
                capture_output=True, timeout=30,
            )
            if result.returncode != 0:
                raise RuntimeError(f"ffmpeg 解码失败: {result.stderr.decode()}")
            pcm = result.stdout
            logger.info(f"生成测试语音: {len(pcm)}B / {len(pcm)/16000/2:.2f}s")
            return pcm
        finally:
            if os.path.exists(mp3_path):
                os.unlink(mp3_path)

    # ── 注入 ──

    async def _push_to(self, inbound) -> int:
        """生成 TTS 并推入指定 input transport，返回字节数。

        inbound 是 BaseInputTransport（即 transport.input()），
        负责把音频帧送入 pipeline。

        _audio_in_queue 是独立音频队列（WebRTC 同路径），
        不存在则回退到 pipeline 主队列 push_frame。
        """
        data = await self._gen_tts()
        if hasattr(inbound, "_audio_in_queue") and inbound._audio_in_queue:
            for i in range(0, len(data), CHUNK_SIZE):
                chunk = data[i : i + CHUNK_SIZE]
                await inbound._audio_in_queue.put(
                    InputAudioRawFrame(audio=chunk, sample_rate=16000, num_channels=1)
                )
        else:
            for i in range(0, len(data), CHUNK_SIZE):
                chunk = data[i : i + CHUNK_SIZE]
                await inbound.push_frame(
                    InputAudioRawFrame(audio=chunk, sample_rate=16000, num_channels=1)
                )
        return len(data)

    async def inject_latest(self) -> dict:
        """查找最新 session 并注入测试音频。返回 {"status", "bytes"}。"""
        # 从框架连接池找最新 session 对应的 input transport
        keys = list(self._pcs_map.keys()) if self._pcs_map else []
        if not keys:
            return {"status": "error", "detail": "No active sessions"}
        conn = self._pcs_map[keys[-1]]
        inbound = getattr(conn, "_inject_inbound", None)
        if inbound is None:
            return {"status": "error", "detail": "Session not ready"}

        n = await self._push_to(inbound)
        # 通知浏览器 Events 面板
        msg = json.dumps({
            "type": "app-message",
            "data": {"label": "test-audio", "message": f"注入了 {n}B 的测试音频"},
        })
        await inbound.push_frame(
            OutputTransportMessageFrame(message=msg),
            direction=FrameDirection.UPSTREAM,
        )
        return {"status": "ok", "bytes": n}


# ── 全局实例 ──
test_audio = TestAudioInjector()
