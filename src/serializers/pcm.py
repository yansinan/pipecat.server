"""Raw-PCM serializer for FastAPI WebSocket transport."""
from __future__ import annotations
import json
from pipecat.frames.frames import Frame, InputAudioRawFrame, OutputAudioRawFrame, TextFrame
from pipecat.serializers.base_serializer import FrameSerializer

class RawPCMSerializer(FrameSerializer):
    SAMPLE_RATE = 16000
    NUM_CHANNELS = 1
    async def setup(self, frame): pass
    async def serialize(self, frame: Frame) -> str | bytes | None:
        if isinstance(frame, (OutputAudioRawFrame,)):
            return frame.audio
        if isinstance(frame, TextFrame):
            return json.dumps({"type": "text", "text": frame.text}).encode()
        return None
    async def deserialize(self, data: str | bytes) -> Frame | None:
        if isinstance(data, bytes) and len(data) > 0:
            return InputAudioRawFrame(audio=data, sample_rate=self.SAMPLE_RATE, num_channels=self.NUM_CHANNELS)
        if isinstance(data, str):
            try:
                msg = json.loads(data)
            except json.JSONDecodeError:
                return None
            if msg.get("type") == "text":
                return TextFrame(text=msg.get("text", ""))
        return None