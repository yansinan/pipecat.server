# Pipecat Voice Agent — 部署文档

> **用途**：供 AI agent 从头搭建并运行 Pipecat 语音 Agent。
> **创建时间**：2026-06-29
> **目标环境**：Linux x1tablet (Debian 13 trixie), Wayland, Python 3.11+
>
> **阅读规则**：本文件不包含代码实现。所有 Python 代码以文件路径引用，**请直接 `read_file` 读取对应文件获取最新内容**。

---

## 一、前提条件

```bash
python3 --version       # 需要 ≥3.11
uv --version            # 必须安装 uv（pip 不能用，PEP 668 限制）
which ffmpeg            # Edge TTS 需要 ffmpeg 解码 MP3 → PCM

# 缺失时安装
apt install ffmpeg
curl -LsSf https://astral.sh/uv/install.sh | sh

# 关键：uv 路径（避免 PATH 找不到）
#   ~/.hermes/bin/uv（推荐，本项目使用）
#   ~/.local/bin/uv
#   ~/.cargo/bin/uv
```

---

## 二、项目初始化

### 2.1 创建项目并安装依赖

```bash
mkdir -p ~/workspace/pipecat && cd ~/workspace/pipecat
uv init
uv add "pipecat-ai[whisper,webrtc]"
uv add edge-tts
uv add fastapi uvicorn
uv add python-dotenv
```

### 2.2 依赖说明

| 包 | 用途 |
|---|---|
| `pipecat-ai[whisper]` | 框架核心 + 本地 Whisper ASR (faster-whisper) |
| `pipecat-ai[webrtc]` | WebRTC transport（当前方案未使用，但保留作扩展） |
| `edge-tts` | 微软 Edge TTS（免费，中文质量好） |
| `fastapi` + `uvicorn` | WebSocket 服务器 |
| `python-dotenv` | `.env` 加载 |

**不要**装 `pipecat-ai[local]`——它需要编译 `pyaudio`，需 `portaudio19-dev` + `build-essential` + `gcc`，在本环境编译失败。

---

## 三、文件结构

```
pipecat/
├── .env                          ← LLM/Whisper/TTS 配置
├── .env.example                  ← 配置模板
├── .gitignore                    ← 忽略 .venv, cache, .env
├── pyproject.toml                ← uv 依赖声明
├── run.sh                        ← 启动脚本
├── README.md                     ← 项目说明
├── DEPLOY.md                     ← 本文件
├── src/
│   ├── bot.py                    ← FastAPI WebSocket 入口 + 测试页面
│   ├── pipeline.py               ← Voice pipeline 构建（Worker API）
│   ├── services/
│   │   ├── edge_tts.py           ← Edge TTS 服务（含 ffmpeg 解码）
│   │   └── whisper_stt.py        ← Whisper STT 服务封装
│   └── serializers/
│       └── pcm.py                ← WebSocket binary ↔ Pipecat Frame
├── .venv/                        ← 隔离的 Python 环境（uv 自动管理）
└── cache/                        ← 模型/数据缓存（首次运行下载）
    ├── whisper/                  ← Whisper 模型 (~950MB)
    ├── silero/                   ← Silero VAD 模型 (~30MB)
    └── nltk/                     ← NLTK 分词器数据
```

---

## 四、核心代码

**所有代码文件以路径引用，请用 `read_file` 读取实际内容**：

### 4.1 `.env` — 环境变量

**文件**：`pipecat/.env`

模板见 `pipecat/.env.example`。关键变量：

| 变量 | 默认 | 说明 |
|---|---|---|
| `LLM_BASE_URL` | `http://serverhome.tail2e6efb.ts.net/litellm/headroom/v1/` | OpenAI-compatible 端点 |
| `LLM_API_KEY` | `***` | 实际 key（Headroom 注入，此处占位即可） |
| `LLM_MODEL` | `minimax` | 模型名 |
| `WHISPER_MODEL_SIZE` | `small` | tiny/base/small/medium/large-v3 |
| `TTS_VOICE` | `zh-CN-XiaoxiaoNeural` | Edge TTS 音色 |
| `PORT` | `8765` | HTTP 端口 |

### 4.2 `src/services/whisper_stt.py` — Whisper STT

**文件**：`pipecat/src/services/whisper_stt.py`

封装 `faster-whisper` 的 `WhisperModel` 为 Pipecat `STTService` 子类。读取该文件获取实现。

### 4.3 `src/services/edge_tts.py` — Edge TTS

**文件**：`pipecat/src/services/edge_tts.py`

封装 `edge-tts` 异步流式 API + `ffmpeg` MP3→PCM 解码。读取该文件获取实现。

### 4.4 `src/pipeline.py` — Pipeline 构建

**文件**：`pipecat/src/pipeline.py`

使用 **Pipecat 1.4.0 新 API**：
- `Pipeline` + `PipelineWorker` + `WorkerRunner`（不是已废弃的 `PipelineRunner`）
- `LLMContext` + `LLMContextAggregatorPair` 做上下文聚合
- `SileroVADAnalyzer` 本地 VAD
- `OpenAILLMService` 指向自定义 `base_url`（接 Headroom/LiteLLM）

读取该文件获取实现。

### 4.5 `src/bot.py` — WebSocket 服务器

**文件**：`pipecat/src/bot.py`

FastAPI WebSocket 服务器，路由：

| 路径 | 类型 | 功能 |
|---|---|---|
| `/` | GET | 内联测试页面（HTML + JS，浏览器 mic 拾音） |
| `/conversation` | WebSocket | binary 帧：16-bit 16kHz mono PCM 双向流 |

读取该文件获取实现。

### 4.6 `src/serializers/pcm.py` — WebSocket 帧转换

**文件**：`pipecat/src/serializers/pcm.py`

`FrameSerializer` 子类，转换规则：
- WebSocket binary bytes → `InputAudioRawFrame`（送入 pipeline）
- `OutputAudioRawFrame` → WebSocket binary bytes（送回客户端）
- 文本 JSON `{"type":"text","data":"..."}` → `TextFrame`（可选调试用）

读取该文件获取实现。

### 4.7 `run.sh` — 启动脚本

**文件**：`pipecat/run.sh`

要点：
- 自动定位 uv（检查 `~/.hermes/bin/uv`, `~/.local/bin/uv`, `~/.cargo/bin/uv`）
- 设置 `WHISPER_CACHE_DIR`, `SILERO_CACHE_DIR`, `XDG_CACHE_HOME` 全部指向 `./cache/`
- 用 `uv run --project .` 执行（自动激活 venv，无需 `source activate`）

读取该文件获取实现。

### 4.8 `.gitignore`

```
.venv/
cache/
__pycache__/
*.pyc
*.pyo
.env
*.lock
.DS_Store
.archive/
```

---

## 五、环境隔离规则（必须遵守）

1. **永远不**用 `source .venv/bin/activate` —— 会污染 Shell 状态
2. **永远**用 `uv run --project . python -m src.bot` —— uv 自动管 venv
3. **永远不**装包到系统 Python —— `apt` 装的 pip 不存在（PEP 668）
4. **所有缓存**限制在 `./cache/` 下（whisper 模型、silero ONNX、NLTK）
5. **venv 完全隔离** —— 不复用 Hermes 的 Python 环境

---

## 六、验证清单

跑通后逐项验证：

```bash
cd ~/workspace/pipecat

# 1. 依赖完整（不会缺包）
uv run --project . python -c "
from src.bot import app
from src.pipeline import build_pipeline
from src.services.edge_tts import EdgeTTSService
from src.services.whisper_stt import WhisperSTTService
from src.serializers.pcm import RawPCMSerializer
print('IMPORTS OK')
"

# 2. 启动服务
./run.sh
# 期望输出: Uvicorn running on http://0.0.0.0:8765

# 3. HTTP 服务
curl -sS -o /dev/null -w "HTTP %{http_code}\n" http://localhost:8765/
# 期望: HTTP 200

# 4. WebSocket
python3 -c "
import asyncio, websockets
async def t():
    async with websockets.connect('ws://localhost:8765/conversation') as ws:
        print('WS OK')
        await ws.send('{\"type\":\"text\",\"data\":\"hi\"}')
        resp = await asyncio.wait_for(ws.recv(), timeout=10)
        print(f'Audio response: {len(resp)} bytes')
asyncio.run(t())
"
# 期望: WS OK + Audio response: 1920+ bytes

# 5. 浏览器实测
xdg-open http://localhost:8765/
# → 点连接 → 允许麦克风 → 说话 → 听 bot 回复
```

---

## 七、已知问题

| 问题 | 状态 | 解决方案 |
|---|---|---|
| 官方 `SmallWebRTCPrebuiltUI` 报 `RTVI Not Found` | 已知 | 缺 `pipecat-ai-rtvi-bot-client` 包 + RTVI handlers。下阶段再做 |
| `local` transport 编译失败 | 跳过 | 用 FastAPI WebSocket 替代，已够用 |
| 浏览器必须用 HTTPS 或 localhost 才能 getUserMedia | 已知 | 服务跑在 `localhost:8765`，不跨域访问 |
| Whisper 首次下载 ~950MB | 已知 | 缓存到 `./cache/whisper/`，下次秒启 |

---

## 八、一键部署

完整从零到运行的脚本（基于上面所有步骤）：

```bash
set -e
mkdir -p ~/workspace/pipecat && cd ~/workspace/pipecat

uv init
uv add "pipecat-ai[whisper,webrtc]" edge-tts fastapi uvicorn python-dotenv

mkdir -p src/services src/serializers

# 创建所有源码文件 —— 见 DEPLOY.md §4 各文件路径引用
# 读取对应文件内容并写入

chmod +x run.sh
./run.sh
```

详细代码请**直接 `read_file` 仓库内对应文件**获取最新实现。

---

## 九、当前实际部署位置（参考）

- **项目根目录**：`/home/dr/workspace/pipecat/`
- **uv 路径**：`/home/dr/.hermes/bin/uv`
- **Python**：`/home/dr/workspace/pipecat/.venv/bin/python` (3.11.15)
- **GitHub**：`https://github.com/yansinan/pipecat.server`
- **端口**：`8765`
- **LLM 端点**：`http://serverhome.tail2e6efb.ts.net/litellm/headroom/v1/`
- **模型**：`minimax`
- **当前进程 PID**（最近）：`969063`

---

## 十、下阶段：SmallWebRTC + 官方 PrebuiltUI

当前用 WebSocket + PCM 自建客户端（已跑通）。下一阶段切到官方路线：

1. 装 `pipecat-ai-small-webrtc-prebuilt`（已在 .venv）
2. 装 `pipecat-ai-rtvi-bot-client`（**待装**）
3. 注册 RTVI action handlers（describe-actions, start-bot 等）
4. 用 `SmallWebRTCTransport` + `SmallWebRTCRequestHandler` 替换 FastAPI WebSocket
5. 客户端用官方 PrebuiltUI（`/client/` 路由）

参考官方 example：`pipecat/examples/transports/transports-small-webrtc.py`
（已归档在 `.archive/bot_official_backup.py`，作为起点参考）