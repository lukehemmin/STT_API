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

### 웹 테스트 UI

파일을 올려 변환해 보는 웹 페이지가 [`web/`](web/README.md)에 있다. API(8000)와 **별개 포트(8080)** 로 실행하며, Colab에서는 API 링크와 웹 링크가 따로 발급된다.

```bash
./run.sh --model small --preload       # API  http://localhost:8000
web/run.sh --api http://localhost:8000  # 웹  http://localhost:8080
```

## API 요약

| 모드 | 엔드포인트 | 결과 |
|---|---|---|
| Batch | `POST /v1/transcriptions` | `job_id` → 폴링 / `GET .../result?format=json\|txt\|srt\|vtt` |
| Batch 실시간 | `GET /v1/transcriptions/{id}/events` | SSE로 진행률/세그먼트 리플레이 + live |
| Streaming | `WS /v1/stream` | `partial` → `final` → `done` 즉시 수신 |

## 언어

- `language`를 생략하면 자동 감지한다. 결과는 배치의 `detected_language` / 스트리밍 `final`·`done` 이벤트의 `language`로 확인한다.
- 자동 감지일 때는 **30초 창마다 언어를 다시 판별**한다(`STT_MULTILINGUAL=1`, 기본). 한국어와 영어가 섞인 파일도 구간별로 해당 언어로 디코딩한다.
- 한 30초 창 **안에서** 언어가 바뀌면 그 창은 우세 언어 하나로 처리되므로 경계에서 정확도가 떨어진다. 언어를 알고 있으면 `language=ko`처럼 고정하는 편이 정확하다.
- `STT_LANGUAGE=ko`를 주면 서버 기본값을 고정한다(요청별 `language`가 우선).

> 작업을 이어받을 때는 [HANDOFF.md](HANDOFF.md)를 먼저 읽는다. 현재 코드 상태·확정 결정·알려진 함정·다음 작업이 정리되어 있다.

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
