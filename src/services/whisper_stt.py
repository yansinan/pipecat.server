"""
Custom Whisper STT Service for Pipecat.

Wraps faster-whisper directly as a Pipecat STTService because the
compiled pipecat-ai[whisper] STT Cython module is not available in
the installed wheel for this platform.
"""

from __future__ import annotations

import os
import time
from typing import AsyncGenerator

from pipecat.frames.frames import (
    Frame,
    TranscriptionFrame,
    InterimTranscriptionFrame,
)
from pipecat.services.stt_service import SegmentedSTTService


class WhisperSTTService(SegmentedSTTService):
    """Speech-to-text using faster-whisper (local Whisper model).

    Downloads the model on first use and caches it in ``./cache/whisper/``
    (or ``WHISPER_CACHE_DIR`` env var).
    """

    def __init__(
        self,
        *,
        model_size: str = "small",
        cache_dir: str | None = None,
        device: str = "cpu",
        compute_type: str = "default",
        **kwargs,
    ):
        super().__init__(**kwargs)
        self._model_size = model_size
        self._cache_dir = cache_dir or os.environ.get(
            "WHISPER_CACHE_DIR", "./cache/whisper"
        )
        self._device = device
        self._compute_type = compute_type
        self._model = None

    def _load_model(self):
        if self._model is None:
            from faster_whisper import WhisperModel

            os.makedirs(self._cache_dir, exist_ok=True)
            self._model = WhisperModel(
                self._model_size,
                device=self._device,
                compute_type=self._compute_type,
                download_root=self._cache_dir,
            )

    async def run_stt(
        self, audio: bytes
    ) -> AsyncGenerator[Frame | None, None]:
        """Transcribe *audio* (16-bit 16kHz mono PCM) with Whisper."""
        from loguru import logger
        import traceback
        logger.info(f"[WHISPER-STT] run_stt called with {len(audio)}B audio from {traceback.extract_stack()[-3].name}")
        self._load_model()

        import io
        import wave

        # Write audio bytes as a WAV in-memory so faster-whisper can read it.
        wav_buffer = io.BytesIO()
        with wave.open(wav_buffer, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(16000)
            wf.writeframes(audio)
        wav_buffer.seek(0)

        segments, info = self._model.transcribe(
            wav_buffer,
            beam_size=5,
            language="zh",
            vad_filter=True,
        )

        full_text = ""
        for seg in segments:
            if seg.text.strip():
                yield InterimTranscriptionFrame(
                    text=seg.text,
                    user_id="user",
                    timestamp=str(time.time()),
                )
            full_text += seg.text

        if full_text.strip():
            yield TranscriptionFrame(
                text=full_text.strip(),
                user_id="user",
                timestamp=str(time.time()),
            )
        else:
            yield None

    def can_generate_metrics(self) -> bool:
        return True
