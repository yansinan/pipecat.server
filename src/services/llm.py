"""
Headroom/DeepSeek LLM Service — 处理 reasoning_content 的 OpenAILLMService 子类。

问题：
  Headroom/LiteLLM 代理透传 DeepSeek 的推理内容在 reasoning_content 字段。
  pipecat 的 OpenAILLMService 只读 delta.content，导致 reasoning_content 被丢弃。

解法：
  继承 OpenAILLMService，重写 get_chat_completions 方法。
  拦截返回 stream 中的 reasoning_content chunk，发射 LLMThought*Frame。
  原始 chunk 继续 yield 给父类 _process_context，不影响 content 输出。

参考 pipecat 官方 NvidiaLLMService 的相同做法。
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

    async def _handle_reasoning(
        self, stream: AsyncIterator[ChatCompletionChunk]
    ) -> AsyncIterator[ChatCompletionChunk]:
        """遍历 stream，拦截 reasoning_content 并发射 LLMThought*Frame。"""
        try:
            async for chunk in stream:
                if chunk.choices and len(chunk.choices) > 0 and chunk.choices[0].delta:
                    delta = chunk.choices[0].delta
                    rc = getattr(delta, "reasoning_content", None)
                    if rc:
                        if not self._has_reasoning:
                            self._has_reasoning = True
                            await self.push_frame(LLMThoughtStartFrame())
                        await self.push_frame(LLMThoughtTextFrame(text=rc))
                    elif self._has_reasoning and delta.content:
                        await self.push_frame(LLMThoughtEndFrame())
                        self._has_reasoning = False
                yield chunk
        finally:
            if self._has_reasoning:
                await self.push_frame(LLMThoughtEndFrame())
