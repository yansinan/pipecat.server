"""
Headroom/DeepSeek LLM Service — 处理 reasoning_content 的 OpenAILLMService 子类。

问题：
  Headroom/LiteLLM 代理透传 DeepSeek 的推理内容在 reasoning_content 字段。
  pipecat 的 OpenAILLMService 只读 delta.content，导致 reasoning_content 被丢弃。

解法：
  继承 OpenAILLMService，重写 get_chat_completions 方法。
  拦截返回 stream 中的 reasoning_content chunk，发射 LLMThought*Frame。
  原始 chunk 继续 yield 给父类 _process_context，不影响 content 输出。

参考：pipecat 官方 NvidiaLLMService（src/pipecat/services/nvidia/llm.py）。
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from openai.types.chat import ChatCompletionChunk

from pipecat.frames.frames import LLMThoughtEndFrame, LLMThoughtStartFrame, LLMThoughtTextFrame
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.services.openai.llm import OpenAILLMService


class HeadroomLLMService(OpenAILLMService):
    """OpenAILLMService 子类，支持 Headroom 代理透传的 reasoning_content。

    重写 get_chat_completions，用 _handle_reasoning 包装原始 stream，
    遇到 reasoning_content 时发射 LLMThought*Frame，不送 TTS。
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._has_reasoning = False

    async def get_chat_completions(
        self, context: LLMContext
    ) -> AsyncIterator[ChatCompletionChunk]:
        """包装父类的 stream，拦截 reasoning_content。"""
        stream = await super().get_chat_completions(context)
        return self._handle_reasoning(stream)

    async def _close_inner_stream(
        self, stream: AsyncIterator[ChatCompletionChunk]
    ) -> None:
        """主动释放底层 OpenAI HTTP stream。

        OpenAI Python SDK 对外暴露 close()。主动关闭避免以下问题：
        - httpx 连接泄漏
        - Python 3.12+ uvloop 因 asyncgen 未正确关闭而段错误
        """
        close = getattr(stream, "close", None)
        if close:
            await close()
        aclose = getattr(stream, "aclose", None)
        if aclose:
            await aclose()

    async def _handle_reasoning(
        self, stream: AsyncIterator[ChatCompletionChunk]
    ) -> AsyncIterator[ChatCompletionChunk]:
        """遍历 stream，拦截 reasoning_content 并发射 LLMThought*Frame。

        reasoning 字段别名：
          reasoning_content — DeepSeek 官方 API、OpenAI o-series
          reasoning        — 部分 Headroom 旧版本、某些兼容层

        Yields:
          原始 chunk 给父类 _process_context，不影响 content 输出。
        """
        completed = False
        try:
            async for chunk in stream:
                if chunk.choices and len(chunk.choices) > 0 and chunk.choices[0].delta:
                    delta = chunk.choices[0].delta
                    # 兼容两种字段名
                    rc = getattr(delta, "reasoning_content", None) or getattr(
                        delta, "reasoning", None
                    )
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
            # 正常完成 → flush；提前中断 → 不 flush，直接关
            if self._has_reasoning and completed:
                await self.push_frame(LLMThoughtEndFrame())
                self._has_reasoning = False
            # 主动释放 HTTP 连接（即使被打断也要执行）
            await self._close_inner_stream(stream)
