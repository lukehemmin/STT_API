# STT_API

음성·영상 파일을 텍스트로 전사하는 API. 실시간 스트리밍과 일괄(batch) 전사를 하나의 엔진에서 동시에 지원한다.

## 실행

### 자체 호스팅 (GPU 서버)

```bash
./run.sh --model small --preload
# http://localhost:8000/docs
```

CPU만 있으면 `./run.sh --cpu --model base`. 옵션은 `./run.sh --help`.

### Google Colab (Pro)

[`notebooks/colab_stt_api.ipynb`](notebooks/colab_stt_api.ipynb)를 GPU 런타임에서 열고 위에서부터 실행한다.
저장소 클론 → 의존성 설치 → 모델 프리로드 → 서버 + 임시 터널 → 배치/스트리밍 데모까지 한 번에 진행된다.

## API 요약

| 모드 | 엔드포인트 | 결과 |
|---|---|---|
| Batch | `POST /v1/transcriptions` | `job_id` → 폴링 / `GET .../result?format=json\|txt\|srt\|vtt` |
| Batch 실시간 | `GET /v1/transcriptions/{id}/events` | SSE로 진행률/세그먼트 리플레이 + live |
| Streaming | `WS /v1/stream` | `partial` → `final` → `done` 즉시 수신 |

## 계획 문서

[plan/README.md](plan/README.md)에서 시작한다.

- [요구사항](plan/01-requirements.md)
- [아키텍처](plan/02-architecture.md)
- [API 명세](plan/03-api-spec.md)
- [파이프라인](plan/04-pipeline.md)
- [데이터/운영](plan/05-data-and-operations.md)
- [로드맵](plan/06-roadmap.md)
- [확정 필요 결정](plan/07-open-decisions.md)
- [실행 환경: 자체 호스팅 · Colab](plan/08-runtime-environments.md)

> 현재 구현은 인메모리 프로토타입이다. 잡 상태는 재시작 시 사라지고 모델 추론은 단일 프로세스에서 직렬화된다.
