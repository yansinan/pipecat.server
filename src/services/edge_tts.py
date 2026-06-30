"""
Edge TTS Service for Pipecat.

Custom TTSService subclass that wraps the edge-tts library
(https://github.com/rany2/edge-tts) for Microsoft Edge's online TTS.
"""

from __future__ import annotations

import asyncio
import io
import os
import subprocess
from dataclasses import dataclass, field
from typing import AsyncGenerator

from pipecat.frames.frames import (
    Frame,
    TTSAudioRawFrame,
    TTSStartedFrame,
    TTSStoppedFrame,
)
from pipecat.services.tts_service import TTSService, TTSSettings


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------

@dataclass
class EdgeTTSSettings(TTSSettings):
    """Runtime-updatable settings for EdgeTTSService."""

    model: str | None = None
    language: str | None = None
    voice: str = "zh-CN-XiaoxiaoNeural"
    rate: str = "+0%"
    volume: str = "+0%"
    pitch: str = "+0Hz"
    # Target output format (ffmpeg decodes Edge's mp3 to this)
    sample_rate: int = 16000
    channels: int = 1


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------

class EdgeTTSService(TTSService):
    """Text-to-speech service using Microsoft Edge's online TTS (edge-tts).

    The ``edge-tts`` library streams MP3 audio at 24 kHz. This service
    decodes the stream to 16 kHz mono PCM on the fly via an ffmpeg
    subprocess pipe.
    """

    def __init__(
        self,
        *,
        voice: str | None = None,
        rate: str | None = None,
        volume: str | None = None,
        pitch: str | None = None,
        sample_rate: int = 16000,
        channels: int = 1,
        settings: EdgeTTSSettings | None = None,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self._settings = settings or EdgeTTSSettings(
            voice=voice or "zh-CN-XiaoxiaoNeural",
            rate=rate or "+0%",
            volume=volume or "+0%",
            pitch=pitch or "+0Hz",
            sample_rate=sample_rate,
            channels=channels,
        )
        self._sample_rate = self._settings.sample_rate
        self._channels = self._settings.channels

    # ------------------------------------------------------------------
    # TTSService required override
    # ------------------------------------------------------------------

    async def run_tts(
        self, text: str, context_id: str
    ) -> AsyncGenerator[Frame | None, None]:
        """Synthesise *text* via Edge TTS, yielding audio frames.

        Parameters
        ----------
        text :
            The text to speak.
        context_id :
            Opaque audio-context identifier managed by the base class.

        Yields
        ------
        TTSStartedFrame
            Sent once before audio begins.
        TTSAudioRawFrame
            One or more frames with 16-bit 16 kHz mono PCM data.
        TTSStoppedFrame
            Sent after the last audio frame.
        """
        import edge_tts

        communicate = edge_tts.Communicate(
            text,
            voice=self._settings.voice,
            rate=self._settings.rate,
            volume=self._settings.volume,
            pitch=self._settings.pitch,
        )

        yield TTSStartedFrame()

        # --- Stream MP3 chunks through ffmpeg to get PCM ---
        # Edge outputs 24 kHz mono MP3.  We use ffmpeg in a pipe to
        # decode and resample on the fly.
        ffmpeg_cmd = [
            "ffmpeg",
            "-loglevel", "error",
            "-i", "pipe:0",           # read MP3 from stdin
            "-f", "s16le",            # raw signed 16-bit little-endian PCM
            "-acodec", "pcm_s16le",
            "-ar", str(self._sample_rate),
            "-ac", str(self._channels),
            "pipe:1",                 # write PCM to stdout
        ]

        # Buffer full mp3 for decoding (simpler than feeding chunk-by-chunk
        # and avoids ffmpeg flushing issues with very short segments).
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

        # --- Decode via ffmpeg ---
        try:
            proc = await asyncio.create_subprocess_exec(
                *ffmpeg_cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )

            pcm_data, stderr_data = await proc.communicate(mp3_data)

            if proc.returncode != 0:
                self._logger.error(
                    "ffmpeg decode failed (rc=%d): %s",
                    proc.returncode,
                    stderr_data.decode(errors="replace"),
                )
                yield TTSStoppedFrame()
                return

            # Yield PCM in ~20 ms frames (~640 bytes @ 16 kHz mono)
            frame_size = self._sample_rate * self._channels * 2 // 50  # 20 ms
            for offset in range(0, len(pcm_data), frame_size):
                frame_bytes = pcm_data[offset : offset + frame_size]
                yield TTSAudioRawFrame(
                    audio=frame_bytes,
                    sample_rate=self._sample_rate,
                    num_channels=self._channels,
                )
        except FileNotFoundError:
            self._logger.error("ffmpeg not found – install it or use a different TTS backend")
        except Exception as exc:
            self._logger.error("ffmpeg decode error: %s", exc)
        finally:
            yield TTSStoppedFrame()

    # ------------------------------------------------------------------
    # Public helpers
    # ------------------------------------------------------------------

    def can_generate_metrics(self) -> bool:
        return True
