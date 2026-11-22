# 인수인계 (Handoff)

> 작성: 2026-11-22 · 저장소: `lukehemmin/STT_API` (branch `main`)
> 이 문서 하나로 다음 작업을 이어받을 수 있게 정리했다. 계획 문서보다 **현재 코드의 실제 상태**를 우선한다.

---

## 0. 한 줄 상태

배치·스트리밍·Colab·자체 호스팅 경로가 모두 구현되어 있고, **Colab(T4)에서 모델 로드까지는 실제로 성공**했다. 마지막 남은 미검증 구간은 **배치 1건 완주와 스트리밍 1세션 완주**다. 마지막 실패 원인이던 PyAV 19 충돌은 수정·실측 검증했으나, Colab에서 재실행해 완주를 확인하지는 않았다.

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

| 단계 | 결과 |
|---|---|
| 클론 | `HEAD: 0e3c4ba` — push한 코드가 실제로 내려옴 |
| 의존성 | `faster-whisper 1.2.1`, `ctranslate2 4.9.0` — **Python 3.13 휠 문제 없음** |
| GPU | `ctranslate2.get_cuda_device_count() == 1` |
| 모델 로드 | `device: cuda`, `compute_type: float16`, `loaded in 1.4s` — **cuBLAS/cuDNN 정상** |
| 서버 | `/healthz`, `/v1/info` 정상, `cloudflared` 터널 공개 URL 생성 |
| 배치 잡 생성 | `POST /v1/transcriptions` → 202, job 생성 |
| 배치 전사 | **실패** (원인은 §4의 PyAV 19. 수정 완료, Colab 재검증은 미실시) |
| 스트리밍 | **미검증** (셀 7 미실행) |

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

---

## 5. 커밋 상태 — **push는 사용자가 직접 한다**

```
eb7359d  PyAV 19와 faster-whisper 1.2.x 충돌로 배치 전사가 실패하던 문제 수정   ← 로컬만
3efdf5a  노트북: 셀 재실행 시 중첩 chdir 방지, 배치 실패 원인 노출             ← 로컬만
1cf05b1  Colab 노트북: 남아 있는 옛 클론을 자동 복구하도록 셀 2 보강           ← 로컬만
0e3c4ba  STT API 프로토타입 추가 (원격 main)                                   ← origin
```

로컬이 `origin/main`보다 3커밋 앞선다. **푸시 권한/자격증명이 없어 대신 push하지 않았다.** 커밋 footer는 `🤖 Generated with Codebuff` + `Co-Authored-By: Codebuff <noreply@codebuff.com>` 형식을 유지할 것. git 작성자는 저장소 로컬 설정만 사용했다(전역 미변경).

---

## 6. 다음 작업 (우선순위)

| 순위 | 작업 | 이유 / 완료 조건 |
|---|---|---|
| **P0** | Colab에서 배치 1건 완주 확인 (셀 3 → 5 → 6) | `completed` + `TEXT:` 출력. 이게 이번 수정의 유일한 미확인 항목 |
| **P0** | Colab에서 스트리밍 1세션 완주 확인 (셀 7) | `partial` → `final` → `done` 순서 출력. 첫 검증 |
| P1 | `/readyz`에 ffmpeg 검사 추가 | 현재 `run.sh`는 경고만 하고, 실제 실패는 첫 배치에서야 드러남. ffmpeg 없으면 기동 거부가 맞다 |
| P1 | `invalid_audio` 코드 분리 | `app/jobs.py`가 `AudioError`를 전부 422 `invalid_audio`로 매핑한다. ffmpeg 미설치(서버 책임, 503 `dependency_missing`)와 손상 파일(사용자 책임, 422)이 구분 안 됨 |
| P1 | 409 코드 `job_not_ready` 분리 | `app/main.py`의 결과 미완료 응답이 `invalid_request`를 반환 중 |
| P2 | 한국어 오디오로 정확도/속도 확인 | 지금까지 데모는 영어 `jfk.wav`뿐. `small` 유지 여부 판단 근거 |
| P2 | partial 비용 벤치 (1초 vs 15초 vs 30초) | `plan/04` §5.1의 15초 상한 vs §4.1의 5~10분 청크 모순을 실측으로 해소 |
| P3 | 계획 문서 정합성 수정 | `plan/04` §4.1 배치 청크 5~10분 ↔ §5.1 `Transcriber` 15초 상한 / `plan/05` `ev:` TTL 1일 ↔ final 30일 |

---

## 7. 알려진 함정 (다음 에이전트가 밟기 쉬운 것)

1. **셀 3만 다시 돌리고 셀 5를 건너뛰면 무효다.** 실행 중인 서버 프로세스가 옛 `av`를 이미 import했으므로, 의존성을 바꾼 뒤에는 반드시 서버를 재기동해야 한다(이제 셀 5가 알아서 이전 프로세스를 종료한다).
2. **Colab 노트북은 저장소 수정이 자동 반영되지 않는다.** 사용자가 직접 올린 사본이거나, GitHub에서 새로 열어야 최신본이 적용된다.
3. **추론은 직렬화되어 있다.** `app/engine.py`의 `_infer_lock` 때문에 배치 2건 + 스트림 1건이 동시에 접수되어도 추론은 하나씩 돈다. "동시 접수·격리"는 되지만 "동시 추론"은 아니다. 이걸 개선하려면 `plan/02`의 워커 풀 설계가 필요하고 GPU 메모리 계산이 선행되어야 한다.
4. **잡 상태는 인메모리다.** 서버를 재시작하면 이전 `job_id`는 404가 된다. 오래된 `job_id`로 결과를 조회하면 "실패"가 아니라 404가 나온다.
5. **`data_dir`는 상대경로(`data`)다.** uvicorn의 cwd가 바뀌면 업로드/작업 경로도 따라 바뀐다. 자체 호스팅에서 systemd 등으로 띄울 때 주의.
6. **`requirements.txt`에 `requests`/`websockets`가 없다.** 노트북 셀 5·7이 쓰지만 Colab에는 기본 설치되어 있어 통과한다. 서버 런타임에는 필요 없다(uvicorn[standard]가 websockets 포함).
7. **`STT_API_KEY`가 없으면 인증이 꺼진 채로 뜬다.** cloudflared 공개 URL과 조합하면 누구나 쓸 수 있는 서버가 된다. 외부 노출 시 반드시 설정할 것.
8. **인증은 `?api_key=` 쿼리도 허용한다** (`app/main.py`). 로그에 키가 남을 수 있다.

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
| 6 | 배치 데모 (상태·error·TEXT·SRT 출력) |
| 7 | 스트리밍 데모 (WS, 100ms 청크) |
| 8 | 서버 로그 tail |

### 검증 명령

```bash
# 노트북 JSON 무결성 + 코드 셀 문법 (nbformat 없이)
python3 - <<'PY'
import ast, json
nb = json.load(open('notebooks/colab_stt_api.ipynb', encoding='utf-8'))
assert len(nb['cells']) == 10
for i, c in enumerate(nb['cells']):
    if c['cell_type'] != 'code':
        continue
    lines = [l[:len(l)-len(l.lstrip())] + 'pass  # ' + l.lstrip()[1:]
             if l.lstrip().startswith('!') else l
             for l in ''.join(c['source']).splitlines()]
    ast.parse('\n'.join(lines))
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
                 주기적으로 재디코딩, 모델 추론은 스레드에서 실행, final/done/error 이벤트 발행
  config.py      환경 변수 설정(STT_*), 기본 small / auto device / 상대 data_dir
  preload.py     모델 다운로드·로드 검증 (노트북 셀 4, run.sh --preload)
  formats.py     txt / srt / vtt 직렬화
notebooks/colab_stt_api.ipynb   10셀, Colab 실행 경로 (출력은 커밋하지 않는 템플릿으로 유지한다.
                                고친 코드와 옛 출력이 섞여 오해를 만들지 않게 하기 위함)
run.sh                          자체 호스팅 러너
requirements.txt                ⚠ av>=11,<19 고정 이유 주석 참고
plan/                           계획 문서 9개 + review/ 2개
HANDOFF.md                      이 문서
```

배치 파이프라인 요점: `_run()`이 `preprocessing`에서 `to_wav16k`만 수행하고, `AudioError`면 `invalid_audio`, 그 외 예외면 `internal`로 `job.error`에 `{code, message}`를 기록한다. 결과 조회는 `status != completed`면 409 + 오류 envelope.

---

## 10. 참고: 계획 문서의 확인된 모순 (아직 미수정)

- `plan/04` §4.1 배치 청크 5~10분 ↔ §5.1 `Transcriber` 15초 상한
- `plan/05` `ev:` 이벤트 TTL 1일 ↔ final 결과 30일
- 리뷰 문서(22건 불일치)의 지적 중 상당수는 코드가 아니라 계획 문서 쪽 문제다. 코드를 고치기 전에 문서를 먼저 확정할지 판단할 것.
