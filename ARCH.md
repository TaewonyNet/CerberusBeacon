# Cerberus Beacon v2 — Architecture

## 1. 모듈 구조

```
CerberusBeacon/
├── main.py                  # CLI 진입점
├── cerberus_ctl.py          # CLI 에이전트 (stdlib only)
├── telegram_daemon.py       # 텔레그램 봇 독립 프로세스 진입점
├── pyproject.toml           # 패키지 메타데이터 및 의존성
├── src/
│   ├── config.py            # Config 데이터클래스, 로드/저장, CLI 파싱
│   ├── i18n.py              # 다국어 지원 (한국어/영어)
│   ├── auth.py              # TOTP 인증, 브루트포스 방지, 세션 쿠키
│   ├── tunnel.py            # Cloudflare 터널 관리, 유휴 워치독, 다국어 반환 문자열
│   ├── terminal.py          # terminado WebSocket 핸들러, 세션 로그
│   ├── agent.py             # API 토큰 인증, 에이전트 세션 API
│   ├── activity.py          # 서버 전체 유휴 시간 추적
│   ├── files.py             # 파일 트리/다운로드/업로드 핸들러
│   ├── macros.py            # 매크로 CRUD API
│   ├── metrics.py           # CPU/MEM/DISK 수집
│   ├── web.py               # Tornado 앱 빌드, 라우팅, run_server
│   ├── templates/
│   │   ├── main.html        # 메인 UI HTML 템플릿 (외부 파일)
│   │   ├── main.js          # 메인 UI JS (외부 파일)
│   │   └── terminal.js      # 터미널 탭/세션 JS (외부 파일)
│   └── bots/
│       ├── telegram_bot.py  # 텔레그램 봇 (src.tunnel 직접 사용)
│       └── slack_bot.py     # Slack 봇
└── tests/
    └── ...
```

## 2. 프로세스 모델

```
OS
├── cloudflared (독립 프로세스, start_new_session=True)
│   └── tunnels.json 으로 상태 공유
├── Python: Cerberus 서버 (main.py)
│   ├── Tornado IOLoop (메인 스레드)
│   ├── idle-watchdog (데몬 스레드, 5초 주기 — 터널 유휴 감시 + 메트릭 병렬 폴링)
│   ├── server-idle-shutdown (데몬 스레드, 60초 주기 — 서버 유휴 감시, 종료 시 ⏱터널 차단)
│   └── log-cleanup (데몬 스레드, 1시간 주기 — 세션 로그 정리)
└── Python: 텔레그램 봇 (telegram_daemon.py, start_new_session=True)
    └── 30초 폴링 (tunnels.json 직접 읽기)
```

- 세 프로세스는 독립적으로 동작. 서버가 죽어도 터널·봇은 유지.
- 터널 상태는 `~/.cerberus/tunnels.json` 파일로 공유.
- 봇은 HTTP API 미사용 — `src.tunnel` 함수를 직접 임포트.

## 3. 설정 우선순위

```
CLI 인자  >  OS 환경변수  >  .env 파일  >  기본값
```

`.env`는 모듈 로드 시 `_load_dotenv()`가 미설정 키만 `os.environ`에 채움(기존 OS 환경변수 우선).  
경로는 `CB_ENV_FILE`로 변경 가능. 전체 변수는 `.env.sample` 참고.

| CLI | 환경변수 | Config 필드 |
|-----|----------|-------------|
| `--port` | `CB_PORT` | `port` |
| `--lang` | `CB_LANG` | `lang` |
| `--tg-token` | `CB_TG_TOKEN` | `tg_token` |
| `--idle-timeout` | `CB_IDLE_TIMEOUT` | `idle_timeout` |

웹 UI 설정 모달(⚙️)·봇 `/idle`·CLI `idle` 변경은 `save_config()`로 `.env`에 영속화된다.

## 4. 인증 흐름

```
브라우저 → /  → 세션 없음 → /login
  → TOTP 6자리 입력 → 검증
  → 성공: cb_session 쿠키(1h) 발급 → / 리다이렉트
  → 실패 5회: IP 잠금 15분
```

- localhost(127.0.0.1) 접속 시 `cb_device` 쿠키(30일) 추가 발급 → 재방문 시 TOTP 스킵
  (Named Tunnel 모드도 발급 대상이나 현재 미구현)

## 5. 터널 생명주기

```
open_tunnel(port)
  → cloudflared 바이너리 확인/다운로드
  → cloudflared 프로세스 기동 (start_new_session=True)
  → 로그에서 trycloudflare.com URL + 메트릭 포트 추출 (최대 20초)
  → 초기 total_requests 폴링 → metrics_requests 기준값 설정
  → _tunnels[port] = TunnelInfo(track_activity=True)
  → tunnels.json 원자적 저장 (tmp+os.replace)

idle-watchdog (5초 주기)
  → sync_tunnels_from_file()  # 다중 프로세스 동기화
  → [시나리오2] 메트릭 병렬 폴링(ThreadPoolExecutor):
      cloudflared_tunnel_total_requests 증가 → last_activity 갱신
  → _is_alive() 좀비/종료 감지 → 제거 + 봇 알림
  → track_activity=True + idle_timeout 초과 → _kill_cf() + 제거 + 봇 알림

toggle_tunnel_lock(port)
  → track_activity 토글
  → True=자동종료 활성(⏱), False=영구고정(🔒)
```

**`last_activity` 갱신 시점:**
- 시나리오1: 인증된 HTTP 요청(`touch_tunnels()`) + 터미널 PTY stdout(`on_pty_read`)
- 시나리오2: cloudflared 메트릭 폴링 — 외부 서비스(Jupyter/Nginx 등) 터널의 실 트래픽 감지

**서버 유휴 자동 종료(`server-idle-shutdown`):** `CB_SERVER_IDLE_MINUTES` 경과 시
`close_idle_tunnels()`로 자동종료(⏱) 터널 차단 후 IOLoop 정지. 영구(🔒) 터널은 유지.
(SIGINT/SIGTERM 종료는 터널을 유지하여 재시작 복원)

## 6. i18n 아키텍처

### 6.1 지원 언어

| 코드 | 언어 |
|------|------|
| `ko` | 한국어 (기본값) |
| `en` | English |

### 6.2 언어 결정 우선순위

```
--lang CLI  >  CB_LANG 환경변수  >  .env  >  OS 로케일  >  ko
```

OS 로케일 감지 순서: `LANG` → `LANGUAGE` → `LC_ALL` → `LC_MESSAGES` → `locale.getdefaultlocale()`

### 6.3 번역 파일 구조

`src/i18n.py` 단일 파일로 관리:

```python
_STRINGS = {
    "ko": {
        "btn_save": "저장",
        ...
        "js": {                        # JS 런타임 문자열
            "toast_saved": "저장됨",
            ...
        }
    },
    "en": { ... }
}
```

- **`js` 서브딕셔너리**: 브라우저 JS에서 동적으로 사용하는 문자열  
  → `const I18N = {...}` 로 HTML에 주입됨  
  → JS 코드에서 `I18N.toast_saved` 형식으로 참조

- **최상위 키**: HTML 정적 텍스트 (버튼 라벨, 모달 제목 등)  
  → `[[key]]` 마커로 HTML 템플릿에 삽입  
  → 서버 렌더 시 `apply_html()` 로 치환

### 6.4 봇 및 터널 i18n

웹 UI 외에 두 곳에서 별도 딕셔너리로 다국어를 관리한다.

**`src/tunnel.py` — `_TN` 딕셔너리:**  
`open_tunnel`, `close_tunnel`, `tunnel_status` 등 함수의 반환 문자열.

```python
_lang: str = ""          # web.py/telegram_bot.py 에서 주입
_TN = {"ko": {...}, "en": {...}}
def _S() -> dict: return _TN.get(_lang, _TN["en"])
```

`web.py` 기동 시 `_tunnel_mod._lang = cfg.lang` 설정.  
텔레그램 봇은 `--lang` CLI 인자 → `_tunnel._lang = lang` 설정.

**`src/bots/telegram_bot.py` — `_TG_STRINGS` 딕셔너리:**  
봇 명령 응답 메시지. `T = _TG_STRINGS.get(lang, _TG_STRINGS["en"])`.  
모든 `cmd_*` 함수는 `T["key"].format(...)` 패턴 사용.

### 6.5 렌더링 파이프라인

```python
# MainHandler.get()
lang = resolve_lang(cfg.lang)           # 1. 언어 결정
strings = get_strings(lang)             # 2. 번역 딕셔너리 로드
i18n_js = get_js_const(lang)           # 3. JS const I18N = {...} JSON 생성

html = open("src/templates/main.html").read()  # 외부 파일 로드
html = html.replace("%(i18n_js)s", i18n_js)   # 4. JS 주입
html = html.replace("%(lang)s", lang)
# ... 기타 %(xxx)s 치환 ...
html = apply_html(html, strings)        # 5. [[key]] 마커 치환
```

### 6.6 언어 변경

1. **웹 UI**: 설정 모달(⚙️) → 언어 드롭다운 → 저장 → 페이지 자동 새로고침
2. **CLI**: `uv run python main.py --lang en`
3. **환경변수**: `CB_LANG=en uv run python main.py`
4. **.env**: `CB_LANG=en`

### 6.7 새 언어 추가 방법

1. `src/i18n.py`의 `_STRINGS` 딕셔너리에 새 언어 코드 키 추가 (`js` 서브딕셔너리 포함)
2. `SUPPORTED_LANGS` 리스트에 추가
3. `src/templates/main.html` 설정 모달의 `<select id="st-lang">` 에 `<option>` 추가
4. `src/config.py` `parse_args()` 의 `--lang choices` 에 추가

## 7. 데이터 흐름 요약

```
브라우저 / 에이전트
  └─ HTTPS (Cloudflare) ──→ HTTP (127.0.0.1:PORT)
       └─ Tornado
            ├─ /             MainHandler  (세션 쿠키, HTML 렌더링·i18n)
            ├─ /api/*        각 API 핸들러
            │                  · AuthMixin       = 세션 쿠키 전용 (파일·설정·매크로POST)
            │                  · AgentAuthMixin   = 세션 쿠키 또는 X-API-Token+X-OTP 2FA
            └─ /ws/*         _ActivityTermSocket (terminado WebSocket)
                               · 브라우저=쿠키 / 에이전트=토큰+OTP 2FA
                               └─ PTY ←→ /bin/bash
```

## 8. 파일 위치

| 경로 | 내용 |
|------|------|
| `.env` (작업 디렉터리) | 설정 파일 (`CB_ENV_FILE`로 경로 변경) |
| `~/.cerberus/otp_secret` | 브라우저 TOTP 시크릿 (0600) |
| `~/.cerberus/agent_otp_secret` | 에이전트 2FA OTP 시크릿 (0600) |
| `~/.cerberus/device_key` | 기기 쿠키 서명 키 (0600) |
| `~/.cerberus/api_token` | 에이전트 API 토큰 (0600) |
| `~/.cerberus/tunnels.json` | 활성 터널 상태 (원자적 저장) |
| `~/.cerberus/macros.json` | 사용자 정의 매크로 (없으면 기본값) |
| `~/.cerberus/telegram.pid` | 텔레그램 봇 PID |
| `~/.cerberus/cloudflared` | cloudflared 바이너리 (0755) |
| `~/.cerberus/tunnel_logs/` | cloudflared 로그 (`cf_{port}.log`) |
| `~/.cerberus/sessions/` | 터미널 세션 로그 |
