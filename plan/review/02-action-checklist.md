# 계획 보완 조치 체크리스트

[01-plan-review-report.md](01-plan-review-report.md)의 발견 사항을 실행 순서대로 정리한 체크리스트. 괄호 안은 보고서 참조 ID.

## A. 문서 정비 (M0 착수 전)

- [ ] Python 버전 3.11+로 통일 — README, 02, 08 (불일치 #1)
- [ ] 스트리밍 파라미터 기본값 통일: partial 주기, 발화 종료 무음, VAD 윈도 — 04 ↔ 08 (#2, #3, #8)
- [ ] 배치 청크 길이 5~10분으로 통일 (#4)
- [ ] 동시 스트림·배치 한도를 "전역 / 키당 / 프로토타입 기본값"으로 나눠 한 표에 정의 (#5, #6)
- [ ] 진행률 단위 0.0~1.0, 옵션명 `word_timestamps`로 통일 (#9, #10)
- [ ] API에 `model`(티어) 파라미터 추가, 결과에 모델 리비전 기록 (#11)
- [ ] README의 Hybrid 설명 수정 ("partial 즉시", "올리면서 보기") (#15)
- [ ] 01 지연 목표 기준 분리: 첫 partial = 발화 시작, final = 발화 종료 (#20)
- [ ] `subscribe: true` 정의 또는 삭제 (#21)
- [ ] 무음 파일 처리 정책 통일 (#22)
- [ ] 07에 담당자·기한·상태 열 추가, D11~D18 추가 (§11)

## B. 설계 결정 (M0 진행 중 확정)

- [ ] **C1** 용량·비용 모델 문서 작성, 01 NFR을 "GPU당 단위 용량"으로 수정
- [ ] **C1** partial 비용 절감안 선택: 2-pass / LocalAgreement / degradation ladder / supersede 폐기
- [ ] **C2** 배치 청크 ↔ 추론 아이템 2단계 분리 또는 GPU 풀 분리 결정 (D12)
- [ ] **C3** 이벤트 계층 분리: 영속(PG) / 휘발(partial, Redis Pub/Sub), seq 발급 주체 결정 (D11)
- [ ] **C3** 01 수용 기준 "늦은 구독자" 문구를 영속 이벤트 기준으로 수정
- [ ] **H1** 스트리밍 세그먼트 저장 방식 결정 (`transcriptions` 통합 권장)
- [ ] **H2** 배치 SSE final 순서·불변성 정책 결정 (D13)
- [ ] **H3** 세션 상태 소유 = Session Hub, GPU 워커 = 무상태 RPC로 확정
- [ ] **D14** 브라우저 WS 인증을 단기 토큰 방식으로 결정

## C. M0 스파이크 검증 항목 (보고서 §9.1)

- [ ] V1 짧은 세그먼트 추론 비용(30초 패딩 영향)
- [ ] V2 요청 간 배치 추론 가능 여부 (CTranslate2 `generate` 직접 호출)
- [ ] V3 partial 1회 비용: small vs turbo, 배치 1/4/8
- [ ] V4 한국어 CER 기준선 (small / turbo / large-v3)
- [ ] V5 Silero VAD CPU 비용 × 100 세션
- [ ] V6 세그먼트·단어 타임스탬프 정확도 (±200 ms 기준)
- [ ] V7 prompt 사용 시 환각·반복률
- [ ] V8 ffmpeg 디코드 + VAD 스캔 처리량 (1시간·4시간)
- [ ] V9 프록시·터널 경유 WS 왕복 지연
- [ ] 결과를 `bench/RESULTS.md`에 기록하고 기본값 확정

## D. 스키마·API 보강 (M1~M2)

- [ ] `tenants`, `webhook_deliveries`, `audit_logs` 테이블 추가
- [ ] `segments` PK를 멱등 키로 변경 (`chunk_idx, local_idx` 또는 `segment_id`)
- [ ] `awaiting_upload` 상태와 업로드 만료 정리 규칙 추가
- [ ] `lock:{job_id}` → lease + heartbeat 방식
- [ ] `jobs` 파티션 정의 또는 정리 잡 방식 명시
- [ ] 엔드포인트 추가: 목록, DELETE(잡/세션), 단기 토큰, usage, health/info
- [ ] 이벤트 카탈로그 (type, 필드, 리플레이 여부, 보존)
- [ ] 에러 코드 보강 (`job_not_ready`, `idempotency_conflict`, timeout류, `server_draining` 등)
- [ ] WS close code 표
- [ ] `quota_exceeded`를 `concurrency_limit` / `usage_limit`로 분리
- [ ] Idempotency 충돌 처리와 키 보존 기간
- [ ] SSE keep-alive, `Last-Event-ID` = seq 명시
- [ ] OpenAPI + AsyncAPI 산출물

## E. 보안 (외부 노출 전 필수)

- [ ] S1 URL 소스·웹훅 SSRF 방어
- [ ] S2 브라우저 WS 단기 토큰
- [ ] S3 쿼리 `api_key`는 개발 모드에서만 허용
- [ ] S4 fail-closed 인증 (비 localhost 바인딩이면 키 필수)
- [ ] S5 ffprobe 사전 검사 + ffmpeg 격리·자원 제한
- [ ] S6 저장 데이터 암호화 (S3/PG/백업)
- [ ] S7 웹훅 시크릿 테넌트별 관리·회전
- [ ] S8 웹훅 재시도 연장 + DLQ + 재전송 API
- [ ] S9 개인정보보호법 검토 (동의 고지, 국외 이전, 파기 증적)
- [ ] S11 Colab 이용 정책 확인 (웹 서비스 호스팅 허용 여부)
- [ ] S12 모델 해시·의존성 버전 고정, 취약점 스캔

## F. 운영·품질 (M4~M5)

- [ ] Redis HA, PG 백업, RPO/RTO 정의, GPU 워커 최소 2대
- [ ] 복구 스캔 절차 구체화 (고아 청크·잡 재큐)
- [ ] SLO·에러 버짓·burn-rate 알림 정의
- [ ] 테넌트 라벨 카디널리티 관리
- [ ] 영상 시작 오프셋(PTS) 보정 규칙
- [ ] 자막 분할 규칙, 텍스트 정규화 규칙 (D16)
- [ ] 절대 CER 목표 설정 (V4 결과 기반)
- [ ] 골든셋 규모(시간 기준)와 동의·라이선스·PII 정책
- [ ] 계약 테스트 + WS 프로토콜 퍼징
- [ ] 부하 도구와 부하용 오디오 코퍼스 준비
- [ ] 2시간 세션 상한 내구 테스트

## G. 일정

- [ ] 일정 재산정 (약 9~10주) 또는 범위 축소안 확정 (보고서 §10.2)
- [ ] 06 리스크 표에 일정·GPU 확보·마이크로배칭 실현성 리스크 추가
- [ ] M1부터 PG 도입 여부 결정 (인메모리 → 영속 재작업 방지)
