"""스트리밍 전사 세션.

에너지 기반 VAD로 발화 구간을 나누고, partial은 주기적으로 현재 버퍼를
재디코딩해 발행한다. 모델 추론은 Promise처럼 스레드에서 실행된다.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import numpy as np

from .audio import SAMPLE_RATE, ms_from_samples, pcm16_to_float32
from .engine import WhisperEngine
from .jobs import new_id

FRAME_SAMPLES = 512  # 32 ms @ 16 kHz


def _env_int(name: str, default: int) -> int:
    import os

    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    import os

    try:
        return float(os.getenv(name, str(default)))
    except ValueError:
        return default


@dataclass
class StreamSessionState:
    id: str
    status: str = "active"
    language: Optional[str] = None
    model: str = ""
    started_at: float = field(default_factory=time.time)
    ended_at: Optional[float] = None
    duration_ms: int = 0
    segments: List[dict] = field(default_factory=list)
    events: List[dict] = field(default_factory=list)
    error: Optional[dict] = None


class StreamingSession:
    def __init__(
        self,
        engine: WhisperEngine,
        language: Optional[str] = None,
        interim: bool = True,
        word_timestamps: bool = False,
    ) -> None:
        self.engine = engine
        self.language = language
        self.interim = interim
        self.word_timestamps = word_timestamps
        self.state = StreamSessionState(
            id=new_id("ss_"), language=language, model=engine.model_name
        )

        self.speech_rms = _env_float("STT_SPEECH_RMS", 0.008)
        self.silence_end_ms = _env_int("STT_SILENCE_END_MS", 600)
        self.min_segment_ms = _env_int("STT_MIN_SEGMENT_MS", 300)
        self.max_segment_ms = _env_int("STT_MAX_SEGMENT_MS", 15000)
        self.partial_interval_ms = _env_int("STT_PARTIAL_INTERVAL_MS", 700)
        self.preroll_ms = _env_int("STT_PREROLL_MS", 300)

        self._pending = np.zeros(0, dtype=np.float32)
        self._buffer: List[np.ndarray] = []
        self._buffer_samples = 0
        self._preroll = np.zeros(0, dtype=np.float32)
        self._speech = False
        self._silence_ms = 0
        self._total_ms = 0
        self._segment_start_ms = 0
        self._segment_index = 0
        self._revision = 0
        self._last_partial_at = 0.0

    # -- 상태 -----------------------------------------------------------
    @property
    def elapsed_ms(self) -> int:
        return self._total_ms

    def _buffer_ms(self) -> int:
        return ms_from_samples(self._buffer_samples)

    def _emit(self, event: Dict[str, Any]) -> Dict[str, Any]:
        event["seq"] = len(self.state.events) + 1
        self.state.events.append(event)
        if event["type"] == "final":
            self.state.segments.append(
                {
                    "segment_id": event["segment_id"],
                    "revision": event["revision"],
                    "text": event["text"],
                    "start_ms": event["start_ms"],
                    "end_ms": event["end_ms"],
                    "confidence": event.get("confidence"),
                }
            )
        self.state.duration_ms = self._total_ms
        return event

    # -- 입력 -----------------------------------------------------------
    async def feed(self, data: bytes) -> List[Dict[str, Any]]:
        audio = pcm16_to_float32(data)
        if audio.size == 0:
            return []
        self._total_ms += ms_from_samples(audio.size)
        self._pending = np.concatenate([self._pending, audio])
        events: List[Dict[str, Any]] = []
        while self._pending.size >= FRAME_SAMPLES:
            block = self._pending[:FRAME_SAMPLES]
            self._pending = self._pending[FRAME_SAMPLES:]
            events.extend(await self._process_block(block))
        return events

    async def _process_block(self, block: np.ndarray) -> List[Dict[str, Any]]:
        rms = float(np.sqrt(np.mean(np.square(block)))) if block.size else 0.0
        speaking = rms >= self.speech_rms
        events: List[Dict[str, Any]] = []

        if speaking:
            if not self._speech:
                self._speech = True
                self._silence_ms = 0
                self._revision = 0
                block_ms = ms_from_samples(block.size)
                self._segment_start_ms = max(
                    0, self._total_ms - block_ms - ms_from_samples(self._preroll.size)
                )
                parts = [p for p in (self._preroll, block) if p.size]
                self._buffer = parts
                self._buffer_samples = sum(p.size for p in parts)
            else:
                self._buffer.append(block)
                self._buffer_samples += block.size
                self._silence_ms = 0

            if self._buffer_ms() >= self.max_segment_ms:
                event = await self._finalize()
                if event:
                    events.append(event)
            else:
                event = await self._maybe_partial()
                if event:
                    events.append(event)
        elif self._speech:
            self._buffer.append(block)
            self._buffer_samples += block.size
            self._silence_ms += ms_from_samples(block.size)
            if self._silence_ms >= self.silence_end_ms:
                event = await self._finalize()
                if event:
                    events.append(event)
        else:
            keep = int(SAMPLE_RATE * self.preroll_ms / 1000)
            self._preroll = np.concatenate([self._preroll, block])[-keep:] if keep else block[:0]

        return events

    # -- 결과 -----------------------------------------------------------
    async def _maybe_partial(self) -> Optional[Dict[str, Any]]:
        if not self.interim or not self._speech or self._buffer_samples == 0:
            return None
        if self._buffer_ms() < self.min_segment_ms:
            return None
        now = time.monotonic()
        if (now - self._last_partial_at) * 1000 < self.partial_interval_ms:
            return None
        self._last_partial_at = now
        audio = np.concatenate(self._buffer)
        result = await asyncio.to_thread(
            self.engine.transcribe_audio, audio, self.language, False
        )
        if not result.text:
            return None
        self._revision += 1
        return self._emit(
            {
                "type": "partial",
                "segment_id": f"s-{self._segment_index}",
                "revision": self._revision,
                "text": result.text,
                "start_ms": self._segment_start_ms,
                "end_ms": self._segment_start_ms + self._buffer_ms(),
            }
        )

    async def _finalize(self) -> Optional[Dict[str, Any]]:
        if self._buffer_samples == 0:
            self._reset()
            return None
        audio = np.concatenate(self._buffer)
        if audio.size < int(SAMPLE_RATE * 0.1):
            self._reset()
            return None
        try:
            result = await asyncio.to_thread(
                self.engine.transcribe_audio, audio, self.language, self.word_timestamps
            )
        except Exception as exc:
            self._reset()
            return self._emit(
                {
                    "type": "error",
                    "code": "internal",
                    "message": f"{type(exc).__name__}: {exc}"[:300],
                    "retryable": True,
                }
            )
        start = self._segment_start_ms
        end = start + ms_from_samples(audio.size)
        confidence = result.segments[0].confidence if result.segments else None
        event = None
        if result.text:
            self._revision += 1
            event = self._emit(
                {
                    "type": "final",
                    "segment_id": f"s-{self._segment_index}",
                    "revision": self._revision,
                    "text": result.text,
                    "start_ms": start,
                    "end_ms": end,
                    "confidence": confidence,
                }
            )
            self._segment_index += 1
        keep = int(SAMPLE_RATE * self.preroll_ms / 1000)
        self._preroll = audio[-keep:].copy() if keep else audio[:0]
        self._reset(buffer_only=True)
        return event

    def _reset(self, buffer_only: bool = False) -> None:
        self._buffer = []
        self._buffer_samples = 0
        self._speech = False
        self._silence_ms = 0
        self._revision = 0
        if not buffer_only:
            self._preroll = np.zeros(0, dtype=np.float32)

    async def flush(self) -> Optional[Dict[str, Any]]:
        if not self._speech or self._buffer_samples == 0:
            return None
        return await self._finalize()

    async def finish(self) -> List[Dict[str, Any]]:
        events: List[Dict[str, Any]] = []
        event = await self._finalize()
        if event:
            events.append(event)
        done = self._emit(
            {
                "type": "done",
                "duration_ms": self._total_ms,
                "segments": len(self.state.segments),
            }
        )
        events.append(done)
        self.state.status = "completed"
        self.state.ended_at = time.time()
        self.state.duration_ms = self._total_ms
        return events

    def abort(self) -> None:
        if self.state.status == "active":
            self.state.status = "aborted"
            self.state.ended_at = time.time()
            self.state.duration_ms = self._total_ms
