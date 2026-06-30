# Cloud Browser DTLS 握手竞态 + SmallWebRTC ping 窗口

## 症状

- 浏览器已连接（Disconnect 按钮可见, Bot ready 事件触发）
- 发送文字后 server log: `Client not connected. Queuing app-message.`
- LLM 永不处理文字输入
- `connectionState` 一直卡在 "connecting" 不变成 "connected"

## 根因 1: aiortc DTLS 握手在 Cloud 浏览器环境不完成

Cloud 浏览器（Browserbase/Chrome headless）中 WebRTC 的 DTLS 握手可能不完成。

### 证据

```
22:29:28.334 | ICE connection state is checking,  connection is connecting
22:29:28.406 | ICE connection state is completed, connection is connecting
                                                          ^^^^^^^^^^ 没变！
```

`iceConnectionState = "completed"`（ICE 协商成功），但 `connectionState = "connecting"`（DTLS 握手未完成）。
SmallWebRTCConnection 未触发 "connected" 事件，pipeline 无法正常启动。

## 根因 2: `is_connected()` 有 3 秒 ping 窗口（connection.py:656-672）

```python
def is_connected(self) -> bool:
    if not self._connect_invoked:
        return False
    if self._last_received_time is None:
        return self._pc.connectionState == "connected"  # ← "connected" 但实际是 "connecting"
    return (time.time() - self._last_received_time) < 3  # ← 3秒窗口！
```

`_last_received_time` 被 ping 消息更新后，`is_connected()` 只看 3 秒 ping 窗口，
不再检查真实连接状态。

## 修正

```python
# server_prebuilt.py _run_pipeline 中:
async def _run_pipeline(connection: SmallWebRTCConnection, session_id: str):
    # ... 构建 transport, pipeline, worker, runner ...

    runner = WorkerRunner()
    await runner.add_workers(worker)
    logger.info(f"[{session_id}] pipeline starting")

    # ⭐ 手动刷新 pending app-messages（DTLS 握手可能未完成）
    pending = getattr(connection, "_pending_app_messages", [])
    if pending:
        logger.info(f"[{session_id}] flushing {len(pending)} queued app-messages")
        for msg in list(pending):
            await connection._call_event_handler("app-message", msg)
        pending.clear()

    try:
        await runner.run(auto_end=False)
    # ...
```

## 相关源码位置

| 文件 | 行 | 内容 |
|---|---|---|
| `smallwebrtc/connection.py` | 656-672 | `is_connected()` 3秒 ping 窗口 |
| `smallwebrtc/connection.py` | 341-356 | `on_message` 排队逻辑 |
| `smallwebrtc/connection.py` | 413-425 | `connect()` 刷新 pending messages |
| `smallwebrtc/transport.py` | 823-832 | `SmallWebRTCOutputTransport.start()` 调 connect |
