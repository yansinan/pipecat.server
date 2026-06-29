# Pipecat Voice Agent — 部署文档

> 用途：供 AI agent 从头搭建 Pipecat 语音 Agent。
> 创建时间：2026-06-29
> 目标环境：Linux x1tablet (Debian 13 trixie), X11/Wayland

## 一、前提条件

```bash
# 1. 系统环境
python3 --version       # 需要 ≥3.11（当前 3.13.5）
uv --version            # 必须安装 uv（当前 0.11.19）
which ffmpeg            # Edge TTS 需要 ffmpeg 解码音频
#    apt install ffmpeg   # 如缺失

# 2. uv 位置
#    常见路径：~/.hermes/bin/uv, ~/.local/bin/uv, ~/.cargo/bin/uv
#    安装：curl -LsSf https://astral.sh/uv/install.sh | sh
```

## 二、项目初始化

```bash
# 创建项目目录
mkdir -p ~/workspace/pipecat && cd ~/workspace/pipecat

# 用 uv 初始化（不创建 .venv，后续 sync 时自动创建）
uv init

# 添加核心依赖
uv add "pipecat-ai[whisper,webrtc]"    # 框架 + 本地 Whisper + WebRTC
uv add edge-tts                         # Edge TTS
uv add fastapi uvicorn                  # WebSocket 服务器
uv add python-dotenv                    # .env 加载
```

### 依赖说明

| 包 | 用途 | 安装方法 |
|---|---|---|
| `pipecat-ai[whisper]` | 框架核心 + 本地 Whisper ASR | `uv add` |
| `pipecat-ai[webrtc]` | WebRTC transport（可选，留作扩展） | `uv add` |
| `edge-tts` | 微软 Edge TTS（免费，中文优质） | `uv add` |
| `fastapi` + `uvicorn` | WebSocket 服务器 | `uv add` |
| `python-dotenv` | 环境变量加载 | `uv add` |

**注意**：不要装 `pipecat-ai[local]`，它需要 `pyaudio` + 系统 `portaudio19-dev` + `build-essential`（gcc），在当前环境编译失败。

## 三、文件结构

```
~/workspace/pipecat/
├── src/
│   ├── __init__.py
│   ├── bot.py              # FastAPI WebSocket 入口
│   ├── pipeline.py         # Pipeline 构建（Worker + Aggregator）
│   └── services/
│       ├── __init__.py
│       ├── edge_tts.py     # Edge TTS 服务（ffmpeg 解码 MP3→PCM）
│       └── whisper_stt.py  # Whisper STT 服务
├── .env                    # 环境变量
├── run.sh                  # 启动脚本
├── pyproject.toml
├── .gitignore
└── claude.md               # AI agent 上下文
```

### 创建目录

```bash
mkdir -p src/services
touch src/__init__.py src/services/__init__.py
```

## 四、核心代码

### 4.1 `.env` — 环境变量

```bash
cat > .env << 'EOF'
# Pipecat 语音 Agent 环境变量
# LLM 端点（LiteLLM / Headroom 代理）
LLM_BASE_URL=http://serverhome.tail2e6efb.ts.net/litellm/headroom/v1/
LLM_API_KEY=*** TTS 配置
TTS_VOICE=zh-CN-XiaoxiaoNeural
TTS_RATE=+0%
TTS_VOLUME=+0%

# STT 配置
WHISPER_MODEL_SIZE=small
WHISPER_CACHE_DIR=./cache/whisper

# VAD 配置
SILERO_CACHE_DIR=./cache/silero

# Transport（webrtc / daily / local）
TRANSPORT=webrtc
EOF
```

### 4.2 `src/services/whisper_stt.py` — Whisper STT 封装

```python
"""Whisper speech-to-text service with locally-downloaded models."""
from __future__ import annotations

import os
from typing import AsyncGenerator

from loguru import logger
from pipecat.frames.frames import Frame, TranscriptionFrame
from pipecat.services.stt_service import SegmentedSTTService

try:
    from faster_whisper import WhisperModel
except ModuleNotFoundError as e:
    logger.error("In order to use Whisper, install: `uv add \"pipecat-ai[whisper]\"`")
    raise ImportError(f"Missing module: {e}") from e


class WhisperSTTService(SegmentedSTTService):
    """Local Whisper STT using faster-whisper (CTranslate2)."""

    def __init__(
        self,
        *,
        model_size: str = "small",
        device: str = "cpu",
        compute_type: str = "int8",  # int8_float16 for GPU
        **kwargs,
    ):
        super().__init__(**kwargs)
        self._model_size = model_size
        self._device = device
        self._compute_type = compute_type
        self._model: WhisperModel | None = None

    async def start(self, frame):
        await super().start(frame)
        cache_dir = os.environ.get("WHISPER_CACHE_DIR", "~/.cache/whisper")
        cache_dir = os.path.expanduser(cache_dir)
        os.makedirs(cache_dir, exist_ok=True)

        self._model = await self._get_loop().run_in_executor(
            None,
            lambda: WhisperModel(
                self._model_size,
                device=self._device,
                compute_type=self._compute_type,
                download_root=cache_dir,
            ),
        )
        logger.info(f"Whisper model '{self._model_size}' loaded")

    async def run_stt(self, audio) -> AsyncGenerator[Frame, None]:
        if not self._model:
            return
        segments, info = await self._get_loop().run_in_executor(
            None,
            lambda: self._model.transcribe(audio, language="zh"),
        )
        for seg in segments:
            yield TranscriptionFrame(seg.text, "", seg.start, seg.end)
```

### 4.3 `src/services/edge_tts.py` — Edge TTS 服务

```python
"""Edge TTS service — Microsoft Edge online TTS via edge-tts library."""
from __future__ import annotations

import asyncio
import io
import subprocess
from typing import AsyncGenerator

from pipecat.frames.frames import Frame, TTSAudioRawFrame, TTSStartedFrame, TTSStoppedFrame
from pipecat.services.tts_service import TTSService, TTSSettings


class EdgeTTSService(TTSService):
    """Text-to-speech using Microsoft Edge's online TTS.

    Streams MP3 audio from edge-tts, decodes to 16kHz mono PCM via ffmpeg.
    """

    class Settings(TTSSettings):
        voice: str = "zh-CN-XiaoxiaoNeural"
        rate: str = "+0%"
        volume: str = "+0%"

    def __init__(self, *, voice: str | None = None, settings: Settings | None = None, **kwargs):
        super().__init__(**kwargs)
        self._settings = settings or self.Settings(voice=voice or "zh-CN-XiaoxiaoNeural")

    async def run_tts(self, text: str, context_id: str) -> AsyncGenerator[Frame | None, None]:
        import edge_tts

        communicate = edge_tts.Communicate(
            text,
            voice=self._settings.voice,
            rate=self._settings.rate,
            volume=self._settings.volume,
        )

        yield TTSStartedFrame()
        mp3_buffer = io.BytesIO()
        try:
            async for chunk in communicate.stream():
                if chunk["type"] == "audio":
                    mp3_buffer.write(chunk["data"])
        except Exception as exc:
            self._logger.warning("Edge TTS stream error: %s", exc)

        mp3_data = mp3_buffer.getvalue()
        if not mp3_data:
            self._logger.warning("Edge TTS returned no audio for: %r", text[:60])
            yield TTSStoppedFrame()
            return

        # Decode MP3 → PCM via ffmpeg
        ffmpeg_cmd = [
            "ffmpeg", "-loglevel", "error",
            "-i", "pipe:0",
            "-f", "s16le",
            "-acodec", "pcm_s16le",
            "-ar", "16000", "-ac", "1",
            "pipe:1",
        ]
        try:
            proc = await asyncio.create_subprocess_exec(
                *ffmpeg_cmd,
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            )
            pcm_data, stderr_data = await proc.communicate(mp3_data)
            if proc.returncode != 0:
                self._logger.error("ffmpeg decode failed (rc=%d): %s", proc.returncode, stderr_data.decode(errors="replace"))
                yield TTSStoppedFrame()
                return
            frame_size = 16000 * 1 * 2 // 50  # 20ms frames
            for offset in range(0, len(pcm_data), frame_size):
                yield TTSAudioRawFrame(
                    audio=pcm_data[offset:offset + frame_size],
                    sample_rate=16000, num_channels=1,
                )
        except FileNotFoundError:
            self._logger.error("ffmpeg not found — install it")
        yield TTSStoppedFrame()
```

### 4.4 `src/pipeline.py` — Pipeline 构建（现代 API）

```python
"""Pipeline configuration — uses PipelineWorker + LLMContextAggregatorPair (>=1.3.0)."""
from __future__ import annotations

import os
from typing import TYPE_CHECKING

from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineParams, PipelineWorker
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    LLMContextAggregatorPair,
    LLMUserAggregatorParams,
)
from pipecat.services.openai.llm import OpenAILLMService

from src.services.edge_tts import EdgeTTSService
from src.services.whisper_stt import WhisperSTTService

if TYPE_CHECKING:
    from pipecat.transports.base_transport import BaseTransport


def build_pipeline(
    *,
    transport: BaseTransport,
    llm_base_url: str = "",
    llm_api_key: str = "",
    llm_model: str = "minimax",
    whisper_model_size: str = "small",
    tts_voice: str = "zh-CN-XiaoxiaoNeural",
) -> tuple[PipelineWorker, LLMContext]:
    """Assemble the full voice-agent pipeline.

    Processor order:
        transport.input() → STT → user_aggregator → LLM → TTS →
        transport.output() → assistant_aggregator
    """
    # VAD — local Silero ONNX
    vad = SileroVADAnalyzer()

    # STT — local Whisper
    stt = WhisperSTTService(model_size=whisper_model_size)

    # LLM — OpenAI-compatible via Headroom/LiteLLM
    llm = OpenAILLMService(
        api_key=llm_api_key or os.environ.get("LLM_API_KEY", ""),
        base_url=llm_base_url or os.environ.get(
            "LLM_BASE_URL",
            "http://serverhome.tail2e6efb.ts.net/litellm/headroom/v1/",
        ),
        model=llm_model,
        settings=OpenAILLMService.Settings(
            system_instruction=(
                "You are a helpful voice assistant. "
                "Keep responses concise and conversational. "
                "Avoid markdown, bullet points, or anything that can't be spoken aloud. "
                "Respond in the same language the user speaks."
            ),
            temperature=0.7,
            max_tokens=512,
        ),
    )

    # TTS — Edge TTS
    tts = EdgeTTSService(voice=tts_voice)

    # Context aggregation with VAD-driven turn detection
    context = LLMContext()
    user_agg, assistant_agg = LLMContextAggregatorPair(
        context,
        user_params=LLMUserAggregatorParams(vad_analyzer=vad),
    )

    # Assemble pipeline
    pipeline = Pipeline([
        transport.input(),
        stt,
        user_agg,
        llm,
        tts,
        transport.output(),
        assistant_agg,
    ])

    worker = PipelineWorker(
        pipeline,
        params=PipelineParams(enable_metrics=True, enable_usage_metrics=True),
    )
    return worker, context
```

### 4.5 `src/bot.py` — 主入口（FastAPI WebSocket 服务）

```python
"""Pipecat Voice Agent — FastAPI WebSocket server.

Usage:
    ./run.sh                                # 启动
    ws://localhost:8765/conversation         # 客户端连接
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys

from dotenv import load_dotenv
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from loguru import logger
import uvicorn

from pipecat.frames.frames import LLMRunFrame
from pipecat.workers.runner import WorkerRunner
from pipecat.transports.websocket.fastapi import (
    FastAPIWebsocketParams,
    FastAPIWebsocketTransport,
)
from src.pipeline import build_pipeline

load_dotenv(override=True)

PORT = int(os.environ.get("PORT", "8765"))
HOST = os.environ.get("HOST", "0.0.0.0")

app = FastAPI(title="Pipecat Voice Agent")


@app.websocket("/conversation")
async def conversation(ws: WebSocket):
    await ws.accept()
    logger.info("WebSocket client connected")

    transport = FastAPIWebsocketTransport(
        websocket=ws,
        params=FastAPIWebsocketParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
            vad_analyzer=None,
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
        await ws.close()


def main():
    logging.basicConfig(level=logging.WARNING)
    logger.info(f"Starting WebSocket server on {HOST}:{PORT}")
    uvicorn.run(app, host=HOST, port=PORT)


if __name__ == "__main__":
    main()
```

### 4.6 `run.sh` — 启动脚本

```bash
#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Locate uv
UV=""
for c in "$HOME/.hermes/bin/uv" "$HOME/.cargo/bin/uv" "$HOME/.local/bin/uv" /usr/local/bin/uv /usr/bin/uv; do
    [[ -x "$c" ]] && { UV="$c"; break; }
done
[[ -z "$UV" ]] && { echo "uv not found"; exit 1; }
echo "uv: $UV"

# Ensure .venv exists
[[ ! -d .venv ]] && "$UV" sync --all-extras

# Model cache isolation (never leak to ~/.cache)
export WHISPER_CACHE_DIR="$SCRIPT_DIR/cache/whisper"
export SILERO_CACHE_DIR="$SCRIPT_DIR/cache/silero"
export XDG_CACHE_HOME="$SCRIPT_DIR/cache"
mkdir -p "$WHISPER_CACHE_DIR" "$SILERO_CACHE_DIR"

# Load .env
[[ -f .env ]] && set -a && source .env && set +a

exec "$UV" run --project "$SCRIPT_DIR" python -m src.bot "$@"
```

```bash
chmod +x run.sh
```

### 4.7 `.gitignore`

```
.venv/
cache/
__pycache__/
*.pyc
.env
*.lock
```

### 4.8 `claude.md` — AI agent 上下文（可选）

创建 `claude.md` 内容参考本文档，给 AI agent 提供项目上下文。关键点：
- 项目结构
- venv 隔离规则（不能用 `source activate` / 必须用 `uv run`）
- 缓存在 `./cache/` 内
- 启动命令：`./run.sh`
- 端口：8765

## 五、环境隔离规则（必须遵守）

```yaml
# ✅ 正确
cd ~/workspace/pipecat
uv run python -m src.bot        # 用项目 .venv 的解释器
uv add ...                       # 用 uv 管理依赖

# ❌ 错误
source .venv/bin/activate        # 不要手动激活 venv
python -m src.bot                # 解释器可能指向系统 Python 或 Hermes venv
pip install ...                  # PEP 668 会拒绝
export VIRTUAL_ENV=...           # 不要全局设置
```

**检查解释器路径**：
```bash
uv run python -c "import sys; print(sys.executable)"
# 必须输出: /home/dr/workspace/pipecat/.venv/bin/python
```

## 六、验证清单

```bash
cd ~/workspace/pipecat

# 1. 依赖安装
uv run python -c "
import pipecat; print('pipecat', pipecat.__version__)
import edge_tts; print('edge_tts', edge_tts.__version__)
"

# 2. 所有服务可导入
uv run python -c "
from pipecat.transports.websocket.fastapi import FastAPIWebsocketTransport, FastAPIWebsocketParams
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.services.whisper.stt import WhisperSTTService
from pipecat.services.openai.llm import OpenAILLMService
from pipecat.pipeline.worker import PipelineWorker, PipelineParams
from pipecat.workers.runner import WorkerRunner
from pipecat.processors.aggregators.llm_response_universal import LLMContextAggregatorPair
print('ALL IMPORTS OK')
" 2>&1 | grep "ALL IMPORTS"

# 3. 启动服务（后台）
./run.sh &
sleep 5

# 4. 端口检查
ss -tlnp | grep 8765 && echo "BOT RUNNING"

# 5. WebSocket 连接测试
python3 -c "
import asyncio, websockets
async def t():
    async with websockets.connect('ws://localhost:8765/conversation') as ws:
        print('CONNECTED OK')
        await ws.close()
asyncio.run(t())
"
```

## 七、已知问题

| 问题 | 原因 | 方案 |
|---|---|---|
| `local` transport 不可用 | `pyaudio` 需要 `portaudio19-dev` + gcc，环境无编译工具 | 改用 WebSocket transport |
| Whisper 模型首次下载慢 | ~950MB (small) 从 HuggingFace 下载 | 预下载或用 `medium` 等小模型 |
| Edge TTS 需要网络 | 调用微软 `speech.microsoft.com` | 国内可直接访问 |
| FastAPI + uvicorn 依赖 | 需要 `fastapi` + `uvicorn` 额外安装 | 已在 `uv add` 中处理 |
| `/tmp` 空间满 | 系统其他进程写 tmp | 设置 `TMPDIR` / 清理 `/tmp/diag-mic` 等 |

## 八、一键部署

执行以下命令可从头搭建完整环境：

```bash
# === 前提检查 ===
command -v uv || curl -LsSf https://astral.sh/uv/install.sh | sh
which ffmpeg || sudo apt install -y ffmpeg

# === 创建项目 ===
cd ~/workspace
mkdir -p pipecat && cd pipecat
uv init
uv add "pipecat-ai[whisper,webrtc]" edge-tts fastapi uvicorn python-dotenv
mkdir -p src/services
touch src/__init__.py src/services/__init__.py

# === 写入源码 ===
# (按本文档 4.1~4.8 写入各文件)

# === 启动 ===
chmod +x run.sh
./run.sh
```
