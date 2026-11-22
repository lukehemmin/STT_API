# 인수인계 (Handoff)

> 작성: 2026-11-22 · 저장소: `lukehemmin/STT_API` (branch `main`)
> 이 문서 하나로 다음 작업을 이어받을 수 있게 정리했다. 계획 문서보다 **현재 코드의 실제 상태**를 우선한다.

---

## 0. 한 줄 상태

**최신 코드(검증 시점 `f8fc8a7`)를 T4에서 돌려 테스트 1(배치)·2(스트리밍)·3(동시 요청)이 모두 통과했고, 프리로드 스모크(`smoke: ok`)까지 확인됐다**(§3.1). 남은 약점은 동시성 증거의 폭이 좁다는 것(§4.9)과 한국어 오디오 미검증(P2)이다.

---

## 1. 원 요구사항 (사용자 발언)

- "실시간으로 결과를 바로 바로 출력하고 싶을때도 있고, 한번에 받고 싶을때도 있잖아. 두가지를 모두 지원해줘야하고. 동시에 처리될 수도 있어야해."
- "STT 엔진은 자체호스팅(GPU 가 있는곳에서 실행되는 경우)가 있으며 Colab(Google Colab)에서 실행되는 경우가 있으니 이점 또한 내용에 포함해야할것입니다."
- "colab의 경우에는 Pro를 사용함함으로 레파지토리를 클론해서 가져와 실행할 수 있도록 노트북도 간단하게 만들어주는게 좋을것 같습니다."
- "자체호스팅에서도 실행될 수 있으니 자체호스팅에서 실행하기 쉽도록 쉘 파일을 만드는것도 좋을것 같습니다."

핵심은 **처리량이 아니라 "두 모드가 서로 간섭하지 않고 동시에 돌아가는 것"**이다.

---

## 2. 확정된 설계 결정

| 항목 | 결정 | 이유 |
|---|---|---|
| 처리 모델 | 하나의 `TranscriptionSession`/이벤트 스트림(`partial`/`final`/`done`/`error`) 위에서 batch·streaming·hybrid를 모두 처리, 전달 계층만 분리 | 두 모드를 별도 엔진으로 만들면 모델을 2개 로드하게 됨 |
| 스택 | Python(코드는 3.9+ 문법), FastAPI + uvicorn, faster-whisper(CTranslate2), ffmpeg | Colab/자체 호스팅 공통 코드 |
| 모델 | 코드 기본값 `small`. 운영 권장 `large-v3-turbo`. `fast`/`default`/`accurate` 티어는 `plan/04` 제안 단계이며 **최종 확정 안 됨** (M0 벤치 후) | T4에서 `small`은 로드 1.4~7.3초로 실측됨 |
| v1 범위 | K8s·웹훅·presigned URL·멀티테넌트 쿼터 **제외**. 일정은 연장이 아니라 축소 | 프로토타입 검증이 먼저 |
| 영속화 | 현재는 인메모리 `JobStore`. PG/Redis/S3는 `plan/02`, `plan/05`의 설계 단계 | |
| 추론 동시성 | 프로세스당 모델 1개 + `_infer_lock`으로 추론 직렬화. 동시 "접수"는 되지만 "추론"은 순차 | 단일 GPU 전제. 워커 풀은 `plan/02` 설계 |
| NFR | 리뷰가 제안한 "초당 100/50 요청" 수치는 **삭제 대상**. 원 요구는 격리이지 처리량이 아님 | |

> 리뷰(Claude Opus 5.5) 결과는 `plan/review/01-plan-review-report.md`, 조치 목록은 `plan/review/02-action-checklist.md`. C1/C2/C3/H4는 타당. 이견 3가지: ① 100/50 NFR 삭제, ② GPU 풀 분리는 GPU 2장 전제라 단일 GPU 구성과 충돌, ③ 2-pass(small partial)는 한국어 partial 품질 문제로 후순위.

---

## 3. 현재 동작 상태

### 3.1 Colab에서 실제로 검증된 것 (Tesla T4 15360MiB, Driver 580.82.07, CUDA 13.0, Python 3.13.16)

최신 실행(2026-11-22, **push 후**, 검증 커밋 `f8fc8a7`). 셀 1~12를 처음부터 다시 돌린 결과다. 실행 카운트 1~9 순차, 실패한 셀 없음.

| 단계 | 결과 |
|---|---|
| 클론 | `HEAD: f8fc8a7` — **push한 최신 커밋**. 기존 클론을 `pull --ff-only`로 갱신 |
| 의존성 | `faster-whisper 1.2.1`, `ctranslate2 4.9.0`, `av 18.1.1` — `av<19` 핀이 실제로 적용됨 |
| GPU | T4 15360MiB, Driver 580.82.07, CUDA 13.0, `get_cuda_device_count() == 1` |
| cuBLAS 가드 | `libcublas.so.12` 부재 감지 → `nvidia-cublas-cu12` + `nvidia-cudnn-cu12==9.*` 설치, `LD_LIBRARY_PATH` 설정 |
| 모델 로드 | `device: cuda`, `compute_type: float16`, `loaded in 1.4s` |
| **프리로드 스모크** | ✅ `smoke: ok (0.5s)` — §4.7 수정이 실제 GPU의 첫 추론까지 확인해 줌 |
| 서버 | `/healthz`, `/v1/info` 정상(`limits`에 `stream_encoding: pcm_s16le`, `stream_sample_rate: 16000`), `PUBLIC_URL` 발급 |
| 테스트 1 (배치) | ✅ 202 → `preprocessing` → `completed`, TEXT 정확 |
| 테스트 2 (스트리밍) | ✅ `ready` → `partial` 15개 → `final` 1 → `done`(`duration_ms` 11000, `segments` 1, `seq` 17) |
| 테스트 3 (동시) | ✅ 배치 2건 `completed`/`error=None`, 스트림 `partial` 8 + `final`/`done`, 6.0s (겹침 0.3s/0.5s — §4.9) |

→ push 전의 같은 노트북 실행은 `HEAD: 0e3c4ba`(원격에 있던 유일한 커밋)라 옛 `app/`로 돌아갔고 `smoke:` 줄이 없었다. **노트북 셀은 최신인데 클론만 옛것일 수 있다**는 사례다(§7-2).

원본 출력 중 핵심 줄. 노트북은 출력 없는 템플릿으로 유지하므로(§9) 여기에 보존한다.

```
smoke: ok (0.5s)
TEXT: And so my fellow Americans, ask not what your country can do for you, ask what you can do for your country.
[final  ] And so my fellow Americans, ask not what your country can do for you, ask what you can do for your country.
done: {'type': 'done', 'duration_ms': 11000, 'segments': 1, 'seq': 17}

동시 접수한 배치 잡: ['tr_51e8c42cdfd4', 'tr_7370966776d0']
tr_51e8c42cdfd4: status=completed progress=1.0 error=None
tr_7370966776d0: status=completed progress=1.0 error=None
스트림 이벤트: {'ready': 1, 'partial': 8, 'final': 1, 'done': 1, 'error': 0}
  tr_51e8c42cdfd4: 배치/스트림 구간 겹침 0.3s
  tr_7370966776d0: 배치/스트림 구간 겹침 0.5s
동시에 돌았던 배치: 2 건
동시 처리 완료: 배치 2건 + 스트리밍 1세션, 6.0s

(스트림 partial 진행 예시 — 11초 오디오, 100ms 프레임)
[partial] And so
[partial] And so my f-
[partial] And so, my fellow Americans, ask not-
[partial] And so, my fellow Americans, ask not what your country can do for you.
[partial] And so my fellow Americans, ask not what your country can do for you, ask what you can do for your country.
```

### 3.2 자체 호스팅

`run.sh`는 가상환경 생성 → 의존성 설치 → ffmpeg 경고 → 모델 프리로드(옵션) → uvicorn 실행까지 처리한다. **GPU가 있는 실제 리눅스 서버에서는 아직 실행해보지 않았다.**

---

## 4. 이번 세션에서 고친 문제들 (중요)

### 4.1 [해결] PyAV 19 ↔ faster-whisper 1.2.1 충돌 — 배치 전사가 전부 실패하던 원인

증상 (Colab):

```
status: preprocessing | progress: 0.0 | error: None
status: failed | progress: 0.0 | error: {'code': 'internal',
  'message': "TypeError: open() got an unexpected keyword argument 'metadata_errors'"}
```

원인: **PyAV 19가 `av.open(metadata_errors=...)` 인자를 제거**했는데(`plan`과 무관한 업스트림 변경), **faster-whisper 1.2.1은 그 인자를 계속 넘긴다.** faster-whisper는 `av>=11`로만 의존성을 걸어 상한이 없어서, PyAV 19 배포 이후 새로 설치한 환경은 전부 깨진다. 우리 배치 경로는 `to_wav16k`로 만든 WAV **경로**를 faster-whisper에 넘기므로 항상 PyAV 디코딩을 타서 100% 실패한다. 모델·GPU·ffmpeg와는 무관.

수정:
- `requirements.txt`에 `av>=11,<19` 추가 (주석으로 이유 명시)
- 노트북 셀 3이 `av` 버전을 출력하고, 19 이상이면 자동으로 `av<19` 재설치 (기존 Colab 환경 자가복구)

실측 근거 (격리 환경 A/B):

```
av 18.1.1: av.open('probe.wav', mode='r', metadata_errors='ignore') → OK, format: wav
av 19.0.2: TypeError: open() got an unexpected keyword argument 'metadata_errors'
```

`av 18.1.1`은 `cp311-abi3` manylinux 휠을 제공하므로 Colab의 Python 3.13에서도 설치된다(PyPI에서 확인).

업스트림: faster-whisper PR #1495 (커밋 `2ce7f9d`)가 PyAV 19에 맞춰 인자를 제거했다. **faster-whisper가 이 수정을 릴리스하면 `av<19` 상한을 풀어도 된다.**

### 4.2 [해결] 노트북 셀 2 비멱등 — cwd 3중 중첩

`os.chdir('STT_API')`가 상대경로라 재실행마다 `/content/STT_API/STT_API/STT_API`로 깊어졌다. 지금은 `WORK_ROOT`(`/content` 또는 현재 디렉터리)를 먼저 잡고 절대 경로로 클론한다.

### 4.3 [해결] 셀 5 재실행 시 "가짜 성공"

옛 서버가 포트 8000을 잡고 있으면 새 uvicorn은 죽는데 healthz는 **옛 서버**가 응답해 셀이 성공으로 보였다. 옛 코드로 계속 테스트하는 조용한 함정. 지금은 이전 `server`/`tunnel`을 terminate → wait → kill 하고, healthz 통과 후 `server.poll()`로 **자식 프로세스 생존**까지 확인한다.

### 4.4 [해결] 실패 원인 미노출

셀 6이 상태만 찍고 `job.error`를 숨겨서, `KeyError: 'text'`만 보이고 실제 원인은 보이지 않았다. 지금은 상태 조회에 `error`를 출력하고, `failed`면 결과 조회를 시도하지 않고 코드/메시지를 출력한다.

### 4.5 [해결] 기타

- 셀 4 힌트 문구가 `\n[힌트]`를 문자 그대로 출력하던 이스케이프 오타
- 셀 6이 `wav` 파일 크기를 검증(1KB 미만이면 재다운로드)

### 4.6 [해결] 오류 코드 정리 — 의존성/입력/미완료 구분

이전에는 ffmpeg 미설치(서버 책임)와 손상 파일(사용자 책임)이 모두 422 `invalid_audio`였고, 결과 미완료 409가 `invalid_request`였다. 지금은:

- `app/audio.py`: `ensure_ffmpeg()`가 `DependencyError("ffmpeg", ...)`를 던진다. `AudioError`는 순수 디코딩/포맷 오류만 남는다.
- `app/jobs.py`: `DependencyError` → `dependency_missing`(retryable), `AudioError` → `invalid_audio`. `job.error`에 `retryable` 필드가 추가됐다.
- `app/main.py`: ffmpeg가 없으면 `POST /v1/transcriptions`가 잡을 만들지 않고 `503 dependency_missing`으로 즉시 거절한다. `/readyz`는 `checks.ffmpeg`를 포함하고, 없으면 `503` + `status: degraded`를 반환한다. `/healthz`는 그대로 liveness(200).
- 결과 미완료 응답 코드: `invalid_request` → `job_not_ready` (409, `Retry-After: 2`).
- `plan/03-api-spec.md` 오류 코드 표에 `job_not_ready`, `dependency_missing` 추가.

**기동 자체를 거부하지는 않는다.** 스트리밍은 PCM을 직접 받아 ffmpeg를 쓰지 않으므로, ffmpeg가 없는 환경에서도 스트리밍 전용으로는 정상 동작해야 하기 때문이다. 배치 경로만 503으로 막는다.

검증(로컬, fastapi TestClient): ffmpeg 있는 PATH → `/readyz` 200 · 없는 PATH → 503 `dependency_missing` + 배치 생성 503 · 미완료 결과 → 409 `job_not_ready` + `Retry-After: 2` · `DependencyError` 발생 잡 → `failed` + `dependency_missing`(retryable).

### 4.7 [해결] Colab CUDA 13 환경에서 첫 추론이 `libcublas.so.12` 로 실패

두 번째 Colab 실행(PyAV 수정 이후)에서 모델 로드와 프리로드는 성공하는데 첫 배치만 실패:

```
status: failed | error: {'code': 'internal',
  'message': 'RuntimeError: Library libcublas.so.12 is not found or cannot be loaded'}
```

원인: ctranslate2 4.9는 **CUDA 12용** cuBLAS/cuDNN을 쓰고, 로드 시점이 아니라 **첫 `encode`에서 지연 로드**한다. Colab 이미지 Driver 580 / CUDA 13.0에는 `libcublas.so.12`가 없어서 모델 로드는 통과하고 첫 추론만 죽는다. GPU가 잡혀 있고 `compute_type: float16` 로드까지 성공하므로 환경 문제로 보이기 어렵다.

수정:
- `app/preload.py`: 모델 로드 후 **무음 1초 추론**(`vad_filter=False`)을 수행한다. 이제 `run.sh --preload`와 노트북 셀 4가 이 문제를 기동 전에 잡고 `pip install nvidia-cublas-cu12 'nvidia-cudnn-cu12==9.*'` + `LD_LIBRARY_PATH` 안내를 출력한다.
- `app/engine.py`: `transcribe_audio(..., vad_filter=)` 추가. VAD가 무음을 걸러 버리면 인코더를 안 태우므로 점검용으로는 꺼야 한다.
- 노트북 셀 3: `ctypes.CDLL`로 `libcublas.so.12`/`libcudnn.so.9` 존재를 확인하고, 없으면 pip 휠을 설치한 뒤 해당 lib 디렉터리를 `LD_LIBRARY_PATH`에 넣는다. 프리로드/서버 서브프로세스는 `os.environ`을 물려받으므로 적용된다(ipykernel 자신에는 소용없음).
- 셀 4·6 힌트: "셀 3 재실행 → **셀 5 재기동**"으로 수정. 셀 3만 다시 실행하면 이미 뜬 서버는 옛 환경 그대로다.

### 4.8 [해결] 추론이 느릴 때 partial이 폭주하던 문제

테스트 3(동시 요청)을 **스텁 엔진 + 실제 서버**로 돌려 보고 발견했다. 8초 오디오에 `partial` 이벤트가 241개, 총 293초가 걸렸다.

원인: `_maybe_partial` 이 **wall-clock 간격(700ms)만** 확인했다. 추론 한 번이 700ms보다 오래 걸리면(CPU, 큰 모델, 긴 버퍼) 다음 32ms 블록에서 조건이 이미 참이라 **매 블록마다 전체 버퍼를 다시 전사**한다. partial 비용은 오디오 길이에 비례하므로 시간이 갈수록 더 느려진다.

수정: 마지막 partial 이후 **새로 쌓인 오디오가 `partial_interval_ms`(700ms) 이상일 때만** 재계산한다(`_last_partial_samples`). 같은 조건 재실행 결과:

| | 수정 전 | 수정 후 |
|---|---|---|
| `partial` 개수(8초 오디오) | 241 | 11 |
| 총 소요 | 293.2s | 16.8s |
| 배치 2건 | completed | completed |
| 배치/스트림 구간 겹침 | 1.1s / 2.4s | 1.1s / 2.4s |

첫 partial 시점은 오디오 300ms → 700ms로 조금 늦어지지만, 폭주와 비용 증가가 사라진다.

### 4.9 [관찰] 실제 T4에서의 partial 빈도와 "동시" 겹침 폭

- 테스트 2: 11초 `jfk.wav`를 실시간(100ms)으로 보내 `partial` **15개** ≈ 0.73초당 1개. 700ms 예산이 GPU에서 지켜진다는 뜻이고, T4 `small`은 추론이 700ms보다 빨라 **wall-clock 게이트만으로도 폭주하지 않는다**. §4.8의 폭주는 느린 추론(CPU·큰 모델·긴 버퍼)에서만 재현된다.
- 테스트 3: 성공했지만 배치/스트림 **겹침이 0.3s / 0.5s**뿐이다(이전 실행 0.3/0.4 — 재현성 있는 결과). `started_at`은 접수 직후에 찍히는데 T4에서 11초 오디오 추론이 1초 미만이라 배치가 스트림 초반에 끝나 버린다. 사실은 동시지만 **폭이 좁아 동시성 증거로는 약하다**. 현재 판정은 `overlapped == 0`만 경고하므로 이 약한 경우도 통과한다. 더 긴 오디오(60~120초)나 느린 모델로 재확인하거나, 경고 임계를 1초 이상으로 올릴 것.

### 4.10 [해결] Colab이 옛 코드로 "전부 통과"를 보여주던 문제 — push 누락

증상: 노트북 3개 테스트가 모두 성공했는데 셀 2는 `HEAD: 0e3c4ba`(원격에 있던 유일한 커밋)였고 셀 4에 `smoke:` 줄이 없었다. **노트북 셀은 최신, `app/`은 9커밋 전 코드**라 `av<19` 핀·cuBLAS 스모크·partial 게이트·오류 코드 분리는 검증되지 않은 채 통과로 보였다.

원인: 로컬 커밋이 push되지 않았고, 이 문서가 "푸시 권한/자격증명이 없어 push 불가"라고 단정하고 있었다. 실제로는 자격증명이 있었다.

조치: `0e3c4ba..f8fc8a7` push → Colab에서 셀 2~12 재실행 → `HEAD: f8fc8a7`, `smoke: ok (0.5s)`, 3개 테스트 통과(§3.1). 검증 후 노트북 출력은 이 문서로 옮기고 파일은 출력 없는 템플릿으로 되돌렸다.

교훈: **"테스트 통과"를 믿기 전에 셀 2의 `HEAD:`가 검증하려는 커밋인지 먼저 확인한다.**


---

## 5. 커밋·푸시 상태

`origin/main`은 **로컬 `main`과 동기화되어 있다** — 초기 커밋 `0e3c4ba` 위로 쌓여 있던 9개 커밋과 그 뒤의 검증 기록 커밋을 2026-11-22에 push했다(§4.10).

- **이 환경에서 push가 가능하다.** credential helper에 유효한 자격증명이 설정돼 있어 `git push --dry-run`이 성공하고, 실제 push도 `0e3c4ba..f8fc8a7` fast-forward로 통과했다. 다만 push는 외부에 영향을 주는 작업이므로 **사용자 승인을 받고** 실행할 것. "자격증명이 없어 push 불가"는 이전 판의 잘못된 단정이었다.
- 커밋 footer는 `🤖 Generated with Codebuff` + `Co-Authored-By: Codebuff <noreply@codebuff.com>` 형식 유지. git 작성자는 저장소 로컬 설정만 사용(전역 미변경).
- 상태 확인:

```bash
git rev-list --left-right --count origin/main...HEAD   # 0 0 이면 동기화됨
git ls-remote origin refs/heads/main
```

- **Colab은 GitHub에서 클론한다.** push하지 않은 커밋은 아무리 로컬에서 잘 돌아도 Colab에 도달하지 않는다(§4.10, §7-2).

---

## 6. 다음 작업 (우선순위)

| 순위 | 작업 | 이유 / 완료 조건 |
|---|---|---|
| ~~P0~~ | ~~push 후 Colab에서 테스트 1·2·3 재실행~~ | ✅ 완료 — `f8fc8a7`에서 3개 테스트 + `smoke: ok` 확인 (§3.1) |
| P1 | 동시성 검증을 더 긴 오디오로 강화 | 겹침이 0.3~0.4초뿐이라 증거가 약하다(§4.9) |
| ~~P1~~ | ~~`/readyz` ffmpeg 검사 · `dependency_missing` 분리 · 409 `job_not_ready`~~ | ✅ 완료 (§4.6) |
| P2 | 한국어 오디오로 정확도/속도 확인 | 지금까지 데모는 영어 `jfk.wav`뿐. `small` 유지 여부 판단 근거 |
| P2 | partial 비용 벤치 (1초 vs 15초 vs 30초) | `plan/04` §5.1의 15초 상한 vs §4.1의 5~10분 청크 모순을 실측으로 해소 |
| P3 | 계획 문서 정합성 수정 | `plan/04` §4.1 배치 청크 5~10분 ↔ §5.1 `Transcriber` 15초 상한 / `plan/05` `ev:` TTL 1일 ↔ final 30일 |
| P3 | 노트북 셀 1의 출력 중복 | `!nvidia-smi`와 stdout 캐시트가 겹쳐 파이썬 `print` 2줄이 두 번 찍힌다. 값은 정상, 표시만 중복. `subprocess.run`으로 바꾸면 사라진다 |

---

## 7. 알려진 함정 (다음 에이전트가 밟기 쉬운 것)

1. **셀 3만 다시 돌리고 셀 5를 건너뛰면 무효다.** 실행 중인 서버 프로세스가 옛 `av`를 이미 import했으므로, 의존성을 바꾼 뒤에는 반드시 서버를 재기동해야 한다(이제 셀 5가 알아서 이전 프로세스를 종료한다).
2. **Colab 노트북은 저장소 수정이 자동 반영되지 않는다.** 사용자가 직접 올린 사본이거나, GitHub에서 새로 열어야 최신본이 적용된다. 실제로 push 전 실행은 노트북은 최신, `app/`은 `HEAD: 0e3c4ba` 옛 클론이라 **전부 통과했지만 최신 수정은 검증되지 않았다**. 셀 2가 찍는 `HEAD:`를 최신 커밋과 비교할 것.
3. **추론은 직렬화되어 있다.** `app/engine.py`의 `_infer_lock` 때문에 배치 2건 + 스트림 1건이 동시에 접수되어도 추론은 하나씩 돈다. "동시 접수·격리"는 되지만 "동시 추론"은 아니다. 이걸 개선하려면 `plan/02`의 워커 풀 설계가 필요하고 GPU 메모리 계산이 선행되어야 한다.
4. **잡 상태는 인메모리다.** 서버를 재시작하면 이전 `job_id`는 404가 된다. 오래된 `job_id`로 결과를 조회하면 "실패"가 아니라 404가 나온다.
5. **`data_dir`는 상대경로(`data`)다.** uvicorn의 cwd가 바뀌면 업로드/작업 경로도 따라 바뀐다. 자체 호스팅에서 systemd 등으로 띄울 때 주의.
6. **`requirements.txt`에 `requests`/`websockets`가 없다.** 노트북 셀 5·7이 쓰지만 Colab에는 기본 설치되어 있어 통과한다. 서버 런타임에는 필요 없다(uvicorn[standard]가 websockets 포함).
7. **`STT_API_KEY`가 없으면 인증이 꺼진 채로 뜬다.** cloudflared 공개 URL과 조합하면 누구나 쓸 수 있는 서버가 된다. 외부 노출 시 반드시 설정할 것.
8. **인증은 `?api_key=` 쿼리도 허용한다** (`app/main.py`). 로그에 키가 남을 수 있다.
9. **노트북을 실행하면 출력이 파일에 쌓인다.** 이 저장소는 출력 없는 템플릿을 유지하므로(§9), 커밋 전에 반드시 비울 것. 실측값은 이 문서 §3.1처럼 옮겨 둔다.

   ```bash
   python3 - <<'PY'
   import json
   p = 'notebooks/colab_stt_api.ipynb'
   nb = json.load(open(p, encoding='utf-8'))
   for c in nb['cells']:
       if c['cell_type'] == 'code':
           c['outputs'] = []
           c['execution_count'] = None
   open(p, 'w', encoding='utf-8').write(
       json.dumps(nb, indent=1, ensure_ascii=False, sort_keys=True) + '\n')
   PY
   ```
10. **GPU 호스트에는 CUDA 12용 cuBLAS/cuDNN이 필요하다.** ctranslate2 4.9 기준이다. CUDA 13만 있는 환경에서는 `pip install nvidia-cublas-cu12 'nvidia-cudnn-cu12==9.*'` + `LD_LIBRARY_PATH` 없이는 첫 추론이 실패한다. `python -m app.preload`(또는 `run.sh --preload`)가 이걸 조기에 잡는다.

---

## 8. 실행 방법

### 자체 호스팅

```bash
./run.sh --model small --preload     # http://localhost:8000/docs
./run.sh --cpu --model base --port 9000
./run.sh --help
```

### Colab (Pro, GPU 런타임)

`notebooks/colab_stt_api.ipynb`를 위에서부터 실행. 셀 구성:

| 셀 | 내용 |
|---|---|
| 1 | GPU/파이썬 확인, `MODEL`, `PORT` 설정 |
| 2 | 클론(+검증, 옛 클론 자동 재클론) |
| 3 | ffmpeg·의존성 설치, **av 19면 자동 다운그레이드** |
| 4 | 모델 프리로드(`python -m app.preload`) |
| 5 | 서버 기동 + cloudflared 터널 (**이전 프로세스 정리 포함**) |
| 6–7 | **테스트 1**: 배치 (설명 마크다운 + 상태·error·TEXT·SRT 출력) |
| 8–9 | **테스트 2**: 스트리밍 (WS, 100ms 청크, `partial`→`final`→`done`) |
| 10–11 | **테스트 3**: 동시 요청 (배치 2건 + 스트리밍 1건, 10분 상한) |
| 12 | 서버 로그 tail |

### 검증 명령

```bash
# 노트북 JSON 무결성 + 코드 셀 문법 (nbformat 없이)
python3 - <<'PY'
import ast, json
nb = json.load(open('notebooks/colab_stt_api.ipynb', encoding='utf-8'))
assert len(nb['cells']) == 14
for c in nb['cells']:
    if c['cell_type'] != 'code':
        continue
    assert c['outputs'] == [] and c['execution_count'] is None  # 템플릿 유지
    src = '\n'.join(
        (l[:len(l) - len(l.lstrip())] + 'pass  # ' + l.lstrip()[1:]) if l.lstrip().startswith('!') else l
        for l in ''.join(c['source']).splitlines()
    )
    # 테스트 2·3은 최상위 await를 쓴다(Colab 허용). ast.parse로는 실패한다.
    compile(src, '<cell>', 'exec', ast.PyCF_ALLOW_TOP_LEVEL_AWAIT)
print('OK')
PY
```

---

## 9. 파일 지도

```
app/
  main.py        FastAPI 엔드포인트: batch(REST+SSE), streaming(WS), 인증, 오류 envelope
  jobs.py        Job/JobStore(인메모리), BatchRunner 파이프라인
                 preprocessing → transcribing → postprocessing, _fail()이 error 기록
  engine.py      faster-whisper 래퍼. 모델 지연 로드, _infer_lock으로 추론 직렬화
  audio.py       ffmpeg 변환(to_wav16k: mono/16k/pcm_s16le), PCM→float32, AudioError
  stream.py      스트리밍 세션: 에너지 기반 VAD(512샘플=32ms 프레임), partial은 현재 버퍼를
                 재디코딩하되 새 오디오 700ms마다로 제한(§4.8), 추론은 스레드, final/done/error 발행
  config.py      환경 변수 설정(STT_*), 기본 small / auto device / 상대 data_dir
  preload.py     모델 다운로드·로드 검증 (노트북 셀 4, run.sh --preload)
  formats.py     txt / srt / vtt 직렬화
notebooks/colab_stt_api.ipynb   14셀(코드 9 + 설명 5), Colab 실행 경로. 테스트 1~3은 각각
                                앞에 무엇을 왜 확인하는지 마크다운이 붙어 있다. 출력은 커밋하지 않는
                                템플릿으로 유지한다(고친 코드와 옛 출력이 섞여 오해를 만들지 않게)
run.sh                          자체 호스팅 러너
requirements.txt                ⚠ av>=11,<19 고정 이유 주석 참고
plan/                           계획 문서 9개 + review/ 2개
HANDOFF.md                      이 문서
```

배치 파이프라인 요점: `_run()`이 `preprocessing`에서 `to_wav16k`만 수행하고, `DependencyError`(ffmpeg 없음)면 `dependency_missing`, `AudioError`(손상/포맷)면 `invalid_audio`, 그 외 예외면 `internal`로 `job.error`에 `{code, message, retryable}`을 기록한다. 결과 조회는 `status != completed`면 409 `job_not_ready` + 오류 envelope. 배치 생성은 ffmpeg가 없으면 시작 전에 503으로 거절하고, `/readyz`도 같은 검사를 `checks.ffmpeg`로 노출한다.

---

## 10. 참고: 계획 문서의 확인된 모순 (아직 미수정)

- `plan/04` §4.1 배치 청크 5~10분 ↔ §5.1 `Transcriber` 15초 상한
- `plan/05` `ev:` 이벤트 TTL 1일 ↔ final 결과 30일
- 리뷰 문서(22건 불일치)의 지적 중 상당수는 코드가 아니라 계획 문서 쪽 문제다. 코드를 고치기 전에 문서를 먼저 확정할지 판단할 것.

---

## 11. 세션 이력 (무엇을 왜 했는지)

| 시점 | 한 일 |
|---|---|
| 1차 | 요구사항 정리 → `plan/` 9개 + `plan/review/` 2개, `app/` 9모듈, `requirements.txt`, `run.sh`, Colab 노트북, 이 문서 |
| 2차 | Colab 실패 원인 추적·수정: PyAV 19(§4.1), 셀 비멱등(§4.2), 가짜 성공(§4.3), 원인 미노출(§4.4), 오류 코드 분리(§4.6), CUDA 13 cuBLAS(§4.7), partial 폭주(§4.8) |
| 3차 (2026-11-22) | T4 실행 검토 → 옛 클론(`0e3c4ba`) 발견(§4.10) → push → 최신 코드 재검증(`f8fc8a7`, `smoke: ok`, 3테스트 통과, §3.1) → 노트북 출력을 이 문서로 옮기고 템플릿 복원 → 테스트 3 겹침 관찰(§4.9) |
| 다음 | 동시성 증거 강화(P1), 한국어 오디오 검증(P2), 계획 문서 정합성(P3) |
