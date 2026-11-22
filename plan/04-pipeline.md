# 04. 전사 파이프라인

## 1. 전체 단계

```
입력 → 디코드/리샘플(16 kHz mono) → VAD → 세그멘테이션 → 추론 → 후처리 → 이벤트
```

모든 입력은 **16 kHz mono float32**로 정규화한 뒤 동일한 파이프라인을 탄다. 배치/스트리밍 차이는 세그멘터의 동작 모드뿐이다.

| 단계 | 배치 | 스트리밍 |
|---|---|---|
| 디코드 | ffmpeg로 파일 전체 → PCM | 프레임 단위 디코드/리샘플 |
| VAD | 전체 스캔, 무음 경계로 분할 | 20~30 ms 단위 실시간 판정 |
| 세그멘테이션 | 5~15분 청크(무음 경계 우선) | 0.5~15초 발화 세그먼트 |
| 추론 | 청크 단위 배치 디코드 | 세그먼트 partial 재디코드 + final |
| 후처리 | 오프셋 병합, 문장부호, 포맷 | 확정 세그먼트 append |

## 2. 오디오 전처리

- 지원 입력: wav/mp3/m4a/aac/flac/ogg/opus, mp4/mov/mkv/webm (오디오 트랙 추출)
- 정규화: 다운믹스(mono), 16 kHz, peak normalize(클리핑 방지), DC offset 제거
- 무음만 있는 파일: `invalid_audio` (batch) / 즉시 `done` (stream)
- 매우 긴 파일: ffmpeg 스트리밍 디코드 → 디스크 스풀 없이 청크 생성 (메모리 사용 상한 고정)
- 손상 구간: 디코더 오류는 해당 구간 skip + `warning` 이벤트, 전체 실패는 아님

## 3. 스트리밍 세그멘테이션

### 3.1 VAD 기반

- Silero VAD, 512 샘플(32 ms) 윈도우.
- 상태: `silence` → `speech`(확률 임계 상향 진입) → `speech` 유지(임계 하향) → `silence` N ms 지속 시 종료.
- 진입 지연을 줄이기 위해 **pre-roll 300 ms**를 버퍼에 유지해 첫 음절 잘림 방지.
- 발화 종료 판정: 무음 500 ms(설정 가능). `final`은 이 시점에 발행.
- 최대 세그먼트 15초: 강제 분할하되, 문장 경계(무음) 우선. 강제 분할 시 `split=true` 표시.

### 3.2 Partial 전략

| 항목 | 값 | 이유 |
|---|---|---|
| partial 주기 | 300~500 ms | 지연 vs GPU 비용 균형 |
| 최소 오디오 | 400 ms | 너무 짧으면 오탐/환각 |
| 컨텍스트 | 이전 확정 1~2 세그먼트 텍스트를 prompt로 전달 | 문맥/고유명사 일관성 |
| 재디코딩 범위 | 현재 세그먼트 전체(+pre-roll) | 단순/정확. 비용은 세그먼트 상한으로 제어 |
| revision | 매 partial마다 +1 | 클라이언트가 최신만 반영 |

- **환각 억제**: `no_speech_threshold`, `condition_on_previous_text=false`(구간별), 무음/잡음 구간 스킵, 반복 텍스트 필터.
- **확정 규칙**: VAD 종료 또는 `flush`/`end` 시점의 마지막 가설을 `final`로 발행. 이후 같은 `segment_id`의 partial은 금지.
- **지연 예산**: 프레임 도착 → partial p95 700 ms (추론 300 ms + 배칭 100 ms + 전송/버퍼 여유).

### 3.3 클라이언트-서버 시간축

- 서버가 받은 첫 프레임을 `t=0`으로 하는 `start_ms`/`end_ms` 반환.
- 클라이언트는 자신의 녹음 시작 시각과의 오프셋을 별도로 기록(서버는 벽시계 미보장).
- 네트워크 지터로 프레임이 늦게 도착해도 **도착 순서대로** 타임스탬프 부여(누락 시 gap 표시).

## 4. 배치 분할과 병합

### 4.1 분할

- 1차: VAD로 무음 구간 탐지.
- 2차: 목표 청크 5~10분, 무음 경계에서 자름. 무음이 없으면 10분 강제 분할.
- 각 청크는 `(index, start_ms, end_ms, audio_ref)`로 큐에 등록 → **병렬 처리**.
- 청크 경계 문맥 손실 방지: 앞뒤 200~500 ms 오버랩을 포함해 디코드하고, 결과에서 오버랩 구간 텍스트를 제거(단어 타임스탬프 기준).

### 4.2 병합

- 청크 결과의 단어/세그먼트 타임스탬프에 `chunk.start_ms` 오프셋 적용.
- 경계에서 중복 제거: 시간 겹침 + 텍스트 유사도(정규화 후) 기준.
- 문장이 청크 경계에서 잘린 경우: 인접 세그먼트 병합 또는 문장부호 후처리.
- 최종 `segments`는 `start_ms` 오름차순, 겹침 없음, `end_ms ≥ start_ms` 불변식 검증.
- 진행률 = 완료 청크 오디오 길이 / 전체 길이 (0.0~1.0).

### 4.3 실패/재시도

- 청크 단위 최대 3회 재시도(지수 백오프). 2회 연속 실패 시 청크를 반으로 나눠 1회 재시도.
- 일부 청크 최종 실패: 기본은 잡 `failed`(부분 결과는 `partial_result=true`로 조회 허용). 설정으로 "부분 성공" 허용 가능.
- 취소: 진행 중 청크는 완료 후 결과 폐기, 미시작 청크는 큐에서 제거. 상태는 `canceled`.

## 5. 추론 계층

### 5.1 인터페이스

```python
class Transcriber(Protocol):
    async def warmup(self) -> None: ...
    async def transcribe(self, audio: np.ndarray, *, language: str | None,
                         prompt: str | None, word_timestamps: bool) -> Hypothesis: ...
```

- `Hypothesis(text, language, words, avg_logprob, no_speech_prob)`
- 엔진 교체(자체 호스팅 ↔ 클라우드)는 이 인터페이스 구현체 교체로 끝난다.
- `Transcriber`는 세그먼트 길이 상한(15초)을 가정. 더 긴 입력은 파이프라인이 나눈다.

### 5.2 마이크로배칭 스케줄러

```
ready_queue: 우선순위 큐 (streaming partial > streaming final > batch chunk)
loop:
  batch = []
  deadline = now + 50ms
  while len(batch) < 8 and now < deadline:
      item = ready_queue.pop(timeout=남은시간)
      batch.append(item)
  results = model.transcribe_batch([...])
  dispatch(results)
```

- 길이가 비슷한 요청끼리 묶어 패딩 낭비 최소화(버킷: <3 s, <8 s, ≤15 s).
- 스트리밍 요청이 없으면 배치만으로 큰 배치(최대 16)를 구성해 처리량 극대화.
- 텍스트만 필요한 partial은 `word_timestamps=false`로 비용 절감.

### 5.3 모델 선택

| 티어 | 모델 | 용도 |
|---|---|---|
| `fast` | small/medium 계열 | 실시간, 낮은 지연 |
| `default` | large-v3-turbo | 일반 배치/스트리밍 |
| `accurate` | large-v3 | 아카이브/오프라인 |

- 모델별 워커 풀 분리(같은 GPU에 여러 모델 상주 시 VRAM 경합 주의). 초기엔 `default` 단일 모델 권장.
- 워커 시작 시 warmup 1회, 준비 전 `readiness` 실패 처리.
- VRAM 예산: 모델 + 배치 8 × 15초 오디오. 초과 시 배치 크기 자동 축소.

## 6. 이벤트 발행 규칙

1. `session_id` + 단조 증가 `seq` (Redis `INCR`).
2. `partial`은 저장하되 TTL 짧게(예: 10분), `final`/`done`은 세션 결과 TTL까지.
3. 이벤트 쓰기와 DB 세그먼트 저장은 같은 트랜잭션 경계를 목표로 하되, 최소한 **final은 DB 커밋 후 발행**(유실 시 재조회 가능).
4. 순서 보장: 세션당 단일 writer(Worker가 해당 세션을 소유) → 병렬 세션 간에는 독립.
5. 재생: `from_seq`/`Last-Event-ID` 이후 이벤트만 반환, 없으면 `done` 후 종료.

## 7. 품질/측정

- 골든셋: 한국어/영어 회의·통화·강의, 무음/잡음/겹침/원거리 각 5개 이상.
- 지표: CER/WER, 세그먼트 경계 오차, partial 안정성(같은 구간 텍스트 변동 횟수), 지연 p50/p95.
- 회귀: 모델/파라미터 변경 시 골든셋 CER 악화 2%p 초과면 배포 차단.
- 스트리밍 CER은 동일 오디오 배치 결과를 기준선으로 비교(절대값 아님).
