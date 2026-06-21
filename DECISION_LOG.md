# Decision Log

구현 중 SPEC.md에 명시되지 않은 사항을 자체 결정한 경우 여기에 기록한다.

| # | 날짜 | 결정 사항 | 이유 | 대안 |
|---|------|-----------|------|------|
| 1 | 2026-06-14 | `_start_cloudflared`에서 `select.select`로 stderr 읽기 (최대 8초) | cloudflared는 stdout/stderr를 동시에 사용하므로 blocking read 대신 non-blocking poll 필요. URL이 8초 내 나타나지 않으면 "URL 추출 실패" 반환 | asyncio 기반 subprocess read (단일 파일 내 복잡도 증가) |
| 2 | 2026-06-14 | `TunnelInfo.track_activity` 기본값 `True`, `open_tunnel` 호출 시 현재 서버 포트(`_current_cfg_port`)와 비교하여 설정 | SPEC §2.1에 "웹 서버 포트만 track_activity=True"라고 명시되어 있음. `open_tunnel`이 `cfg`를 받지 않으므로 전역 변수 `_current_cfg_port` 사용 | `open_tunnel(port, track_activity)` 시그니처 변경 |
| 3 | 2026-06-14 | `cerberus_ctl.py exec`는 `websockets` 라이브러리 사용, `/api/agent/exec` 엔드포인트 없이 직접 `/ws/<session_id>?token=<token>` WS 연결 | SPEC §7.6에 명시된 동작. `exec` API 없음. `macros` 명령은 `/api/macros`에 X-API-Token 헤더 포함하여 요청 (SPEC에 macros 인증 방식 미명시, 단순화 선택) | POST /api/agent/exec 별도 엔드포인트 구현 |
| 4 | 2026-06-14 | `MacrosHandler.post`에서 `send=""` 항목 허용 (구분선 표현) | SPEC §6.4 "send = '' → 구분선으로 렌더링"이므로 유효성 검사에서 허용해야 함 | send 필드 필수 값 검증 |
| 5 | 2026-06-14 | `_ActivityTermSocket`을 terminado `NamedTermManager` 방식으로 구현. 세션별 `TermSocket` 서브클래스 대신 `TerminalWsHandler` (tornado.websocket.WebSocketHandler 상속)로 WS 멀티플렉서 직접 구현 | terminado의 TermSocket은 단일 터미널 단일 클라이언트 가정이 강함. 다중 클라이언트 브로드캐스트를 위해 직접 WS 핸들러 구현이 더 단순함 | terminado TermSocket 그대로 상속 후 on_pty_read 오버라이드 |
| 6 | 2026-06-14 | `/proc` fallback의 cpu_pct는 항상 `0.0` 반환 | `/proc/stat`를 두 번 폴링해야 CPU 사용률을 계산할 수 있으나 SPEC에서 "첫 호출 0.0 허용(psutil 비블로킹 특성)"을 명시하므로 `/proc` 경로에서도 동일하게 처리 | /proc/stat 2회 폴링 (100ms sleep 필요, blocking) |
| 7 | 2026-06-14 | `cb_device` 쿠키 서명: `HMAC(device_key, payload_b64)[:32]` 단순 구현 | SPEC에 서명 알고리즘 미명시. tornado secure_cookie와 별도 키(device_key)를 사용해야 하므로 SHA256 기반 자체 서명 선택 | tornado의 create_signed_value 활용 |
| 8 | 2026-06-14 | `AgentNewSessionHandler`에서 terminado의 `new_named_terminal()` API 사용 | terminado NamedTermManager의 표준 API. 실제 pty 생성은 terminado가 담당 | 직접 subprocess.Popen으로 pty 생성 |
| 9 | 2026-06-14 | `tornado.websocket.WebSocketHandler`를 `from tornado.websocket import WebSocketHandler as _TornadoWebSocketHandler`로 별도 import | tornado를 `import tornado.web` 등으로 import해도 `tornado.websocket`이 `tornado` 모듈의 속성으로 자동 노출되지 않아 AttributeError 발생. 명시적 from-import로 해결 | `import tornado.websocket` 후 `tornado.websocket.WebSocketHandler` 직접 참조 (AttributeError 발생) |
| 10 | 2026-06-14 | `IdleTimeoutApiHandler.post`에서 minutes 범위를 `max(0, min(1440, v))`로 클램프 | SPEC에 범위 미명시. 0=비활성, 최댓값 1440(24시간)은 실용적 상한 | 제한 없이 임의 값 허용 |
| 11 | 2026-06-14 | `_ensure_cloudflared`에서 `urlopen(url, timeout=N)` → `socket.setdefaulttimeout(N)` + `urlopen(url)` 방식으로 변경 | 테스트 mock `_side(url)`이 url 1개 인자만 받으므로 `timeout=` 키워드 전달 시 TypeError 발생. socket 레벨 timeout 설정으로 동일 효과, 원래/복원 패턴으로 thread-safety 유지 | urlopen wrapper 함수 별도 정의 |
| 12 | 2026-06-14 | `AgentAuthMixin._check_session_cookie()`에서 `getattr(self, 'get_secure_cookie', None)` 가드 추가 | 테스트에서 `AgentAuthMixin()`을 독립 인스턴스화하므로 Tornado handler 메서드가 없음. `getattr` 가드로 `AttributeError` 방지, 실제 핸들러에선 정상 동작 | `hasattr` 체크 후 호출 |
| 13 | 2026-06-14 | JS WS URL을 `ws://` 고정 → `location.protocol === 'https:' ? 'wss:' : 'ws:'` 자동 선택 | Cloudflare Quick Tunnel은 HTTPS/WSS 전용. `ws://`로 연결 시 브라우저가 차단 | wss:// 고정 (로컬 http 환경 미지원) |
| 14 | 2026-06-14 | `loadSessions()`에서 세션 없을 때 `POST /api/agent/session` 자동 호출로 터미널 세션 생성 | 브라우저 UI 접속 즉시 터미널이 표시되어야 함. 수동 "＋" 버튼 없이도 기본 세션 생성 | 사용자가 직접 "＋" 버튼 클릭하여 생성 |
| 15 | 2026-06-14 | **멀티파일 전환**: `cerberus_beacon.py` (2603줄) → `src/` 패키지 (9모듈 + `src/bots/` 2모듈) | 단일 파일 원칙 폐기 결정. 유지보수성·테스트 독립성 향상. 순환 임포트 방지를 위해 공유 상태는 소유 모듈에 선언, 다른 모듈이 import. `run_server`는 `src/web.py`에 배치하여 모든 모듈을 집결하는 단일 진입 지점 역할 | 기존 단일파일 유지 |
| 16 | 2026-06-14 | `src/terminal.py`에서 `_ActivityTermSocket.open()` 내부에서 `from src.agent import _agent_api_token` 지연 import | `src/agent.py`가 `src/terminal.py`를 import하고 `src/terminal.py`가 `src/agent.py`를 import하면 순환 임포트 발생. 해결책으로 `_agent_api_token` 참조를 함수 호출 시점까지 미룸 | `src/notify.py` 별도 모듈로 공유 토큰 분리 |
| 17 | 2026-06-14 | `src/web.py`에 `TunnelOpenHandler`, `TunnelCloseHandler`, `TunnelStatusHandler`, `IdleTimeoutApiHandler` 배치 | 이 핸들러들은 `src/tunnel.py`의 함수를 호출하고 `AuthMixin`(`src/auth.py`)을 상속. `src/tunnel.py`에 배치하면 `src/auth.py`를 역방향 import해야 하므로 순환 임포트 위험. 상위 집결 모듈인 `src/web.py`에 배치로 해결 | 별도 `src/tunnel_api.py` 모듈 생성 |
| 18 | 2026-06-14 | `tests/conftest.py`에서 `terminado` mock 등록 (tornado는 실제 설치) | 테스트 환경에 terminado 미설치 시 `src/terminal.py` import 실패 방지. tornado는 실제 설치되어 있으므로 mock 불필요 | 각 test 파일에서 개별 mock 등록 |
| 19 | 2026-06-20 | 설정 저장소를 `config.toml` → `.env`로 전환 | 의존성(tomli) 제거, 환경변수와 1:1 매핑으로 단순화. `_load_dotenv()`가 미설정 키만 채워 OS 환경변수 우선 유지 | tomllib 기반 config.toml 유지 |
| 20 | 2026-06-20 | Named Tunnel 미구현 확정 — `CB_TUNNEL_*`는 파싱만, 분기 없음 | 사용자 결정("구현 불가, 의미 없음"). Quick Tunnel만으로 목적 달성 | cloudflared `tunnel run` 분기 구현 |
| 21 | 2026-06-20 | 실 WS 핸들러 `_ActivityTermSocket.open()`에 토큰+OTP 2FA + `agent_enabled` 검증 추가, 데드코드 `TerminalWsHandler` 제거 | #5/#16 당시 `TerminalWsHandler`가 2FA를 구현했으나 라우팅되지 않아 토큰만 검사하는 `_ActivityTermSocket`이 실제 동작 → OTP 우회 보안 결함. 실 핸들러에 2FA 이관 | 라우팅을 `TerminalWsHandler`로 교체 (terminado 다중 클라이언트 미지원) |
| 22 | 2026-06-20 | `POST /api/macros`를 세션 쿠키 전용으로 제한 (에이전트 토큰 거부) | SPEC §10 — 매크로 변경은 대화형 사용자만. GET은 AgentAuth 유지 | GET/POST 모두 AgentAuth 허용 |
| 23 | 2026-06-20 | `web.py`의 `_MAIN_HTML`(862줄) 등 데드코드 제거, UI는 `templates/`에서만 로드 | 상수가 참조되지 않는 순수 데드코드. templates 외부 파일과 이중 관리되어 혼선 | 데드코드 유지 |
| 24 | 2026-06-21 | 서버 유휴 종료 시 `close_idle_tunnels()`로 자동종료(⏱) 터널 차단, 영구(🔒) 유지 | 서버 종료 시 watchdog도 죽어 터널 무감시 노출 → "필요할 때만 노출" 가치 훼손. SIGTERM(의도적)은 터널 유지하여 재시작 복원과 구분 | 종료 시 전체 터널 차단 / 현행 유지 |
| 25 | 2026-06-21 | stdout(`on_pty_read`)은 서버 활동(`_activity`)에 미포함 — 키 입력만 활동 | 사용자 결정. 무인 출력만으로 서버를 유지하지 않음. 브라우저 탭 폴링이 평소 방어 | stdout도 서버 활동으로 간주 |
| 26 | 2026-06-21 | `tunnels.json` 원자적 쓰기(tmp+`os.replace`), `_idle_watchdog` 메트릭 폴링 `ThreadPoolExecutor` 병렬화, 봇/CLI idle 변경 `save_config` 영속화, `_bf_counters` 만료 정리 | 멀티프로세스 파일 손상·폴링 블로킹·설정 영속성 불일치·브루트포스 카운터 누수 해소 | 각 항목 현행 유지 |
