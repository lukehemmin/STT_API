#!/usr/bin/env bash
# STT 웹 테스트 UI 러너: API와 별개 포트로 정적 페이지를 제공한다 (파이썬 표준 라이브러리만 사용)
set -euo pipefail
cd "$(dirname "$0")"

HOST="${WEB_HOST:-0.0.0.0}"
PORT="${WEB_PORT:-8080}"
API="${WEB_API_URL:-}"

usage() {
  cat <<'EOF'
사용법: web/run.sh [옵션]

  --api URL     페이지에 미리 채울 API 주소 (예: http://localhost:8000)
  --host HOST   바인딩 주소 (기본 0.0.0.0)
  --port PORT   포트 (기본 8080)
  -h, --help    이 도움말

예:
  web/run.sh                          # API 주소를 비워 두면 페이지가 같은 호스트의 :8000 을 기본값으로 쓴다
  web/run.sh --api https://xxxx.trycloudflare.com --port 9090
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --api) API="$2"; shift 2 ;;
    --host) HOST="$2"; shift 2 ;;
    --port) PORT="$2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "알 수 없는 옵션: $1" >&2; usage; exit 1 ;;
  esac
done

if ! command -v python3 >/dev/null 2>&1; then
  echo "python3가 필요합니다." >&2
  exit 1
fi

exec python3 server.py --host "$HOST" --port "$PORT" --api "$API"
