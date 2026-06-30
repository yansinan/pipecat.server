---
name: pipecat-voice-agent-project
description: "Pipecat 语音机器人项目实践经验总结 — HeadroomLLMService、代码复用抽取、单端口生产构建、测试音频注入、调试方法论。在对此项目做任何修改前加载此技能，尤其注意文件结构（src/core/webrtc_server.py 共享模块）、端口规则（bot_js_client :8765, PrebuiltUI :8766）、重启脚本（restart_webrtc_client.sh）"
version: 1.0.0
platforms: [linux]
metadata:
  hermes:
    tags: [pipecat, voice-agent, webrtc, smallwebrtc, headroom, litellm, deepseek]
    related_skills: [pipecat-realtime-voice, pipecat-server-deployment, pipecat-voice-agent]
---

# Pipecat Voice Agent Project Experience

## Project structure

```
src/
  bot_js_client.py          入口（生产前端 + SmallWebRTC, :8765）
  server_prebuilt.py        入口（PrebuiltUI + SmallWebRTC, :8766）
  core/
    pipeline.py             pipeline 编排（被 webrtc_server 调用）
    webrtc_server.py        共享模块（handler 创建 / 路由注册 / pipeline 启动器工厂）
  helpers/
    test_audio.py           测试音频注入（独立可删）
  services/
    edge_tts.py             Edge TTS 封装
    llm.py                  HeadroomLLMService（处理 reasoning_content）
    whisper_stt.py          Whisper STT 封装
```

- `bot_js_client.py` 和 `server_prebuilt.py` 共用 `core/webrtc_server.py` 中的 `create_handler()` / `register_start_endpoint()` / `register_webrtc_endpoints()` / `make_run_pipeline()`
- `bot_js_client.py` 额外：CORS、静态文件 mount、测试音频端点
- `server_prebuilt.py` 额外：PrebuiltUI mount、pending app-messages hack

### Shared module pattern (core/webrtc_server.py)

```python
# webrtc_server.py exports:
create_handler()                                 # → SmallWebRTCRequestHandler
register_start_endpoint(app, session_id="default")  # POST /start
register_webrtc_endpoints(app, handler, run_pipeline) # SDP Offer + ICE
make_run_pipeline(on_client_ready=None, on_transport_created=None)  # → async _run_pipeline

# bot_js_client.py:
from src.core.webrtc_server import create_handler, make_run_pipeline, register_start_endpoint, register_webrtc_endpoints

handler = create_handler()
test_audio.set_handler(handler)

async def _setup_inject(transport, worker, conn):
    conn._inject_inbound = transport.input()

run_pipeline = make_run_pipeline(on_transport_created=_setup_inject)
register_start_endpoint(app)
register_webrtc_endpoints(app, handler, run_pipeline)

# server_prebuilt.py:
async def _on_client_ready(worker, context, connection):
    @worker.rtvi.event_handler("on_client_ready")
    async def ready(rtvi):
        context.add_message({"role": "system", "content": SYSTEM_PROMPT})
        await worker.queue_frames([LLMRunFrame()])

run_pipeline = make_run_pipeline(on_client_ready=_on_client_ready)
```

**关键原则**: 每个入口传自己需要的回调，不共享的代码不混入公共模块。

## HeadroomLLMService (src/services/llm.py)

**问题**: Headroom/LiteLLM 代理将 DeepSeek 推理内容放在 `delta.reasoning_content` 而非 `delta.content`。pipecat 的 `OpenAILLMService` 只读 `delta.content`。

**解法**: 继承 `OpenAILLMService`，覆写 `get_chat_completions`，拦截 `reasoning_content` 并发射 `LLMThought*Frame`（官方做法，参照 `NvidiaLLMService`）。

```python
class HeadroomLLMService(OpenAILLMService):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._has_reasoning = False

    async def get_chat_completions(self, context):
        stream = await super().get_chat_completions(context)
        return self._handle_reasoning(stream)

    async def _handle_reasoning(self, stream):
        completed = False
        try:
            async for chunk in stream:
                delta = chunk.choices[0].delta
                rc = getattr(delta, "reasoning_content", None) or getattr(delta, "reasoning", None)
                if rc:
                    if not self._has_reasoning:
                        self._has_reasoning = True
                        await self.push_frame(LLMThoughtStartFrame())
                    await self.push_frame(LLMThoughtTextFrame(text=rc))
                elif self._has_reasoning and delta.content:
                    await self.push_frame(LLMThoughtEndFrame())
                    self._has_reasoning = False
                yield chunk
            completed = True
        finally:
            if self._has_reasoning and completed:
                await self.push_frame(LLMThoughtEndFrame())
            await self._close_inner_stream(stream)
```

**三个关键保护:**
1. `reasoning` 别名 — 兼容 `reasoning_content`（DeepSeek）和 `reasoning`（旧版 Headroom）
2. `_close_inner_stream()` — 主动关闭 HTTP stream，预防 uvloop 段错误
3. `completed` 标记 — 正常完成才 flush，中断只关连接

**同时需要在 core/pipeline.py 中将 llm 类改为 HeadroomLLMService:**
```python
# Instead of OpenAILLMService:
from src.services.llm import HeadroomLLMService
llm = HeadroomLLMService(base_url=..., api_key=..., settings=OpenAILLMService.Settings(model=...))
```

**必须使用 HeadroomLLMService 当 LLM 后端是 Headroom/LiteLLM 代理时**。直接使用 OpenAILLMService 会导致 `reasoning_content` 被丢弃。

## 单端口生产构建

```bash
cd client/javascript
npm run build                     # → dist/
```

```python
# bot_js_client.py
from fastapi.staticfiles import StaticFiles

if os.path.isdir("client/javascript/dist"):
    app.mount("/", StaticFiles(directory="client/javascript/dist", html=True), name="client")
```

**路由顺序**: API 端点（POST /start, /sessions/{id}/...）必须先注册，StaticFiles 最后 mount。

**开发模式**: dist/ 不存在时，`GET /` → `FileResponse(dist/index.html)`；开发模式重定向到 Vite dev server。

**端口**: bot_js_client = :8765, PrebuiltUI = :8766, Vite dev = :8764

## 重启脚本 (restart_webrtc_client.sh)

```bash
# Features:
# 1. Kill old process on port 8765
# 2. Check if frontend is built (auto-build if not)
# 3. Load .env
# 4. Start bot_js_client

bash restart_webrtc_client.sh
```

**install.sh** — 首次部署（找 uv + venv + npm install + build）

## 测试音频注入方法论

### 原则
1. **用真实 TTS 语音，不用正弦波** — VAD 需要语音能量 (>5000) 才能检测
2. **走 `_audio_in_queue`**，不走 `push_frame` — 绕过 pipeline 队列积压
3. **不存在就报错** — 不静默降级，错误信息告诉用户缺什么

### 模块隔离模式 (src/helpers/test_audio.py)
- `TestAudioInjector` 类，不维护 session 注册表
- 通过 `handler._pcs_map` + `connection._inject_inbound` 查找目标
- 删除 = 删模块 + 删 2 处引用

## 关键教训

### 框架代码复用优先
**永远先搜框架**再写自定义实现:
```bash
grep -r "def process_frame\|class.*FrameProcessor" .venv/lib/python3.11/site-packages/pipecat/
grep -r "def handle_patch_request\|def handle_web_request" .venv/lib/python3.11/site-packages/pipecat/
```

已知框架提供但容易自己造的轮子:
| 你要的 | 框架已有 | 位置 |
|---|---|---|
| ICE candidate 处理 | `SmallWebRTCRequestHandler.handle_patch_request()` | request_handler.py |
| LLM 文本聚合 | `LLMTextProcessor` | processors/aggregators/ |
| SDP Offer 处理 | `SmallWebRTCRequestHandler.handle_web_request()` | request_handler.py |
| RTVI 观察者 | `RTVIObserver` | processors/frameworks/rtvi/ |

### 查官方资料再动手
遇到问题（如 reasoning_content 处理），先搜 pipecat 仓库的 issue / 代码，看官方怎么处理:
```bash
# 搜索框架源码
grep -r "reasoning_content" .venv/lib/python3.11/site-packages/pipecat/
# → 发现 NvidiaLLMService 已经处理了，直接参照
```

### 自验原则（硬性规则）
1. 改完必须自己验证（重启服务 + 完整流程），不能丢给用户测试
2. 三次要求用户测试 = 失去信任
3. 浏览器 CDP 工具不可靠时，写 standalone Python 脚本验证
4. 报告必须带实证（日志原文、HTTP 状态码、curl 输出）

### 不要加空壳函数
- 删了功能就一并删调用方和端点
- 不要 `def pop_events(): return []` 这种 stub
- 不要正弦波降级，不要静默 fallback

### session_id
- `POST /start` 返回的 `sessionId` — JS 协议必须，固定值即可（"default"）
- 内部代码不用 session_id——用 `connection.pc_id` 做日志标识
- 路由函数参数 `session_id` 只是 FastAPI URL 语法，业务代码不收不传

### 动态 host 重定向
```python
# ❌ 不要写死 localhost
return RedirectResponse(url="http://localhost:5173/")
# ✅ 用 request.url.hostname
redirect_url = f"{request.url.scheme}://{request.url.hostname}:8764/"
```

## 启动
```bash
bash restart_webrtc_client.sh     # bot_js_client :8765
uv run python -m src.bot_js_client  # 或直接
uv run python -m src.server_prebuilt  # PrebuiltUI :8766
```
