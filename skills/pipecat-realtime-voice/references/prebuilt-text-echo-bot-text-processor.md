# PrebuiltUI Text Echo — BotTextProcessor (已废弃)

> **2026-06-30 废弃**: 此方案被用户否决。不要用自定义 FrameProcessor。
> 正确机制见 `references/prebuiltui-bot-output-mechanism.md`。
> - RTVIObserver 内置了 BotOutput 处理
> - PrebuiltUI 渲染对话走的 BotOutput 事件，不是 bot-transcription
> - `_check_started` 检查 `self.__started`（name-mangled）而非 `self._started`（历史遗留，不再需要）

## 旧方案记录（供参考）

[原有内容保持不变 — BotTextProcessor 实现细节、pipeline 位置、check_started 问题等]
