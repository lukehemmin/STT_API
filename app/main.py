"""FastAPI 진입점 (인메모리 프로토타입).

계획(plan/)의 API 계약 중 batch 생성/조회/결과/취소/SSE, streaming WebSocket을
최소 구현한다. 프로세스 재시작 시 상태가 사라지고 단일 모델을 직렬 사용한다.
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import (
    Depends,
    FastAPI,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    UploadFile,
    WebSocket,
)
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse, StreamingResponse

from . import formats
from .config import settings
from .engine import engine
from .jobs import TERMINAL_STATUSES, BatchRunner, Job, JobStore
from .stream import StreamingSession

DATA_DIR = Path(settings.data_dir)
UPLOAD_DIR = DATA_DIR / "uploads"
WORK_DIR = DATA_DIR / "work"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
WORK_DIR.mkdir(parents=True, exist_ok=True)

store = JobStore()
runner = BatchRunner(engine, store, WORK_DIR)

ACTIVE_STREAMS = 0
MAX_STREAM_MS = 2 * 60 * 60 * 1000  # 2시간
RESULT_FORMATS = {"json", "txt", "srt", "vtt"}


@asynccontextmanager
async def lifespan(_: FastAPI):
    if settings.preload:
        await asyncio.to_thread(engine.load)
    yield


app = FastAPI(
    title="STT API",
    version="0.1.0",
    description="자체 호스팅 / Colab 공용 STT API 프로토타입 (batch + streaming)",
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in settings.cors_origins.split(",") if o.strip()],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# 공통 유틸
# ---------------------------------------------------------------------------
def _iso(ts: Optional[float]) -> Optional[str]:
    if ts is None:
        return None
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat().replace("+00:00", "Z")


def _error(code: str, message: str, retryable: bool = False) -> dict:
    return {"error": {"code": code, "message": message, "retryable": retryable}}


def require_auth(request: Request) -> None:
    if not settings.api_key:
        return
    header = request.headers.get("authorization", "")
    token = header[7:].strip() if header.lower().startswith("bearer ") else ""
    if token != settings.api_key:
        raise HTTPException(
            status_code=401,
            detail={"code": "unauthorized", "message": "유효한 API 키가 필요합니다."},
        )


def job_payload(job: Job) -> dict:
    return {
        "id": job.id,
        "mode": job.mode,
        "status": job.status,
        "progress": round(job.progress, 4),
        "language": job.language,
        "detected_language": job.detected_language,
        "duration_ms": job.duration_ms,
        "model": job.model,
        "error": job.error,
        "created_at": _iso(job.created_at),
        "started_at": _iso(job.started_at),
        "finished_at": _iso(job.finished_at),
        "links": {
            "self": f"/v1/transcriptions/{job.id}",
            "events": f"/v1/transcriptions/{job.id}/events",
            "result": f"/v1/transcriptions/{job.id}/result",
            "cancel": f"/v1/transcriptions/{job.id}/cancel",
        },
    }


async def _get_job(job_id: str) -> Job:
    job = store.get(job_id)
    if job is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "job_not_found", "message": f"잡을 찾을 수 없습니다: {job_id}"},
        )
    return job


@app.middleware("http")
async def _request_id(request: Request, call_next):
    request.state.request_id = request.headers.get("x-request-id") or "req_" + uuid.uuid4().hex[:12]
    response = await call_next(request)
    response.headers["X-Request-Id"] = request.state.request_id
    return response


@app.exception_handler(HTTPException)
async def _http_error(request: Request, exc: HTTPException):
    detail = exc.detail if isinstance(exc.detail, dict) else {}
    payload = _error(
        detail.get("code", "invalid_request"),
        detail.get("message", str(exc.detail)),
        bool(detail.get("retryable", False)),
    )
    payload["error"]["request_id"] = getattr(request.state, "request_id", None)
    return JSONResponse(status_code=exc.status_code, content=payload, headers=exc.headers)


@app.exception_handler(RequestValidationError)
async def _validation_error(request: Request, exc: RequestValidationError):
    payload = _error("invalid_request", str(exc.errors())[:500])
    payload["error"]["request_id"] = getattr(request.state, "request_id", None)
    return JSONResponse(status_code=400, content=payload)


# ---------------------------------------------------------------------------
# 상태/정보
# ---------------------------------------------------------------------------
@app.get("/healthz")
async def healthz() -> dict:
    return {"status": "ok"}


@app.get("/readyz")
async def readyz() -> dict:
    return {"status": "ok", "engine": engine.info(), "active_streams": ACTIVE_STREAMS}


@app.get("/v1/info")
async def info() -> dict:
    return {
        "engine": engine.info(),
        "limits": {
            "max_upload_mb": settings.max_upload_mb,
            "max_active_jobs": settings.max_active_jobs,
            "max_active_streams": settings.max_active_streams,
            "max_stream_ms": MAX_STREAM_MS,
            "stream_sample_rate": 16000,
            "stream_encoding": "pcm_s16le",
            "stream_channels": 1,
        },
        "active_jobs": store.active_count(),
        "active_streams": ACTIVE_STREAMS,
    }


# ---------------------------------------------------------------------------
# Batch
# ---------------------------------------------------------------------------
@app.post("/v1/transcriptions", status_code=202)
async def create_transcription(
    file: UploadFile = File(...),
    language: Optional[str] = Form(None),
    timestamps: str = Form("segment"),
    _: None = Depends(require_auth),
):
    if timestamps not in {"segment", "word"}:
        raise HTTPException(
            status_code=400,
            detail={"code": "invalid_request", "message": "timestamps는 segment 또는 word만 지원합니다."},
        )
    if store.active_count() >= settings.max_active_jobs:
        raise HTTPException(
            status_code=429,
            detail={"code": "quota_exceeded", "message": "동시 배치 잡 한도를 초과했습니다.", "retryable": True},
        )
    if not file.filename:
        raise HTTPException(
            status_code=400,
            detail={"code": "invalid_request", "message": "파일 이름이 필요합니다."},
        )

    suffix = Path(file.filename).suffix[:10]
    dest = UPLOAD_DIR / f"{uuid.uuid4().hex[:12]}{suffix}"
    limit = settings.max_upload_mb * 1024 * 1024
    size = 0
    try:
        with dest.open("wb") as fh:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > limit:
                    raise HTTPException(
                        status_code=413,
                        detail={
                            "code": "payload_too_large",
                            "message": f"최대 {settings.max_upload_mb} MB까지 업로드할 수 있습니다.",
                        },
                    )
                fh.write(chunk)
    finally:
        await file.close()
        if size == 0 or size > limit:
            dest.unlink(missing_ok=True)

    job = store.create(file.filename, language, engine.model_name)
    job.source_name = file.filename
    runner.start(job, dest, timestamps == "word")
    return job_payload(job)


@app.get("/v1/transcriptions/{job_id}")
async def get_transcription(job_id: str, _: None = Depends(require_auth)):
    return job_payload(await _get_job(job_id))


@app.get("/v1/transcriptions/{job_id}/result")
async def get_result(
    job_id: str,
    format_: str = Query("json", alias="format"),
    include_words: bool = False,
    _: None = Depends(require_auth),
):
    job = await _get_job(job_id)
    if job.status != "completed":
        raise HTTPException(
            status_code=409,
            detail={
                "code": "invalid_request",
                "message": f"아직 결과가 준비되지 않았습니다(status={job.status}).",
                "retryable": job.status not in TERMINAL_STATUSES,
            },
            headers={"Retry-After": "2"},
        )
    if format_ not in RESULT_FORMATS:
        raise HTTPException(
            status_code=400,
            detail={"code": "invalid_request", "message": "format은 json|txt|srt|vtt 중 하나입니다."},
        )
    if format_ != "json":
        body = {
            "txt": lambda: formats.to_txt(job.segments, job.text),
            "srt": lambda: formats.to_srt(job.segments),
            "vtt": lambda: formats.to_vtt(job.segments),
        }[format_]()
        return PlainTextResponse(
            body,
            media_type="text/vtt" if format_ == "vtt" else "text/plain",
            headers={"X-STT-Segments": str(len(job.segments))},
        )

    segments: List[dict] = []
    for seg in job.segments:
        item = dict(seg)
        if not include_words:
            item.pop("words", None)
        segments.append(item)
    return {
        "id": job.id,
        "status": job.status,
        "language": job.detected_language or job.language,
        "duration_ms": job.duration_ms,
        "text": job.text,
        "segments": segments,
        "model": job.model,
        "created_at": _iso(job.created_at),
    }


@app.post("/v1/transcriptions/{job_id}/cancel", status_code=202)
async def cancel_transcription(job_id: str, _: None = Depends(require_auth)):
    job = await _get_job(job_id)
    if job.status in TERMINAL_STATUSES:
        raise HTTPException(
            status_code=409,
            detail={"code": "canceled", "message": f"이미 종료된 잡입니다(status={job.status})."},
        )
    job.canceled = True
    if job.status == "queued":
        job.status = "canceled"
        job.finished_at = time.time()
        runner.publish(job, "status")
        runner.publish(job, "done")
    return job_payload(job)


@app.get("/v1/transcriptions/{job_id}/events")
async def transcription_events(
    job_id: str,
    request: Request,
    from_seq: int = 0,
    _: None = Depends(require_auth),
):
    job = await _get_job(job_id)

    async def generate():
        sent = 0
        queue: asyncio.Queue = asyncio.Queue(maxsize=1000)
        job.subscribers.add(queue)
        try:
            while True:
                while sent < len(job.events):
                    event = job.events[sent]
                    sent += 1
                    if event["seq"] < from_seq:
                        continue
                    yield _sse(event)
                if job.status in TERMINAL_STATUSES:
                    break
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=15)
                except asyncio.TimeoutError:
                    yield ": keep-alive\n\n"
                    continue
                if event["seq"] < sent:
                    continue
                sent += 1
                yield _sse(event)
        finally:
            job.subscribers.discard(queue)

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _sse(event: dict) -> str:
    return f"id: {event['seq']}\nevent: {event['type']}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"


# ---------------------------------------------------------------------------
# Streaming
# ---------------------------------------------------------------------------
@app.websocket("/v1/stream")
async def stream(ws: WebSocket):
    global ACTIVE_STREAMS
    params = ws.query_params

    if settings.api_key:
        header = ws.headers.get("authorization", "")
        token = header[7:].strip() if header.lower().startswith("bearer ") else ""
        token = token or params.get("api_key", "")
        if token != settings.api_key:
            await ws.close(code=4401)
            return

    await ws.accept()

    unsupported = None
    if params.get("sample_rate", "16000") != "16000":
        unsupported = "sample_rate는 16000만 지원합니다."
    elif params.get("encoding", "pcm_s16le") not in {"pcm_s16le", "pcm16"}:
        unsupported = "encoding은 pcm_s16le만 지원합니다."
    elif params.get("channels", "1") != "1":
        unsupported = "mono(1ch)만 지원합니다."
    if unsupported:
        await ws.send_json(
            {"type": "error", "code": "unsupported_format", "message": unsupported, "retryable": False}
        )
        await ws.close(code=1003)
        return

    if ACTIVE_STREAMS >= settings.max_active_streams:
        await ws.send_json(
            {
                "type": "error",
                "code": "session_limit_reached",
                "message": "동시 스트리밍 세션 한도를 초과했습니다.",
                "retryable": True,
            }
        )
        await ws.close(code=1013)
        return

    session = StreamingSession(
        engine=engine,
        language=params.get("language"),
        interim=params.get("interim", "true").lower() != "false",
        word_timestamps=params.get("word_timestamps", "false").lower() == "true",
    )
    ACTIVE_STREAMS += 1
    store.stream_sessions[session.state.id] = session.state

    send_lock = asyncio.Lock()

    async def send(payload: Dict[str, Any]) -> None:
        async with send_lock:
            await ws.send_json(payload)

    await send(
        {
            "type": "ready",
            "session_id": session.state.id,
            "frame_ms": 100,
            "max_duration_ms": MAX_STREAM_MS,
            "model": engine.model_name,
        }
    )

    queue: asyncio.Queue = asyncio.Queue(maxsize=100)
    dropped = {"count": 0}

    async def reader() -> None:
        while True:
            message = await ws.receive()
            if message["type"] == "websocket.disconnect":
                queue.put_nowait(("end", None))
                return
            data = message.get("bytes")
            if data:
                if queue.full():
                    try:
                        queue.get_nowait()
                    except asyncio.QueueEmpty:
                        pass
                    dropped["count"] += 1
                    if dropped["count"] % 10 == 1:
                        await send(
                            {
                                "type": "error",
                                "code": "slow_consumer",
                                "message": "수신이 느려 오디오 일부를 건너뛰었습니다.",
                                "retryable": True,
                            }
                        )
                queue.put_nowait(("audio", data))
                continue
            try:
                payload = json.loads(message.get("text") or "{}")
            except json.JSONDecodeError:
                continue
            kind = payload.get("type")
            if kind == "end":
                queue.put_nowait(("end", None))
                return
            if kind == "flush":
                queue.put_nowait(("flush", None))
            elif kind == "ping":
                await send({"type": "pong", "t": payload.get("t")})

    async def processor() -> None:
        while True:
            kind, payload = await queue.get()
            if kind == "end":
                break
            try:
                if kind == "flush":
                    event = await session.flush()
                    if event:
                        await send(event)
                else:
                    for event in await session.feed(payload):
                        await send(event)
                if session.elapsed_ms >= MAX_STREAM_MS:
                    break
            except Exception as exc:  # 추론/전송 오류
                session.state.error = {"code": "internal", "message": str(exc)[:300]}
                await send(
                    {
                        "type": "error",
                        "code": "internal",
                        "message": f"{type(exc).__name__}: {exc}"[:300],
                        "retryable": True,
                    }
                )
                break
        for event in await session.finish():
            await send(event)

    reader_task = asyncio.create_task(reader())
    try:
        await processor()
    except Exception:
        session.abort()
    finally:
        reader_task.cancel()
        ACTIVE_STREAMS = max(0, ACTIVE_STREAMS - 1)
        if session.state.status == "active":
            session.abort()
        try:
            await ws.close()
        except Exception:
            pass


@app.get("/v1/stream-sessions/{session_id}")
async def get_stream_session(session_id: str, _: None = Depends(require_auth)):
    state = store.stream_sessions.get(session_id)
    if state is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "job_not_found", "message": f"세션을 찾을 수 없습니다: {session_id}"},
        )
    return {
        "id": state.id,
        "status": state.status,
        "language": state.language,
        "model": state.model,
        "duration_ms": state.duration_ms,
        "segments": state.segments,
        "error": state.error,
        "started_at": _iso(state.started_at),
        "ended_at": _iso(state.ended_at),
    }


@app.get("/v1/stream-sessions/{session_id}/events")
async def get_stream_events(session_id: str, _: None = Depends(require_auth)):
    state = store.stream_sessions.get(session_id)
    if state is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "job_not_found", "message": f"세션을 찾을 수 없습니다: {session_id}"},
        )
    return {"id": state.id, "events": state.events}


@app.get("/")
async def root() -> dict:
    return {
        "name": "STT API (prototype)",
        "docs": "/docs",
        "endpoints": [
            "POST /v1/transcriptions",
            "GET /v1/transcriptions/{id}",
            "GET /v1/transcriptions/{id}/result",
            "POST /v1/transcriptions/{id}/cancel",
            "GET /v1/transcriptions/{id}/events (SSE)",
            "WS /v1/stream",
            "GET /v1/stream-sessions/{id}",
        ],
    }
