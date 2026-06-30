# 前后端同端口服务 — 可行性报告

## 现状

| 组件 | 端口 | 技术栈 |
|---|---|---|
| JS 前端 (Vite dev server) | 5173 | Vite + vanilla JS + @pipecat-ai/client-js |
| Bot 服务端 (FastAPI) | 7860 | FastAPI + pipecat + SmallWebRTC |

当前两个端口通过 CORS 跨域通信。问题是多端口部署和访问不直观。

## 方案：生产构建 + FastAPI 静态托管（推荐）

```
Vite 源码                            npm run build
  src/app.js                              ↓
  src/config.js                     client/dist/
  src/style.css                      index.html
  index.html                         assets/*.js
  vite.config.js                     assets/*.css
       │                                  │
       │ (开发：Vite dev :5173)             │ (生产：FastAPI :7860)
       ▼                                  ▼
  Vite dev server                  FastAPI StaticFiles
  hot reload                       快 10 倍、无 node 依赖
```

### 实施步骤

```bash
cd client/javascript
npm install              # 安装依赖（只需一次）
npm run build            # 构建 → client/javascript/dist/
```

`dist/` 目录结构：

```
client/javascript/dist/
  index.html
  assets/
    index-xxxx.js        # 压缩后的 JS
    index-xxxx.css       # 压缩后的 CSS
```

在 `bot_js_client.py` 里加一行：

```python
from fastapi.staticfiles import StaticFiles
app.mount("/", StaticFiles(directory="client/javascript/dist", html=True), name="client")
```

### 关键改动

| 改动项 | 原因 |
|---|---|
| `VITE_BOT_START_URL=/start`（相对路径） | 不再写死 `http://localhost:7860/start`，同端口时直接用相对路径 |
| FastAPI 路由顺序：API 端点先注册，`StaticFiles` 最后 mount | 避免 StaticFiles 拦截 API 请求（如 `/start` 返回 HTML 而非 JSON） |
| `app.mount("/", ..., html=True)` | `html=True` 让 FastAPI 处理 SPA 路由（非 `/api/*` 的路径返回 `index.html`） |

### 路由顺序

```python
# 1. API 路由优先（FastAPI 根据注册顺序匹配）
app.post("/start")                     # → 返回 ICE config
app.post("/sessions/{id}/api/offer")  # → SDP Offer
app.patch("/sessions/{id}/api/offer") # → ICE candidates
app.post("/inject_test_audio")        # → 测试音频

# 2. 静态文件最后（兜底匹配）
app.mount("/", StaticFiles(...))       # → index.html
```

### 开发体验不变

| 场景 | 前端地址 | 后端地址 | 端口 |
|---|---|---|---|
| 开发 | `localhost:5173` (Vite HMR) | `localhost:7860` | 2 个 |
| 生产 | `localhost:7860` (FastAPI 托管) | 同端口 | **1 个** |

开发时不 build，继续用 Vite dev server + 代理转发。CORS 已经配好了。

## 为什么不选其他方案

### 方案 B：反向代理（nginx/caddy）

```
客户端 → nginx :80
           ├─ / → Vite :5173 (或 dist/ 静态文件)
           └─ /api/* → FastAPI :7860
```

优点：标准做法，负载均衡灵活
缺点：多一个进程要维护 —— 对单机演示来说太重

### 方案 C：开发也用 FastAPI 托管 dist/

每次改 JS 都要 `npm run build`（~5 秒），才能看到效果。Vite HMR 是毫秒级的，开发体验差很多。

结论：**生产构建 + FastAPI 静态托管** 是成本和收益最平衡的方案。

## 可行性评估

| 项 | 结论 |
|---|---|
| **工作量** | 4 行代码改动（`bot_js_client.py`）+ 1 次 `npm run build` |
| **风险** | 低。`StaticFiles` mount 顺序只影响不存在的路由 |
| **兼容性** | 现有 Vite dev 流程完全不变 |
| **依赖** | 无新增 Python 依赖，node 只用于 build（可 CI） |
| **端口数** | 生产 1 个（7860），开发 2 个不变 |

## 当前进展

前端源码已复制到 `pipecat/client/javascript/`，与 `pipecat-examples/` 中的外部仓库分离。
之前对 `pipecat-examples/` 的改动也一并 copy 过来（自动连接、测试按钮、Events 面板）。

要执行这个方案吗？
