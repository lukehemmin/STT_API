'''STT 웹 테스트 서버.

web/static/ 의 정적 파일(HTML/CSS/JS)만 제공한다. API 서버(기본 8000)와 별개 포트(기본 8080)에서
돌기 때문에 Colab에서는 터널을 두 개 열어 API 링크와 웹 링크를 따로 쓸 수 있다.

/config.js 가 페이지에 API 주소를 주입한다 (--api 또는 환경 변수 WEB_API_URL).
주소를 비우면 페이지가 같은 호스트의 :8000 을 기본값으로 쓰고, 입력 칸이나 ?api= 로 바꿀 수 있다.
'''

from __future__ import annotations

import argparse
import json
import os
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

STATIC_DIR = Path(__file__).resolve().parent / 'static'


class Handler(SimpleHTTPRequestHandler):
    api_base = ''

    def do_GET(self) -> None:
        if self.path.split('?', 1)[0] == '/config.js':
            body = ('window.STT_CONFIG = ' + json.dumps({'apiBase': self.api_base}) + ';').encode('utf-8')
            self.send_response(200)
            self.send_header('Content-Type', 'application/javascript; charset=utf-8')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        super().do_GET()

    def end_headers(self) -> None:
        # 터널 URL이 바뀌어도 옛 config.js 가 캐시되지 않게 한다.
        self.send_header('Cache-Control', 'no-store')
        super().end_headers()


def main() -> None:
    parser = argparse.ArgumentParser(description='STT 웹 테스트 서버')
    parser.add_argument('--host', default=os.getenv('WEB_HOST', '0.0.0.0'))
    parser.add_argument('--port', type=int, default=int(os.getenv('WEB_PORT', '8080')))
    parser.add_argument('--api', default=os.getenv('WEB_API_URL', ''),
                        help='페이지에 주입할 API 주소 (예: https://xxxx.trycloudflare.com)')
    args = parser.parse_args()

    Handler.api_base = args.api.strip().rstrip('/')
    shown = Handler.api_base or '(페이지에서 지정, 기본 같은 호스트 :8000)'
    server = ThreadingHTTPServer((args.host, args.port), partial(Handler, directory=str(STATIC_DIR)))
    print(f'[web] http://{args.host}:{args.port}  api: {shown}', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
