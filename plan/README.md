# STT API 구축 계획

음성·영상 파일을 텍스트로 전사하는 API. **실시간 스트리밍**과 **일괄(batch)** 두 가지 결과 전달 방식을 같은 엔진 위에서 지원하고, 두 방식이 **동시에** 처리되어도 서로 영향을 주지 않도록 설계한다.

## 한 줄 요약

> 전사 파이프라인은 하나, 이벤트 스트림도 하나.
> 결과를 **즉시 흘려보내는가(WebSocket/SSE)** vs **모아서 한 번에 주는가(REST/웹훅)** 만 다르다.

## 이렇게 동작한다

| 모드 | 입력 | 결과 전달 | 대표 사용처 |
|---|---|---|---|
| **Batch** | 파일 업로드 또는 URL | `202 + job_id` → 폴링 / 웹훅 / SSE 구독 | 회의 녹음, 통화 녹취, 자막 생성 |
| **Streaming** | 마이크/실시간 오디오 프레임 | WebSocket으로 `partial` → `final` 즉시 푸시 | 실시간 자막, 라이브 상담, 음성 명령 |
| **Hybrid** | Batch로 제출한 잡을 SSE/WS로 구독 | 진행 중 `partial`도 즉시, 종료 시 최종본 | 긴 파일을 올리면서 앞부분부터 보기 |

세 모드는 **별도 코드 경로가 아니다.** 모든 전사는 `TranscriptionSession` 하나로 모델링되고, 동일한 이벤트(`partial` / `final` / `done` / `error`)를 발행한다. 전달 계층만 다르다. 덕분에 "동시에"에 대한 요구가 자연스럽게 만족된다.

- 여러 배치 잡 + 여러 스트리밍 세션이 동시에 → 워커 풀과 큐로 격리·공정 분배
- 같은 오디오를 배치로 제출하고 스트리밍으로도 구독 → 같은 이벤트 스트림을 두 소비자가 읽음
- 배치 잡을 나중에 구독해도 → 저장된 이벤트를 리플레이한 뒤 `done`

## 문서 지도

| 문서 | 내용 |
|---|---|
| [01-requirements.md](01-requirements.md) | 기능/비기능 요구사항, 사용자 시나리오, 수용 기준 |
| [02-architecture.md](02-architecture.md) | 컴포넌트 구조, 동시성 모델, 세션 추상화, 기술 스택 |
| [03-api-spec.md](03-api-spec.md) | REST·WebSocket·SSE 명세, 이벤트/에러 스키마, 예제 |
| [04-pipeline.md](04-pipeline.md) | 오디오 전처리, VAD 세그멘테이션, 배치 분할·병합, 모델 서빙 |
| [05-data-and-operations.md](05-data-and-operations.md) | 상태 머신, DB 스키마, 저장소, 보존, 관측성, 보안, 배포 |
| [06-roadmap.md](06-roadmap.md) | 마일스톤, 작업 분해, 테스트 전략, 리스크 |
| [07-open-decisions.md](07-open-decisions.md) | 확정이 필요한 선택지와 권장 기본값 |
| [08-runtime-environments.md](08-runtime-environments.md) | 자체 호스팅 vs Colab(Pro), 런처, 환경 변수, 제약 |

## 빠른 시작

### 자체 호스팅 (GPU 서버)

```bash
./run.sh --model small --preload
# http://localhost:8000/docs
```

### Colab (Pro)

[`notebooks/colab_stt_api.ipynb`](../notebooks/colab_stt_api.ipynb)를 열고 GPU 런타임에서 위에서부터 실행한다. 저장소 클론 → 의존성 설치 → 모델 프리로드 → 서버 + 임시 터널 → 배치/스트리밍 데모 순서다. 자세한 내용은 [08-runtime-environments.md](08-runtime-environments.md).

## 권장 기본값 (문서에서 제안하는 안)

- **언어/스택**: Python 3.11+, FastAPI + uvicorn (async API 계층)
- **STT 엔진**: `faster-whisper`(CTranslate2) 자체 호스팅, `Transcriber` 인터페이스로 교체 가능하게
- **큐/상태**: Redis (Streams + TTL), **영속 데이터**: PostgreSQL, **오디오 원본**: S3 호환 스토리지
- **동시성**: API(무상태, 수평 확장) ↔ GPU 추론 워커(별도 배포) 분리, 마이크로배칭
- **스트리밍 프로토콜**: WebSocket + 16 kHz mono PCM16, 20~100 ms 프레임
- **결과 포맷**: JSON(세그먼트/단어 타임스탬프), TXT, SRT, VTT
- **실행 환경**: 자체 호스팅(`run.sh`) + Colab 노트북(`notebooks/`), 동일 코드·환경 변수

## 용어

- **세션(Session)**: 한 번의 전사 단위. 스트리밍 연결 1개 또는 배치 잡 1개에 대응.
- **세그먼트(Segment)**: VAD/분할로 잘린 발화 구간. `partial`(교체 가능) → `final`(확정).
- **RTF(Real-Time Factor)**: 처리 시간 / 오디오 길이. 0.1이면 10배속.
- **부분 결과(partial)**: 아직 확정되지 않은 가설. 같은 `segment_id`의 최신 `revision`만 유효.
- **최종 결과(final)**: 확정된 텍스트. 이후 변경되지 않음(타임스탬프 기준 병합만 일어남).
