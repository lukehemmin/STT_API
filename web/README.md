# 웹 테스트 UI

파일(오디오·영상)을 올려 텍스트로 변환해 보는 정적 웹 페이지다. **API 서버를 수정하지 않고** API만 호출하는 클라이언트이므로, 이 폴더는 "웹에서 API를 쓰는 예제"로도 읽을 수 있다.

```
web/
  server.py        정적 파일 서버 + /config.js (API 주소 주입). 표준 라이브러리만 사용
  run.sh           server.py 실행 래퍼
  static/
    index.html     페이지
    style.css      스타일 (라이트/다크)
    app.js         API 호출: 업로드 → 폴링 → 결과(txt/srt/vtt/json)
```

## 두 개의 주소

| | 기본 포트 | 용도 |
|---|---|---|
| API 서버 (`app.main`) | 8000 | REST / SSE / WebSocket, `/docs` |
| 웹 서버 (`web/server.py`) | 8080 | 이 페이지 |

페이지는 브라우저에서 API를 **직접** 호출한다. 그래서 두 주소는 서로 다른 포트·호스트여도 되고, API 서버의 CORS(`STT_CORS_ORIGINS`, 기본 `*`)가 열려 있어야 한다.

## 실행

### 자체 호스팅

```bash
./run.sh --model small --preload      # 터미널 1: API  (:8000)
web/run.sh --api http://localhost:8000 # 터미널 2: 웹  (:8080)
```

브라우저에서 `http://localhost:8080` 을 연다. 원격 서버라면 `--api`에 **브라우저에서 접근 가능한** API 주소를 준다(서버 내부 주소가 아니다).

### Colab

`notebooks/colab_stt_api.ipynb`에서 서버 셀 다음의 **웹 테스트 UI** 셀을 실행한다. cloudflared 터널이 두 개 열리고 링크가 따로 출력된다.

```
API 링크: https://aaaa.trycloudflare.com   (/docs, REST, WebSocket)
웹 링크: https://bbbb.trycloudflare.com   (업로드 페이지, API 주소가 미리 채워짐)
```

## API 주소가 정해지는 순서

1. 주소창의 `?api=` (예: `https://bbbb.trycloudflare.com/?api=https://aaaa.trycloudflare.com`)
2. `web/server.py --api` / `WEB_API_URL` 이 주입한 `/config.js`
3. 이전에 "연결 확인"에 성공했을 때 브라우저에 저장된 값
4. 같은 호스트의 `:8000`

페이지의 "API 주소" 입력 칸에서 언제든 직접 바꿀 수 있다.

## 인증

서버에 `STT_API_KEY`를 설정했다면 페이지의 "API 키" 칸에 넣는다. 키는 요청의 `Authorization: Bearer` 헤더로만 보내며 브라우저에 저장하지 않는다.

## 제한

- 업로드·폴링만 쓴다. 실시간 스트리밍(WebSocket)과 SSE 진행률은 포함하지 않았다.
- Colab 터널 URL은 공개 주소다. 민감한 파일을 올리지 말고, 공개 노출 시에는 `STT_API_KEY`를 설정한다.
- HTTPS 페이지(터널 웹 링크)에서 `http://` API를 호출하면 브라우저가 막는다(mixed content). 터널 링크끼리 쓰거나 로컬에서는 둘 다 `http://`로 맞춘다.
