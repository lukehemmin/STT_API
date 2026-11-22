"""faster-whisper 래퍼.

- 모델은 첫 사용 시 지연 로드한다(Colab/자체 호스팅 공통).
- 한 프로세스에 모델 1개를 두고 추론을 직렬화한다. 프로토타입 기준이며,
  동시 처리 확장은 plan/02-architecture.md 의 워커 풀 설계를 따른다.
"""

from __future__ import annotations

import math
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, List, Optional, Union

import numpy as np

from .audio import SAMPLE_RATE
from .config import settings


@dataclass
class Segment:
    start_ms: int
    end_ms: int
    text: str
    confidence: Optional[float] = None
    words: List[dict] = field(default_factory=list)

    def as_dict(self) -> dict:
        data = {
            "start_ms": self.start_ms,
            "end_ms": self.end_ms,
            "text": self.text,
            "confidence": self.confidence,
        }
        if self.words:
            data["words"] = self.words
        return data


@dataclass
class Transcript:
    text: str
    segments: List[Segment]
    language: Optional[str] = None
    language_probability: Optional[float] = None
    duration_ms: int = 0
    elapsed_ms: int = 0
    device: Optional[str] = None
    compute_type: Optional[str] = None

    @property
    def rtf(self) -> Optional[float]:
        if not self.duration_ms:
            return None
        return round(self.elapsed_ms / self.duration_ms, 4)


ProgressFn = Callable[[float, Optional[Segment]], None]


class WhisperEngine:
    def __init__(
        self,
        model_name: Optional[str] = None,
        device: Optional[str] = None,
        compute_type: Optional[str] = None,
        download_root: Optional[str] = None,
    ) -> None:
        self.model_name = model_name or settings.model
        self.device = device or settings.device
        self.compute_type = compute_type or settings.compute_type
        self.download_root = download_root if download_root is not None else settings.model_dir
        self.language = settings.language
        self._model = None
        self._resolved_device: Optional[str] = None
        self._resolved_compute: Optional[str] = None
        self._load_lock = threading.Lock()
        self._infer_lock = threading.Lock()  # 모델 1개에 대한 추론 직렬화

    # -- 로드 -----------------------------------------------------------
    @property
    def loaded(self) -> bool:
        return self._model is not None

    def _pick_device(self) -> str:
        if self.device != "auto":
            return self.device
        try:
            import ctranslate2

            return "cuda" if ctranslate2.get_cuda_device_count() > 0 else "cpu"
        except Exception:
            return "cpu"

    def load(self) -> "WhisperEngine":
        if self._model is not None:
            return self
        with self._load_lock:
            if self._model is not None:
                return self
            from faster_whisper import WhisperModel

            device = self._pick_device()
            compute = self.compute_type
            if compute == "auto":
                compute = "float16" if device == "cuda" else "int8"
            self._model = WhisperModel(
                self.model_name,
                device=device,
                compute_type=compute,
                download_root=self.download_root,
            )
            self._resolved_device = device
            self._resolved_compute = compute
        return self

    def info(self) -> dict:
        return {
            "model": self.model_name,
            "loaded": self.loaded,
            "device": self._resolved_device or ("auto" if self.device == "auto" else self.device),
            "compute_type": self._resolved_compute or self.compute_type,
            "language": self.language,
        }

    # -- 추론 -----------------------------------------------------------
    def _transcribe(
        self,
        source: Union[str, np.ndarray],
        language: Optional[str],
        word_timestamps: bool,
        on_progress: Optional[ProgressFn],
        fallback_duration_s: float,
    ) -> Transcript:
        self.load()
        started = time.perf_counter()
        segments: List[Segment] = []
        info = None
        with self._infer_lock:
            try:
                segments_iter, info = self._model.transcribe(
                    source,
                    language=(language or self.language or None),
                    beam_size=5,
                    vad_filter=True,
                    word_timestamps=word_timestamps,
                    condition_on_previous_text=False,
                )
                duration = float(getattr(info, "duration", 0.0) or 0.0)
                if duration <= 0:
                    duration = fallback_duration_s
                for raw in segments_iter:
                    text = (raw.text or "").strip()
                    if duration > 0 and on_progress is not None:
                        on_progress(min(0.99, max(0.0, raw.end / duration)), None)  # type: ignore[arg-type]
                    if not text:
                        continue
                    confidence = None
                    if getattr(raw, "avg_logprob", None) is not None:
                        confidence = round(min(1.0, max(0.0, math.exp(raw.avg_logprob))), 3)
                    words: List[dict] = []
                    if word_timestamps and getattr(raw, "words", None):
                        for w in raw.words:
                            word = (w.word or "").strip()
                            if not word:
                                continue
                            words.append(
                                {
                                    "word": word,
                                    "start_ms": int(round(w.start * 1000)),
                                    "end_ms": int(round(w.end * 1000)),
                                    "probability": round(float(w.probability), 3),
                                }
                            )
                    segments.append(
                        Segment(
                            start_ms=int(round(raw.start * 1000)),
                            end_ms=int(round(raw.end * 1000)),
                            text=text,
                            confidence=confidence,
                            words=words,
                        )
                    )
            finally:
                elapsed_ms = int((time.perf_counter() - started) * 1000)

        if duration <= 0 and segments:
            duration = segments[-1].end_ms / 1000.0
        return Transcript(
            text="".join(s.text for s in segments).strip(),
            segments=segments,
            language=getattr(info, "language", None),
            language_probability=getattr(info, "language_probability", None),
            duration_ms=int(round(duration * 1000)),
            elapsed_ms=elapsed_ms,
            device=self._resolved_device,
            compute_type=self._resolved_compute,
        )

    def transcribe_file(
        self,
        path: Union[str, Path],
        language: Optional[str] = None,
        word_timestamps: bool = False,
        on_progress: Optional[ProgressFn] = None,
    ) -> Transcript:
        return self._transcribe(str(path), language, word_timestamps, on_progress, 0.0)

    def transcribe_audio(
        self,
        audio: np.ndarray,
        language: Optional[str] = None,
        word_timestamps: bool = False,
        on_progress: Optional[ProgressFn] = None,
    ) -> Transcript:
        audio = np.ascontiguousarray(audio, dtype=np.float32)
        duration = audio.size / SAMPLE_RATE
        return self._transcribe(audio, language, word_timestamps, on_progress, duration)


engine = WhisperEngine()
