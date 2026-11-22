# 03. API 명세

Base URL: `https://api.example.com/v1` · 인증: `Authorization: Bearer <API_KEY>`
모든 응답에 `X-Request-Id` 헤더. 오류 응답 본문은 공통 형식.

## 0. 공통

### 오류 형식

```json
{
  "error": {
    "code": "audio_too_long",
    "message": "오디오 길이가 최대 4시간을 초과했습니다.",
    "retryable": false,
    "request_id": "req_01J8Z9..."
  }
}
```

| code | HTTP | 의미 |
|---|---|---|
| `invalid_request` | 400 | 필수 값 누락/형식 오류 |
| `unsupported_format` | 415 | 디코딩 불가 |
| `invalid_audio` | 422 | 무음/손상/채널 문제 |
| `payload_too_large` | 413 | 파일 크기 초과 |
| `audio_too_long` | 422 | 길이 초과 |
| `unauthorized` | 401 | 키 없음/무효 |
| `forbidden` | 403 | 타 테넌트 리소스 접근 |
| `job_not_found` | 404 | 잡/결과 없음 |
| `job_not_ready` | 409 | 결과 미완료(폴링/`Retry-After`) |
| `quota_exceeded` | 429 | 동시 세션/잡/사용량 초과 |
| `rate_limited` | 429 | 요청 빈도 초과 (`Retry-After`) |
| `session_limit_reached` | 503 | 스트리밍 전역 용량 초과 (재시도 권장) |
| `model_unavailable` | 503 | 엔진 일시 불가 |
| `dependency_missing` | 503 | 서버 의존성(ffmpeg 등) 없음 |
| `canceled` | 409 | 취소된 잡에 대한 조작 |
| `internal` | 500 | 서버 오류 |

### 한도 (기본값, 설정 가능)

| 항목 | 값 |
|---|---|
| 최대 파일 크기 | 2 GB |
| 최대 오디오 길이 (batch) | 4시간 |
| 최대 스트리밍 세션 | 2시간 (연장 불가, 새 세션) |
| 프레임 크기 | 20~100 ms |
| 스트리밍 포맷 | `pcm_s16le`, 16 kHz, mono (서버 리샘플 옵션) |
| 동시 스트리밍 (키당) | 5 |
| 동시 배치 잡 (키당) | 20 |
| 폴링 최소 간격 | 1 s |

---

## 1. Batch

### 1.1 잡 생성

`POST /v1/transcriptions`

옵션 A — multipart 업로드:

```http
POST /v1/transcriptions
Content-Type: multipart/form-data
Idempotency-Key: 6f1c2a3e-...

file=@meeting.mp4
language=ko
timestamps=segment
callback_url=https://client.example.com/hooks/stt
```

옵션 B — JSON + URL:

```json
{
  "source": { "type": "url", "url": "https://.../call.wav" },
  "language": "ko",
  "timestamps": "segment",
  "profanity_filter": false,
  "callback_url": "https://client.example.com/hooks/stt",
  "metadata": { "meeting_id": "m-123" }
}
```

옵션 C — 대용량 presigned 업로드:

```json
{ "source": { "type": "upload", "filename": "lecture.mp4", "size_bytes": 734003200 } }
```

→ `201`에 `upload.url`(PUT), `upload.headers`, `upload.expires_at` 반환. 업로드 완료 후 `POST /v1/transcriptions/{id}/submit`.

응답 `202 Accepted`:

```json
{
  "id": "tr_01J8Z9K4",
  "mode": "batch",
  "status": "queued",
  "language": "ko",
  "created_at": "2026-11-22T10:00:00Z",
  "links": {
    "self": "/v1/transcriptions/tr_01J8Z9K4",
    "events": "/v1/transcriptions/tr_01J8Z9K4/events",
    "result": "/v1/transcriptions/tr_01J8Z9K4/result",
    "cancel": "/v1/transcriptions/tr_01J8Z9K4/cancel"
  }
}
```

### 1.2 상태 조회

`GET /v1/transcriptions/{id}`

```json
{
  "id": "tr_01J8Z9K4",
  "status": "transcribing",
  "progress": 0.42,
  "duration_ms": 3600000,
  "language": "ko",
  "detected_language": "ko",
  "segments_completed": 128,
  "error": null,
  "created_at": "2026-11-22T10:00:00Z",
  "started_at": "2026-11-22T10:00:03Z",
  "estimated_completion_at": "2026-11-22T10:07:00Z"
}
```

상태: `queued` → `preprocessing` → `transcribing` → `postprocessing` → `completed` | `failed` | `canceled`

### 1.3 결과 조회

`GET /v1/transcriptions/{id}/result?format=json|txt|srt|vtt&include_words=true`

```json
{
  "id": "tr_01J8Z9K4",
  "language": "ko",
  "duration_ms": 3600000,
  "text": "안녕하세요. 오늘 회의는...",
  "segments": [
    {
      "id": "s-0",
      "start_ms": 0,
      "end_ms": 3200,
      "text": "안녕하세요.",
      "confidence": 0.94,
      "words": [{ "w": "안녕하세요", "start_ms": 120, "end_ms": 900, "p": 0.97 }]
    }
  ],
  "model": "whisper-large-v3-turbo",
  "created_at": "2026-11-22T10:00:00Z"
}
```

미완료 시 `409 job_not_ready` + `Retry-After`. `format=srt` 등은 `text/plain` / `text/vtt`로 반환.

### 1.4 취소

`POST /v1/transcriptions/{id}/cancel` → `202`, 상태 `canceled`. 이미 `completed`면 `409`.

### 1.5 이벤트 구독 (SSE) — Hybrid

`GET /v1/transcriptions/{id}/events?from_seq=0` (`text/event-stream`)

- 이미 완료된 잡: 저장된 이벤트를 `seq` 순서대로 리플레이 후 `done` → 연결 종료.
- 진행 중: 리플레이 후 live tail. `Last-Event-ID` 헤더로 재개.
- 진행률/상태 변경도 이벤트로 포함.

```
id: 1
event: status
data: {"type":"status","seq":1,"status":"transcribing","progress":0.1}

id: 2
event: final
data: {"type":"final","seq":2,"segment_id":"s-0","revision":1,"text":"안녕하세요.","start_ms":0,"end_ms":3200}

id: 3
event: done
data: {"type":"done","seq":3,"duration_ms":3600000,"segments":128}
```

### 1.6 웹훅

완료/실패 시 `callback_url`로 POST.

```json
{
  "event": "transcription.completed",
  "id": "tr_01J8Z9K4",
  "status": "completed",
  "result_url": "https://api.example.com/v1/transcriptions/tr_01J8Z9K4/result",
  "occurred_at": "2026-11-22T10:06:40Z"
}
```

헤더: `X-STT-Signature: t=<unix>,v1=<HMAC-SHA256(t + "." + body, secret)>`
재시도: 5회 지수 백오프(1s→16s), 2xx 아니면 실패. 서명 검증 + 5분 타임스탬프 윈도.

---

## 2. Streaming (WebSocket)

`WSS /v1/stream?language=ko&encoding=pcm_s16le&sample_rate=16000&channels=1&interim=true&word_timestamps=false`

- 인증: `Authorization` 헤더(브라우저는 `Sec-WebSocket-Protocol: bearer.<token>` 서브프로토콜 사용).
- 텍스트 프레임 = JSON 제어, 바이너리 프레임 = 오디오.

### 2.1 서버 → 클라이언트

```json
{ "type": "ready", "session_id": "ss_01J8ZA", "max_duration_ms": 7200000, "frame_ms": 100 }
```

부분 결과(교체 가능):

```json
{ "type": "partial", "seq": 12, "segment_id": "s-3", "revision": 4,
  "text": "오늘 회의는", "start_ms": 1200, "end_ms": 2800 }
```

확정 결과:

```json
{ "type": "final", "seq": 13, "segment_id": "s-3", "revision": 5,
  "text": "오늘 회의는 시작하겠습니다.", "start_ms": 1200, "end_ms": 3400,
  "confidence": 0.91, "words": [] }
```

종료:

```json
{ "type": "done", "seq": 40, "duration_ms": 15230, "segments": 7 }
```

오류/경고:

```json
{ "type": "error", "code": "slow_consumer", "message": "수신 지연으로 프레임 일부를 건너뛰었습니다.", "retryable": true }
```

```json
{ "type": "error", "code": "session_limit_reached", "retryable": true }
```

- `seq`는 세션 내 단조 증가. 클라이언트는 `partial`을 `segment_id` 기준으로 덮어쓰고, `final`이 오면 확정.
- `revision`은 같은 세그먼트의 가설 버전. 낮은 revision은 무시.

### 2.2 클라이언트 → 서버

```json
{ "type": "flush" }
```
→ 현재 버퍼를 즉시 확정하고 `final` 발행.

```json
{ "type": "end" }
```
→ 남은 버퍼 확정 후 `done` 발행, 서버가 close.

```json
{ "type": "ping", "t": 1732269600000 }
```
→ `{"type":"pong","t":...}` (프록시 유휴 타임아웃 방지, 15초 권장)

(v2) `{ "type": "resume", "session_id": "...", "last_seq": 39 }`

### 2.3 세션 규칙

| 규칙 | 동작 |
|---|---|
| 첫 바이너리 프레임까지 10초 | `error: handshake_timeout` 후 close |
| 60초 무입력 | `error: idle_timeout` 후 close |
| 최대 길이 도달 | 남은 버퍼 `final` → `done` → close |
| 프레임 크기 초과(>1초) | `invalid_request` 후 close |
| 서버 종료(SIGTERM) | `error: server_draining`(retryable) 후 close, 확정분 저장 |
| 백프레셔 | ring buffer 한계 시 오래된 오디오 drop + `slow_consumer` 통지 |

### 2.4 클라이언트 예시 (개념)

```python
async with websockets.connect(url, additional_headers={"Authorization": f"Bearer {key}"}) as ws:
    print(await ws.recv())                     # ready
    async def send():
        while chunk := mic.read(1600):          # 100 ms @16 kHz
            await ws.send(chunk)
        await ws.send(json.dumps({"type": "end"}))
    async def recv():
        async for msg in ws:
            ev = json.loads(msg)
            if ev["type"] in {"partial", "final"}:
                render(ev)
            elif ev["type"] == "done":
                break
    await asyncio.gather(send(), recv())
```

---

## 3. 세션 조회 (스트리밍 사후 조회)

`GET /v1/stream-sessions/{session_id}` → 상태, 확정 세그먼트, 지속 시간.
`GET /v1/stream-sessions/{session_id}/events` → 저장된 이벤트 리플레이(디버깅/복구용).

---

## 4. 인증/쿼터 응답 헤더

```http
X-RateLimit-Limit: 100
X-RateLimit-Remaining: 87
X-RateLimit-Reset: 1732269660
X-Quota-Concurrent-Streams: 5
X-Quota-Concurrent-Jobs: 20
Retry-After: 2
```

## 5. 버전 관리

- URL 경로 버전(`/v1`). 하위 호환 필드 추가는 마이너로 간주, 제거/의미 변경은 `/v2`.
- 이벤트 스키마는 `type` 기준으로 진화. 알 수 없는 `type`은 클라이언트가 무시할 것을 권장.
