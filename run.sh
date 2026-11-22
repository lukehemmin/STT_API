#!/usr/bin/env bash
# STT API 자체 호스팅 러너: 가상환경 생성 → 의존성 설치 → (선택) 모델 프리로드 → 서버 실행
set -euo pipefail
cd "$(dirname "$0")"

HOST="${STT_HOST:-0.0.0.0}"
PORT="${STT_PORT:-8000}"
MODEL="${STT_MODEL:-small}"
DEVICE="${STT_DEVICE:-auto}"
COMPUTE="${STT_COMPUTE_TYPE:-auto}"
VENV="${STT_VENV:-.venv}"
INSTALL=1
INSTALL_ONLY=0
PRELOAD="${STT_PRELOAD:-0}"
RELOAD=0

usage() {
  cat <<'EOF'
사용법: ./run.sh [옵션]

  --model NAME        모델 (기본 small, 예: large-v3-turbo)
  --cpu               CPU 강제 (기본 auto: CUDA 있으면 GPU)
  --compute-type T    auto | float16 | int8 | int8_float16
  --host HOST         바인딩 주소 (기본 0.0.0.0)
  --port PORT         포트 (기본 8000)
  --preload           시작 전 모델 다운로드/로드
  --dev               코드 변경 시 자동 재시작
  --no-install        의존성 설치 건너뛰기
  --install-only      설치만 하고 종료
  -h, --help          이 도움말

예:
  ./run.sh --model small --preload
  ./run.sh --cpu --model base --port 9000
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --model) MODEL="$2"; shift 2 ;;
    --cpu) DEVICE="cpu"; COMPUTE="${STT_COMPUTE_TYPE:-int8}"; shift ;;
    --compute-type) COMPUTE="$2"; shift 2 ;;
    --host) HOST="$2"; shift 2 ;;
    --port) PORT="$2"; shift 2 ;;
    --preload) PRELOAD=1; shift ;;
    --dev) RELOAD=1; shift ;;
    --no-install) INSTALL=0; shift ;;
    --install-only) INSTALL_ONLY=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "알 수 없는 옵션: $1" >&2; usage; exit 1 ;;
  esac
done

if ! command -v python3 >/dev/null 2>&1; then
  echo "python3가 필요합니다." >&2
  exit 1
fi

PY="$VENV/bin/python"
PIP="$VENV/bin/pip"

if [[ ! -x "$PY" ]]; then
  echo "[run] 가상환경 생성: $VENV"
  if ! python3 -m venv "$VENV"; then
    echo "가상환경 생성 실패. python3-venv 패키지를 설치하세요 (예: sudo apt-get install -y python3-venv)." >&2
    exit 1
  fi
fi

if [[ "$INSTALL" -eq 1 ]]; then
  echo "[run] 의존성 설치"
  "$PIP" install --quiet --upgrade pip
  "$PIP" install --quiet -r requirements.txt
else
  echo "[run] 의존성 설치 건너뜀 (--no-install)"
fi

if ! command -v ffmpeg >/dev/null 2>&1; then
  echo "[run] 경고: ffmpeg가 없어 배치 전사(파일 업로드)가 실패합니다." >&2
  echo "      설치: sudo apt-get install -y ffmpeg (macOS: brew install ffmpeg)" >&2
fi

echo "[run] model=$MODEL device=$DEVICE compute=$COMPUTE"
if [[ "$DEVICE" == "auto" ]]; then
  "$PY" - <<'PY' || true
try:
    import ctranslate2
    count = ctranslate2.get_cuda_device_count()
    print(f"[run] CUDA device count: {count}", "(GPU 사용)" if count else "(CPU 사용)")
except Exception as exc:  # pragma: no cover
    print(f"[run] CUDA 확인 실패: {exc}")
PY
fi

if [[ "$INSTALL_ONLY" -eq 1 ]]; then
  echo "[run] 설치 완료"
  exit 0
fi

if [[ "$PRELOAD" == "1" ]]; then
  echo "[run] 모델 프리로드"
  STT_MODEL="$MODEL" STT_DEVICE="$DEVICE" STT_COMPUTE_TYPE="$COMPUTE" "$PY" -m app.preload
fi

export STT_MODEL="$MODEL"
export STT_DEVICE="$DEVICE"
export STT_COMPUTE_TYPE="$COMPUTE"
export STT_PRELOAD="$PRELOAD"

UVICORN_ARGS=(app.main:app --host "$HOST" --port "$PORT")
if [[ "$RELOAD" -eq 1 ]]; then
  UVICORN_ARGS+=(--reload)
fi

echo "[run] http://$HOST:$PORT  (docs: /docs)"
exec "$PY" -m uvicorn "${UVICORN_ARGS[@]}"
