"""배치 잡 저장소와 실행기 (인메모리 프로토타입).

계획의 PostgreSQL/Redis 구조를 대체하는 최소 구현이다.
서버 재시작 시 잡이 사라지며, 단일 모델 추론을 전제로 한다.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from .audio import AudioError, DependencyError, to_wav16k
from .config import settings
from .engine import WhisperEngine

TERMINAL_STATUSES = {"completed", "failed", "canceled"}


def new_id(prefix: str) -> str:
    return prefix + uuid.uuid4().hex[:12]


class Canceled(Exception):
    """잡 취소로 인한 중단."""


@dataclass
class Job:
    id: str
    mode: str = "batch"
    status: str = "queued"
    progress: float = 0.0
    language: Optional[str] = None
    detected_language: Optional[str] = None
    detected_language_probability: Optional[float] = None
    duration_ms: Optional[int] = None
    model: str = ""
    source_name: str = ""
    text: str = ""
    segments: List[dict] = field(default_factory=list)
    error: Optional[dict] = None
    events: List[dict] = field(default_factory=list)
    subscribers: Set[asyncio.Queue] = field(default_factory=set)
    created_at: float = field(default_factory=time.time)
    started_at: Optional[float] = None
    finished_at: Optional[float] = None
    canceled: bool = False
    task: Optional["asyncio.Task[None]"] = None


class JobStore:
    def __init__(self) -> None:
        self.jobs: Dict[str, Job] = {}
        self.stream_sessions: Dict[str, Any] = {}

    def create(self, source_name: str, language: Optional[str], model: str) -> Job:
        job = Job(id=new_id("tr_"), source_name=source_name, language=language, model=model)
        self.jobs[job.id] = job
        return job

    def get(self, job_id: str) -> Optional[Job]:
        return self.jobs.get(job_id)

    def active_count(self) -> int:
        return sum(1 for job in self.jobs.values() if job.status not in TERMINAL_STATUSES)


class BatchRunner:
    def __init__(self, engine: WhisperEngine, store: JobStore, work_dir: Path) -> None:
        self.engine = engine
        self.store = store
        self.work_dir = work_dir
        self.work_dir.mkdir(parents=True, exist_ok=True)
        self._sem = asyncio.Semaphore(max(1, settings.max_batch_workers))

    # -- 이벤트 ---------------------------------------------------------
    def publish(self, job: Job, kind: str) -> None:
        """이벤트 루프 스레드에서만 호출한다."""
        if kind == "status":
            event = {
                "type": "status",
                "seq": len(job.events) + 1,
                "status": job.status,
                "progress": round(job.progress, 4),
                "error": job.error,
            }
        elif kind == "final":
            if not job.segments:
                return
            seg = job.segments[-1]
            event = {
                "type": "final",
                "seq": len(job.events) + 1,
                "segment_id": f"s-{seg['seq']}",
                "revision": 1,
                "text": seg["text"],
                "start_ms": seg["start_ms"],
                "end_ms": seg["end_ms"],
            }
        else:  # done
            event = {
                "type": "done",
                "seq": len(job.events) + 1,
                "status": job.status,
                "duration_ms": job.duration_ms,
                "segments": len(job.segments),
            }
        job.events.append(event)
        for queue in list(job.subscribers):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                pass

    # -- 실행 -----------------------------------------------------------
    def start(self, job: Job, source: Path, word_timestamps: bool) -> None:
        job.task = asyncio.create_task(self._run(job, source, word_timestamps))

    def _fail(self, job: Job, code: str, message: str, retryable: bool = False) -> None:
        job.error = {"code": code, "message": message[:500], "retryable": retryable}
        job.status = "failed"
        job.finished_at = time.time()

    async def _run(self, job: Job, source: Path, word_timestamps: bool) -> None:
        async with self._sem:
            if job.canceled:
                job.status = "canceled"
                job.finished_at = time.time()
                self.publish(job, "status")
                self.publish(job, "done")
                source.unlink(missing_ok=True)
                return
            loop = asyncio.get_running_loop()
            job.status = "preprocessing"
            job.started_at = time.time()
            self.publish(job, "status")
            wav = self.work_dir / f"{job.id}.wav"
            last_status_publish = {"at": 0.0}
            try:
                await asyncio.to_thread(to_wav16k, source, wav)
                if job.canceled:
                    raise Canceled()
                job.status = "transcribing"
                self.publish(job, "status")

                def on_progress(progress: float, seg) -> None:
                    """추론 스레드에서 호출된다."""
                    if job.canceled:
                        raise Canceled()
                    job.progress = max(0.0, min(0.99, progress))
                    if seg is None:
                        now = time.monotonic()
                        if now - last_status_publish["at"] > 0.5:
                            last_status_publish["at"] = now
                            loop.call_soon_threadsafe(self.publish, job, "status")
                        return
                    data = seg.as_dict()
                    data["seq"] = len(job.segments)
                    job.segments.append(data)
                    job.text = "".join(item["text"] for item in job.segments).strip()
                    loop.call_soon_threadsafe(self.publish, job, "final")
                    now = time.monotonic()
                    if now - last_status_publish["at"] > 0.5:
                        last_status_publish["at"] = now
                        loop.call_soon_threadsafe(self.publish, job, "status")

                transcript = await asyncio.to_thread(
                    self.engine.transcribe_file,
                    wav,
                    job.language,
                    word_timestamps,
                    on_progress,
                )
                if job.canceled:
                    raise Canceled()
                job.status = "postprocessing"
                self.publish(job, "status")
                job.text = transcript.text
                job.detected_language = transcript.language
                job.detected_language_probability = transcript.language_probability
                job.duration_ms = transcript.duration_ms
                job.progress = 1.0
                job.status = "completed"
                job.finished_at = time.time()
                self.publish(job, "status")
            except Canceled:
                job.status = "canceled"
                job.finished_at = time.time()
                self.publish(job, "status")
            except DependencyError as exc:
                # 서버 책임(ffmpeg 미설치 등). 사용자 입력 문제와 분리해 503 계열로 알린다.
                self._fail(job, "dependency_missing", str(exc), retryable=True)
                self.publish(job, "status")
            except AudioError as exc:
                self._fail(job, "invalid_audio", str(exc))
                self.publish(job, "status")
            except Exception as exc:  # 모델/IO 오류
                self._fail(job, "internal", f"{type(exc).__name__}: {exc}")
                self.publish(job, "status")
            finally:
                wav.unlink(missing_ok=True)
                source.unlink(missing_ok=True)
                self.publish(job, "done")
