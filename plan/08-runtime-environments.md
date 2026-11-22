# 08. 실행 환경: 자체 호스팅 · Colab

같은 코드(`app/`)와 같은 환경 변수로 두 곳에서 실행한다. 차이는 **런처와 제약**뿐이다.

| 항목 | 자체 호스팅 (GPU 서버) | Colab (Pro) |
|---|---|---|
| 진입점 | `./run.sh` | `notebooks/colab_stt_api.ipynb` |
| GPU | 소유/임대 GPU, 드라이버 직접 관리 | 세션에 할당된 T4/L4/A100 (요금제에 따라) |
| 수명 | 상시 | 세션 단위(최대 12시간, 유휴 시 종료) |
| 외부 접속 | 고정 주소/리버스 프록시 | cloudflared 임시 URL (`*.trycloudflare.com`) |
| 저장 | 로컬 디스크/오브젝트 스토리지 | 임시 디스크(세션 종료 시 소실) |
| 용도 | 운영·상시 API | 데모, PoC, 모델/성능 검증, 노트북 실습 |
| 인증 | `STT_API_KEY` 필수 | 선택이지만 공개 URL이면 필수 |

> Colab은 **운영 환경이 아니다.** 세션이 끊기면 API도 멈추고, 터널 URL은 공개된다. 계획의 M1~M5는 자체 호스팅을 기준으로 하고, Colab은 같은 코드를 빠르게 검증하는 경로로 둔다.

## 1. 공통 구조

```
app/
  main.py     # FastAPI: batch REST + SSE + streaming WS
  engine.py   # faster-whisper 래퍼 (지연 로드, 장치 자동 선택)
  stream.py   # VAD 세그멘테이션 + partial/final
  jobs.py     # 인메모리 잡 러너 (프로토타입)
  audio.py    # ffmpeg → 16 kHz mono
  formats.py  # TXT/SRT/VTT
  preload.py  # 모델 다운로드/로드 확인
run.sh                    # 자체 호스팅 런처
notebooks/colab_stt_api.ipynb  # Colab 런처 + 데모
```

- 두 경로 모두 `requirements.txt`를 사용한다.
- 모델 캐시: `STT_MODEL_DIR`(기본 HuggingFace 캐시). Colab은 세션마다 다시 받으므로 `small` 이하 권장.
- 장치 선택: `STT_DEVICE=auto` → `ctranslate2.get_cuda_device_count()`로 판정, GPU면 `float16`, CPU면 `int8`.

## 2. 자체 호스팅

### 요구사항

- Python 3.9+ (`python3-venv`), ffmpeg
- NVIDIA GPU 사용 시: 드라이버 + CUDA 12 런타임 (ctranslate2 휠 기준), VRAM 4 GB 이상(모델에 따라)
- CPU만으로도 동작하나 RTF가 크게 나빠진다 (`base`/`small` + `int8` 권장)

### 실행

```bash
chmod +x run.sh
./run.sh --model small --preload          # GPU 자동 감지
./run.sh --cpu --model base               # CPU 전용
./run.sh --model large-v3-turbo --port 9000
```

`run.sh`가 하는 일: 가상환경 생성 → `requirements.txt` 설치 → ffmpeg/CUDA 확인 → 모델 프리로드(옵션) → uvicorn 실행.

### 운영 배포 (예시)

- **systemd**: `ExecStart=/opt/stt/run.sh --model small --preload`, `Restart=always`, `Environment=STT_API_KEY=...`
- **Docker**: `python:3.11-slim` + `ffmpeg` 설치 + `uvicorn app.main:app`. GPU는 `--gpus all` + CUDA 베이스 이미지.
- **리버스 프록시**: Nginx에서 `/v1/stream`은 `Upgrade`/`Connection` 헤더와 긴 `proxy_read_timeout` 필요. 업로드 크기 한도를 `STT_MAX_UPLOAD_MB`와 맞춘다.
- **워커 분리**: 프로토타입은 API 프로세스가 직접 추론한다. 계획대로 확장하려면 `app/jobs.py`의 러너를 Redis 큐 기반 워커로 교체한다.

### GPU별 권장 모델 (출발점, M0에서 실측 보정)

| GPU | VRAM | 권장 모델 | 비고 |
|---|---|---|---|
| T4 | 16 GB | `small` / `base` | 스트리밍 포함 시 `small` |
| L4 | 24 GB | `large-v3-turbo` | RTF 여유 |
| A10/A100 | 24/40 GB+ | `large-v3-turbo` / `large-v3` | 배치 정확도 우선 |

## 3. Colab (Pro)

### 준비

1. 런타임 유형을 **GPU**로 변경
2. 노트북 셀 순서대로 실행: GPU 확인 → 저장소 클론 → 의존성 설치 → 모델 프리로드 → 서버+터널 → 배치 데모 → 스트리밍 데모
3. `MODEL`은 T4 기준 `small` 권장. 세션마다 모델을 다시 받으므로 큰 모델은 시작 시간이 길다.

### 노트북이 하는 일

- `git clone --depth 1`로 저장소를 가져와 `os.chdir`
- `apt-get install ffmpeg`, `pip install -r requirements.txt`
- `python -m app.preload`로 모델 로드 확인
- `uvicorn app.main:app`을 백그라운드로 실행, `/healthz` 폴링으로 준비 확인
- cloudflared 임시 터널을 띄워 `PUBLIC_URL` 출력 (외부 접속용)
- `jfk.wav` 샘플로 **배치**(업로드→폴링→SRT)와 **스트리밍**(PCM 100 ms 전송→partial/final) 데모

### Colab 제약과 대응

| 제약 | 대응 |
|---|---|
| 세션 최대 수명/유휴 종료 | 데모로만 사용, 장시간 작업은 자체 호스팅 |
| 임시 디스크 | 결과는 세션 안에서 소비, 필요 시 외부 스토리지로 전송 |
| 공개 터널 URL | `STT_API_KEY` 설정, 민감 데이터 업로드 금지 |
| GPU 종류 변동 | `STT_MODEL=small` 기본, 벤치마크는 M0에서 기록 |
| CUDA/ctranslate2 불일치 | `STT_DEVICE=cpu`로 폴백해 동작 확인 후 GPU 재시도 |

## 4. 환경 변수

| 변수 | 기본 | 설명 |
|---|---|---|
| `STT_MODEL` | `small` | faster-whisper 모델명 |
| `STT_DEVICE` | `auto` | `auto`/`cuda`/`cpu` |
| `STT_COMPUTE_TYPE` | `auto` | `float16`(GPU), `int8`(CPU) 자동 |
| `STT_MODEL_DIR` | HF 캐시 | 모델 다운로드 경로 |
| `STT_LANGUAGE` | 자동 | 기본 언어 고정 (`ko`, `en` 등) |
| `STT_MULTILINGUAL` | 1 | 자동 감지일 때 30초 창마다 언어 재감지(혼합 언어 대응) |
| `STT_API_KEY` | 없음 | 설정 시 Bearer/`api_key` 인증 필수 |
| `STT_DATA_DIR` | `data` | 업로드/작업 파일 경로 |
| `STT_MAX_UPLOAD_MB` | 2048 | 업로드 상한 |
| `STT_MAX_ACTIVE_JOBS` | 4 | 동시 배치 잡 |
| `STT_MAX_ACTIVE_STREAMS` | 4 | 동시 스트리밍 세션 |
| `STT_MAX_BATCH_WORKERS` | 1 | 배치 동시 실행 수(모델 1개 기준) |
| `STT_PRELOAD` | 0 | 시작 시 모델 로드 |
| `STT_PARTIAL_INTERVAL_MS` | 700 | partial 주기 |
| `STT_SILENCE_END_MS` | 600 | 발화 종료 판정 무음 |
| `STT_MAX_SEGMENT_MS` | 15000 | 세그먼트 최대 길이 |
| `STT_SPEECH_RMS` | 0.008 | 에너지 VAD 임계 |

## 5. 보안

- Colab 터널은 URL을 아는 누구나 접근할 수 있다. 반드시 `STT_API_KEY`를 설정하고, 데모 후 서버를 종료한다.
- 자체 호스팅에서도 기본은 인증 없음(개발 편의)이므로, 외부 노출 시 키를 설정한다.
- 업로드 파일은 처리 후 삭제한다(프로토타입). 계획의 보존 정책은 [05-data-and-operations.md](05-data-and-operations.md)를 따른다.
- 운영에서는 TLS 종료를 프록시에 맡기고, WS도 `wss`만 허용한다.

## 6. 검증 체크리스트

- [ ] `./run.sh --install-only` 후 `python -m app.preload` 성공
- [ ] `/healthz`, `/v1/info` 응답, `/docs` 접근
- [ ] 배치: `jfk.wav` 업로드 → `completed` → `format=srt` 파싱 가능
- [ ] 스트리밍: `ready` → `partial` 1회 이상 → `final` → `done`
- [ ] Colab: 노트북 셀 순서대로 실행해 `PUBLIC_URL` 생성 및 `/v1/info` 접근
- [ ] `STT_API_KEY` 설정 시 무키 요청이 401(HTTP)/4401(WS)

## 7. 한계와 다음 단계

- 인메모리 잡 저장 → 재시작 시 소실. M2에서 PostgreSQL/Redis로 교체.
- 단일 모델 직렬 추론 → 동시 스트리밍이 늘면 대기. M4에서 워커 풀/마이크로배칭.
- 에너지 기반 VAD → 잡음 환경 품질 제한. Silero VAD로 교체 예정.
- Colab 자동 재시작/영구 URL 없음 → 운영은 자체 호스팅 또는 관리형 배포.
