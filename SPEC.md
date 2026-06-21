# Cerberus Beacon v2 — 기능 명세

> 개인 내부망 역방향 터널 + 파일 관리 + 시스템 모니터링 + Web Terminal.
> Cloudflare Tunnel + TOTP 인증으로 필요할 때만 내부망을 외부에 노출.

**Python ≥ 3.10 필수.**

---

## 1. 인증 (AUTH)

### 1.1 TOTP (RFC 6238)

- 알고리즘: SHA-1, 30초 스텝, 6자리 — Google Authenticator / Authy 호환
- 시크릿 최초 생성: `~/.cerberus/otp_secret` (0600 권한, 없으면 자동 생성)
- 시작 시 `otpauth://` URI + ASCII QR 출력 (터미널). `qrcode` 없으면 URI만.
- 검증: `pyotp.TOTP(secret).verify(code, valid_window=1)`

### 1.2 Brute-force 방지

- `window_minutes` 내 `max_attempts`회 실패 시 `lockout_minutes`분 잠금 (메모리 기반 IP별 카운터)
- 잠금 중 `/login` 접속 시 남은 시간 표시
- 기본값: `max_attempts=5`, `window_minutes=5`, `lockout_minutes=15`

### 1.3 세션

- Tornado secure cookie (`set_secure_cookie`) — `cb_session` 서명 키는 랜덤 생성 후 **메모리에만** 보관
  - 서버 재시작 시 `cb_session` 무효화 (의도된 동작)
- 기본 만료: 1시간 (`CB_SESSION_HOURS=1`)
- 쿠키명: `cb_session`

### 1.4 기기 등록 쿠키 (로컬 / 고정 도메인 전용)

Quick Tunnel은 접속마다 도메인이 바뀌므로 쿠키 지속 불가.  
로컬(127.0.0.1) 접속 또는 Named Tunnel(고정 도메인) 모드에서만 기기 등록 쿠키 활성화.

```
Quick Tunnel 접속:
  TOTP 성공 → cb_session(1시간) 발급 → 끝

로컬(127.0.0.1) / Named Tunnel 접속:
  TOTP 성공 → cb_session(1시간) + cb_device(30일, signed) 발급
  재방문 시 cb_device 유효 → TOTP 스킵, cb_session만 갱신
  새 기기/브라우저 → cb_device 없음 → TOTP 필수
```

**`cb_device` 서명 키:**  
`~/.cerberus/device_key` 파일에 저장 (0600). 없으면 자동 생성.  
`cb_session` 키(메모리)와 별도 보관하므로 서버 재시작 후에도 `cb_device` 유효.

### 1.5 보호 범위

| 경로 | 인증 방식 |
|---|---|
| `/login` | 없음 |
| `/logout` | 없음 (GET → 쿠키 삭제 → `/login` 리다이렉트) |
| `/health` | 없음 |
| `/`, `/api/settings`, `/api/tree`, `/api/download`, `/api/upload` | 세션 쿠키 전용 |
| `/api/tunnel/*`, `/api/idle-timeout`, `/api/metrics`, `/api/macros GET`, `/api/agent/*` | AgentAuth (세션 쿠키 또는 X-API-Token + X-OTP) |
| `/api/macros POST` | 세션 쿠키 전용 (에이전트 불가) |
| `/ws/<session_id>` | AgentAuth |

세션 없으면 → `/login` 리다이렉트 (WebSocket 요청은 401).

### 1.6 인증 흐름

```
브라우저 접속 → 세션 없음 → /login
  → OTP 6자리 입력 → 검증 → 실패: 재표시 + 카운터
  → 성공: cb_session 발급 (로컬/Named Tunnel이면 cb_device도) → / 리다이렉트

5회 실패 → 잠금 → 15분 후 해제
```

---

## 2. 멀티 터널 (TUNNEL)

### 2.1 상태 구조

```python
@dataclass
class TunnelInfo:
    pid: int                       # cloudflared PID (서버 재시작 후에도 유효)
    url: str                       # https://xxx.trycloudflare.com
    port: int
    proc: Optional[subprocess.Popen] = None  # 이번 세션에서 기동한 경우만
    opened_at: float = field(default_factory=time.time)
    last_activity: float = field(default_factory=time.time)
    track_activity: bool = True    # False면 watchdog 자동 종료 제외 (🔒 영구 유지)
    metrics_port: int = 0          # cloudflared 메트릭 서버 포트 (0=미확인)
    metrics_requests: int = 0      # 마지막 폴링 시점 total_requests 기준값

_tunnels: dict[int, TunnelInfo] = {}  # key: local port
_tlock = threading.Lock()
```

**`track_activity` 규칙:**
- 기본값 `True` — 유휴 타임아웃 watchdog 대상
- `toggle_tunnel_lock(port)` 으로 `False`(🔒 영구 유지) ↔ `True`(⏱ 자동 종료) 전환

**`last_activity` 갱신 시점 (시나리오 1 + 2):**
- 시나리오 1 (브라우저 직접 사용): 모든 인증된 HTTP 요청(`touch_tunnels()`) + 터미널 PTY stdout 수신(`on_pty_read`, 입력 echo 포함)
- 시나리오 2 (외부 서비스 터널): 5초 주기 cloudflared 메트릭 폴링 — `cloudflared_tunnel_total_requests` 증가 시 갱신
  - Cloudflare 헬스체크는 HTTP origin 요청을 발생시키지 않으므로 오탐 없음

### 2.2 함수 API

| 함수 | 반환 타입 | 설명 |
|---|---|---|
| `open_tunnel(port)` | `str` | 터널 개통. 이미 열려 있으면 기존 URL 반환 |
| `close_tunnel(port, reason)` | `str` | 특정 포트 종료 |
| `close_all_tunnels(reason)` | `str` | 전체 종료 |
| `tunnel_status_data()` | `list[dict]` | 활성 터널 구조화 데이터 (HTTP API용) |
| `tunnel_status_text()` | `str` | 활성 터널 사람이 읽는 텍스트 (봇 메시지용) |
| `touch_tunnels()` | `None` | track_activity 터널의 last_activity 갱신 (HTTP 요청 훅) |

`tunnel_status_data()` 반환 예:
```json
[
  {
    "port": 8765,
    "url": "https://xxx.trycloudflare.com",
    "idle_sec": 120,
    "opened_at": 1718000000,
    "track_activity": true
  }
]
```

### 2.3 cloudflared 비정상 종료 감지

watchdog이 5초마다 `_is_alive(pid)` 확인:
- `os.kill(pid, 0)` 성공 후 `/proc/{pid}/status`에서 `State: Z`(좀비) 검사
- 종료(또는 좀비) 감지 시 → `_tunnels`에서 제거 + `_notify_all(...)` 알림
- 자동 재시도 없음 (봇/UI로 수동 재개통)

### 2.4 Telegram / Slack 명령

명령은 **터널 제어**와 **터미널 제어** 두 그룹으로 분리.

#### 터널 제어

| 명령 | 동작 |
|---|---|
| `/open [port]` | 포트 터널 개통 (생략 시 cfg.port) |
| `/close [port\|all]` | 포트 또는 전체 종료 |
| `/status` | 활성 터널 목록 (🔒/⏱ 잠금 상태 포함) |
| `/idle [분]` | 유휴 타임아웃 조회(인자 없음) / 재설정 (웹서버 `/api/idle-timeout` 경유) |
| `/lock <port>` | `track_activity` 토글 — 🔒 영구 유지 ↔ ⏱ 유휴 자동 종료 |

#### 터미널 제어

| 명령 | 동작 |
|---|---|
| `/exec <명령>` | 명령 실행 후 stdout 반환 (세션 없으면 자동 생성) |
| `/sessions` | 활성 터미널 세션 목록 |
| `/new` | 새 터미널 세션 생성 |
| `/kill <세션ID>` | 터미널 세션 종료 |

**터미널 명령 인증:**  
봇은 `~/.cerberus/api_token` + `~/.cerberus/agent_otp_secret`(pyotp)으로  
웹서버 `/api/agent/*`와 `/ws/<session_id>?token=&otp=` 2FA 인증.

**`/exec` 구현 상세:**
1. `GET /api/agent/sessions` → 세션 없으면 `POST /api/agent/session` 자동 생성
2. `ws://127.0.0.1:{port}/ws/{session_id}?token=<token>&otp=<otp>` WS 연결
3. `["stdin", cmd + "\n"]` 전송 → stdout 수신 (200ms 무출력 또는 5초 타임아웃)
4. 결과를 텔레그램 메시지로 전송 (4096자 초과 시 앞부분 잘림)
5. `terminal_enabled=false`이면 오류 메시지 반환

**의존성 추가:** `websockets>=12` (exec WS 연결용)

**봇 시작 시 `set_my_commands`로 명령어 목록을 자동 등록한다.**  
텔레그램 `/` 입력 시 자동완성 목록에 표시됨.

**`/idle` 구현 상세:**  
봇은 독립 프로세스이므로 웹서버의 `cfg.idle_timeout`에 직접 접근 불가.  
`~/.cerberus/api_token` + OTP로 웹서버 `/api/idle-timeout` POST 경유.  
웹서버 미실행 시 오류 메시지 반환.

### 2.5 Tunnel 모드

현재 구현: **Quick Tunnel 전용** (매번 랜덤 도메인).  
Named Tunnel(고정 도메인)은 미구현 — `CB_TUNNEL_MODE`, `CB_TUNNEL_NAME`, `CB_TUNNEL_DOMAIN` 환경변수는 파싱되나 실제 분기 없음.

### 2.6 cloudflared 바이너리

- 경로: `~/.cerberus/cloudflared`
- `urllib.request` + SHA256 검증 후 설치
- Port validation: 1~65535, 이미 열린 포트 재호출 시 기존 URL 반환
- 기동 로그에서 메트릭 서버 포트 자동 파싱: `Starting metrics server on 127.0.0.1:{PORT}/metrics`
- 서버 재시작 후 복원 시: `tunnels.json`에 `metrics_port` 저장 → 없으면 로그 재파싱

### 2.7 서버 유휴 자동 종료

`CB_SERVER_IDLE_MINUTES` (기본 5분): 모든 인증된 HTTP 요청이 없는 상태가 지정 시간 초과 시 서버 프로세스 자동 종료.  
0 = 비활성. 터널 watchdog의 유휴 타임아웃(`CB_IDLE_TIMEOUT`)과는 별개.

**활동 기준:** 인증된 HTTP 요청(`AuthMixin`/`AgentAuthMixin.prepare`) + 터미널 WS stdin(`on_message`).  
터미널 stdout(`on_pty_read`)은 서버 활동에 포함되지 않음 — 무인 출력만으로는 서버를 유지하지 않음.  
(브라우저 탭이 열려 있으면 5초/10초 폴링이 활동을 갱신하므로 서버가 유지됨.)

**종료 시 터널 처리:** 서버가 유휴로 종료될 때, watchdog 데몬도 함께 죽어 터널을 감시할 주체가 사라진다.  
이를 방지하기 위해 종료 직전 `close_idle_tunnels()`로 **자동 종료 대상(⏱ track_activity=True) 터널을 차단**한다.  
영구(🔒 track_activity=False) 터널은 사용자가 명시적으로 지정한 것이므로 유지된다.  
반면 SIGINT/SIGTERM(사용자 의도적 종료)은 터널을 유지하여 재시작 시 복원한다.

---

## 3. 파일 관리 (FILES)

### 3.1 v2 스코프 (명시)

| 기능 | v2 | v3+ |
|---|---|---|
| 디렉터리 탐색 | ✅ | |
| 파일 다운로드 | ✅ | |
| 파일 업로드 | ✅ | |
| 디렉터리 생성 | ❌ | 예정 |
| 파일 삭제 | ❌ | 예정 |
| 파일 rename/move | ❌ | 예정 |
| 텍스트 파일 인라인 뷰 | ❌ | 예정 |

### 3.2 엔드포인트

| 메서드 | 경로 | 설명 |
|---|---|---|
| `GET` | `/api/tree?path=<path>[&hidden=1]` | 디렉터리 목록 JSON |
| `GET` | `/api/download?path=<path>` | 파일 스트리밍 다운로드 |
| `POST` | `/api/upload?path=<dir>` | multipart/form-data 업로드 |

### 3.3 `/api/tree` 응답

```json
{
  "path": "/home/user/project",
  "dirs":  [{"name": "src", "path": "/home/user/project/src"}],
  "files": [{"name": "main.py", "path": "/home/user/project/main.py", "size": 4096}]
}
```

- 숨김 파일(`.`으로 시작) 기본 제외, `?hidden=1` 시 포함
- `os.scandir` 사용 (재귀 없음)
- 경로 없음 → 404 JSON `{"error": "not found"}`

### 3.4 보안

**경로 traversal 방지:**
```python
def _safe_path(requested: str, root: Path) -> Path:
    real = Path(os.path.realpath(requested))
    if not str(real).startswith(str(root.resolve())):
        raise ValueError("path outside FILES_ROOT")
    return real
```

**제외 경로:**
- `~/.cerberus/` — **항상 차단 (하드코딩)**. 환경변수에서 제거해도 차단 유지.
- `CB_FILES_EXCLUDE` 로 추가 경로 지정 가능 (기본: `["~/.cerberus", "~/.ssh"]`)

**`upload?path=<dir>` 검증:** 대상 디렉터리도 _safe_path 통과 필요.

**동일 파일명 충돌 정책:** 덮어쓰기(overwrite). 클라이언트가 사전 확인 책임.

### 3.5 UI

- 사이드바 파일 패널: 클릭 펼침/접힘
- 파일 클릭 → `/api/download` 트리거
- 드래그앤드롭 업로드 → 파일 트리에서 **가장 마지막으로 클릭(선택)한 디렉터리**에 저장
- 현재 선택 디렉터리는 UI 상단에 "업로드 대상: /path/to/dir" 표시
- 선택 디렉터리가 없으면 `[upload]` 버튼 비활성 + 툴팁 "디렉터리를 먼저 선택하세요"

---

## 4. 시스템 메트릭 (METRICS)

### 4.1 엔드포인트

`GET /api/metrics` → JSON (인증 필요)

```json
{
  "cpu_pct": 12.3,
  "cpu_available": true,
  "mem_pct": 45.6,
  "mem_used_gb": 3.2,
  "mem_total_gb": 7.8,
  "disk_pct": 67.0,
  "disk_used_gb": 120.5,
  "disk_total_gb": 500.0,
  "load_avg": [0.5, 0.8, 1.2],
  "uptime_sec": 86400
}
```

`cpu_available`: psutil 없이 `/proc` fallback만 사용하는 경우 `false` — UI에서 "N/A" 표시.

### 4.2 구현 상세

| 항목 | psutil 경로 | /proc fallback |
|---|---|---|
| cpu_pct | `cpu_percent(interval=None)` ¹ | 0.0 (cpu_available=false) |
| mem_* | `virtual_memory()` | `/proc/meminfo` |
| disk_* | `disk_usage(disk_path)` ² | `os.statvfs(disk_path)` |
| load_avg | `os.getloadavg()` | `/proc/loadavg` |
| uptime_sec | `time.time() - psutil.boot_time()` | `/proc/uptime` |

¹ `interval=None`: 비블로킹. 첫 호출은 0.0 반환 (허용).  
² disk는 `CB_METRICS_DISK_PATH` 또는 `CB_FILES_ROOT` 기준. `/` 가 아님.  
> `/proc` fallback은 Linux 전용. macOS에서 동작하려면 psutil 필수.

### 4.3 UI

- 사이드바 메트릭 패널, 5초 polling
- CPU / MEM / DISK 프로그레스 바
- `cpu_available=false` 이면 CPU "N/A" 표시, `cpu_pct=0.0` 이면 "측정 중…" 표시

---

## 5. 터미널 (TERMINAL) — 선택

- `--terminal` CLI 플래그 **또는** `CB_TERMINAL=1` 환경변수 시 활성화
  - `terminado` 설치 여부로 자동 감지하지 않음 (명시 필수)
  - `terminado` 미설치 상태에서 활성화 시 → 시작 즉시 `RuntimeError`로 종료 + 안내 메시지
- 미활성 시: 터미널 관련 UI 숨김, `/ws/*` 404 반환
- xterm.js 5.3.0 + FitAddon 0.8.0 (CDN)
- Shell: `bash` (기본). `CB_TERMINAL_SHELL=/bin/zsh` 로 변경 가능
- 최대 세션: `CB_MAX_SESSIONS=50`

---

## 6. 키보드 매크로 (MACROS)

### 6.1 개요

터미널 세션에 자주 쓰는 문자열을 버튼 하나로 전송.  
`terminal_enabled=false`이면 매크로 패널 숨김 (터미널 없으면 전송 대상 없음).

### 6.2 저장 레이어

우선순위: **`~/.cerberus/macros.json` > 기본값 (Config.macros)**

- 서버 시작 시 `~/.cerberus/macros.json` 로드 → 없으면 `Config.macros` 기본값 사용
- `POST /api/macros` 시 메모리 + `~/.cerberus/macros.json` 동시 저장 (재시작 후에도 유지)
- 브라우저 localStorage(`cb_macros`)는 미사용 — 서버 측 저장으로 통일

### 6.3 엔드포인트

| 메서드 | 경로 | 인증 | 설명 |
|---|---|---|---|
| `GET` | `/api/macros` | AgentAuth | 현재 매크로 목록 JSON 반환 |
| `POST` | `/api/macros` | 세션 전용 | 목록 전체 교체 + `~/.cerberus/macros.json` 저장 |

응답 형식: `[{"label": "ls -la", "send": "ls -la\n"}, ...]`  
`send = ""` → 구분선으로 렌더링.

### 6.4 UI — 사이드바 매크로 패널

- 하단 바 + 사이드바 2열 그리드
- 버튼 클릭 → 활성 세션 WebSocket에 `["stdin", send]` 전송
- **편집 모달**: 라벨/send 편집 → 서버 저장
- **리셋**: 기본값으로 복원 (POST /api/macros)
- 우클릭 → 편집 모달 열기

### 6.5 기본 매크로

| label | send |
|---|---|
| `ls -la` | `ls -la\n` |
| `pwd` | `pwd\n` |
| `git status` | `git status\n` |
| `git log -10` | `git log --oneline -10\n` |
| `ps aux` | `ps aux \| head -20\n` |
| `df -h` | `df -h\n` |
| `free -h` | `free -h\n` |
| `python3` | `python3\n` |
| `C-c` | `\x03` |
| `C-d` | `\x04` |

---

## 7. 에이전트 인터페이스 (AGENT)

### 7.1 개요

외부 에이전트·스크립트가 브라우저 UI와 **동일한 WebSocket 프로토콜**로 터미널 세션에 접속.
별도 exec API 없음. 에이전트가 stdin을 보내면 브라우저 UI에도 실시간으로 보임.

```
Browser  ──WS──┐
               ├──→ [세션 멀티플렉서] ──→ pty (bash) ──→ stdout 브로드캐스트 → 모든 WS
Agent    ──WS──┘                                │
                                               └──→ [세션 로그 기록기] → 파일
```

`terminal_enabled=true` 시에만 동작. false면 `/ws/*` 404.

### 7.2 인증

**브라우저 경로 (세션 쿠키):**
- TOTP 세션 쿠키 (`cb_session`) — OTP 추가 불필요 (이미 TOTP 인증됨)

**에이전트 경로 (2FA):**
- `X-API-Token: <token>` + `X-OTP: <6자리>` 헤더 — 둘 다 필수
- 쿼리 파라미터: `?token=<api_token>&otp=<6자리>` — WS 등 헤더 설정 불가 환경용
- OTP는 **에이전트 전용 시크릿** (`~/.cerberus/agent_otp_secret`)으로 계산
  - 브라우저 로그인용 `otp_secret`과 **분리** — 에이전트 환경 침해 시 브라우저 로그인은 안전

**API 토큰 관리:**
- 파일: `~/.cerberus/api_token` (0600, 없으면 시작 시 자동 생성)
- 토큰 재생성: `--rotate-token` CLI 플래그 (기존 토큰 즉시 무효화)
- 조회: `--show-token` CLI 플래그

**에이전트 OTP 시크릿 관리:**
- 파일: `~/.cerberus/agent_otp_secret` (0600, 없으면 시작 시 자동 생성)
- 최초 생성 시 터미널에 otpauth:// URI + QR 출력 (별도 인증 앱에 등록)
- `cerberus_ctl.py`는 이 파일을 읽어 stdlib(`hmac`, `hashlib`, `struct`)로 TOTP 자동 계산 → pyotp 불필요

> **`?token&otp` 보안 주의:** 쿼리 파라미터는 서버 액세스 로그·프록시에 기록됨.  
> 127.0.0.1 로컬 연결에서만 사용 권장.

**HTTP 에이전트 엔드포인트 (`/api/agent/*`, `/api/tunnel/*` 등):**  
X-API-Token + X-OTP 2FA 인증 (AgentAuth = 세션 쿠키 또는 토큰+OTP 중 하나).

**`CB_AGENT=0`으로 에이전트 API 전체 비활성화:**  
`/api/agent/*` → 503, `/ws/` 에이전트 토큰 인증 거부. 브라우저 세션 쿠키는 정상 동작.

### 7.3 WebSocket 멀티플렉서

**연결:** `ws://127.0.0.1:8765/ws/<session_id>`

- 동일 session_id에 N개 클라이언트 동시 연결 가능
- stdin: 어느 클라이언트에서 보내도 pty로 전달
- stdout: 연결된 모든 클라이언트에게 브로드캐스트
- 브라우저에서 에이전트 타이핑이 실시간으로 보임

**프로토콜 (기존 동일):**
```
Client → Server: ["stdin", "<text>"]
Client → Server: ["set_size", <rows>, <cols>]
Server → Client: ["stdout", "<text>"]
```

**주의 사항:**
- 브라우저 + 에이전트가 동시에 stdin 전송하면 pty에서 뒤섞임
- 권장: 에이전트는 **전용 세션**을 생성하여 사용 (`POST /api/agent/session`)
- 잠금 없음 — 동시 stdin은 미정의 동작으로 문서화

### 7.4 HTTP 에이전트 엔드포인트

`AgentAuthMixin` — X-API-Token+OTP 또는 세션 쿠키 둘 다 허용.  
`AuthMixin` — 세션 쿠키 전용 (에이전트 토큰 거부).

| 메서드 | 경로 | 인증 | 설명 |
|---|---|---|---|
| `GET` | `/api/agent/sessions` | AgentAuth | 활성 세션 목록 |
| `POST` | `/api/agent/session` | AgentAuth | 새 세션 생성 → `{"session_id": "sess_xxx"}` |
| `DELETE` | `/api/agent/session/<id>` | AgentAuth | 세션 종료 |
| `GET` | `/api/tunnel/status` | AgentAuth | 활성 터널 목록 JSON |
| `POST` | `/api/tunnel/open` | AgentAuth | `{"port": N}` |
| `POST` | `/api/tunnel/close` | AgentAuth | `{"port": N}` 또는 `{"all": true}` |
| `POST` | `/api/tunnel/lock` | AgentAuth | `{"port": N}` — track_activity 토글 |
| `GET` | `/api/idle-timeout` | AgentAuth | 현재 유휴 타임아웃 조회 |
| `POST` | `/api/idle-timeout` | AgentAuth | `{"idle_timeout": N}` (분, 0=비활성) |
| `GET` | `/api/metrics` | AgentAuth | 시스템 메트릭 JSON |
| `GET` | `/api/macros` | AgentAuth | 매크로 목록 |
| `POST` | `/api/macros` | **세션 전용** | 매크로 교체 (에이전트 불가) |
| `GET` | `/api/tree` | 세션 전용 | 파일 트리 (세션 전용) |
| `GET` | `/api/download` | 세션 전용 | 파일 다운로드 (세션 전용) |
| `POST` | `/api/upload` | 세션 전용 | 파일 업로드 (세션 전용) |
| `GET/POST` | `/api/settings` | 세션 전용 | 서버 설정 조회/변경 |

> **파일 API가 세션 전용인 이유:** 임의 경로 접근 + 다운로드/업로드 권한은 대화형 인증(TOTP) 후에만 허용.  
> 에이전트가 파일을 다뤄야 하면 `exec "cat /path"` 또는 `exec "cp ..."` 경유.

**`terminal_enabled` × `agent_enabled` 교차 동작:**

| terminal_enabled | agent_enabled | `/ws/` (브라우저) | `/ws/` (토큰) | `/api/agent/*` |
|---|---|---|---|---|
| true | true | ✅ cb_session | ✅ X-API-Token | ✅ |
| true | false | ✅ cb_session | ❌ 401 | ❌ 503 |
| false | any | ❌ 404 | ❌ 404 | ❌ 503 |

### 7.5 세션 로그 기록기

```
CB_SESSION_LOG=1                          # 활성화 (기본값)
CB_SESSION_LOG_DIR=~/.cerberus/sessions   # 저장 디렉터리
CB_SESSION_LOG_MAX_MB=50                  # 파일 최대 크기 (초과 시 .1로 rotate)
CB_SESSION_LOG_MAX_DAYS=7                 # 보관 기간 (초과 시 자동 삭제)
```

파일명 형식: `{session_id}_{date}.log` (예: `sess_abc12345_20260614.log`)

- 세션 시작 시 로그 파일 오픈 (append 모드)
- pty stdout 바이트를 그대로 기록 (ANSI escape 포함, raw)
- 세션 종료 시 파일 닫기
- `max_size_mb` 초과 시 현재 파일 `.1` 로 rename 후 새 파일 오픈 (1세대 rotate)
- watchdog이 `max_age_days` 초과 파일 삭제

### 7.6 CLI 클라이언트 (`cerberus_ctl.py`)

**의존성: `websockets>=12` (exec 명령 WS 연결용). 나머지는 stdlib.**

#### 터미널 세션

```bash
python cerberus_ctl.py sessions                         # 활성 세션 목록
python cerberus_ctl.py new-session                      # 새 세션 생성 → session_id 출력
python cerberus_ctl.py exec "ls -la"                    # 명령 실행 (세션 없으면 자동 생성)
python cerberus_ctl.py exec --session sess_abc "git log" --timeout 10
python cerberus_ctl.py delete-session sess_abc123       # 세션 종료
```

#### 터널 제어

```bash
python cerberus_ctl.py tunnel status                    # 활성 터널 목록
python cerberus_ctl.py tunnel open                      # 기본 포트(cfg.port) 터널 개통
python cerberus_ctl.py tunnel open 9000                 # 지정 포트 터널 개통
python cerberus_ctl.py tunnel close 8765               # 포트 터널 종료
python cerberus_ctl.py tunnel close all                 # 전체 터널 종료
python cerberus_ctl.py tunnel lock 8765                # track_activity 토글 (영구↔자동)
```

#### 서버 설정 조회/변경

```bash
python cerberus_ctl.py idle                             # 현재 유휴 타임아웃 조회
python cerberus_ctl.py idle 10                          # 유휴 타임아웃 10분으로 변경 (0=비활성)
python cerberus_ctl.py metrics                          # CPU / MEM / DISK 현황 출력
python cerberus_ctl.py macros                           # 서버 매크로 목록
python cerberus_ctl.py health                           # 서버 연결 상태 확인
```

**환경변수:**

| 변수 | 기본값 | 설명 |
|---|---|---|
| `CERBERUS_URL` | `http://127.0.0.1:8765` | 서버 주소 |
| `CERBERUS_TOKEN` | `~/.cerberus/api_token` 파일 | API 토큰 |

**인증:** 모든 커맨드는 `X-API-Token` + `X-OTP` 2FA 헤더 자동 전송.  
`cerberus_ctl.py`가 `~/.cerberus/agent_otp_secret`을 읽어 stdlib TOTP 자동 계산.  
의존성: `websockets>=12` (exec 전용). OTP 계산은 stdlib (`hmac`, `hashlib`, `struct`, `base64`)만 사용.

**`tunnel status` 출력 형식:**

```
🟢 포트 8765: https://xxx.trycloudflare.com ⏱ (유휴 42초)
🟢 포트 9000: https://yyy.trycloudflare.com 🔒 (영구 유지)
```

- `⏱` — track_activity=True, 유휴 타임아웃 적용
- `🔒` — track_activity=False, 영구 유지

**`metrics` 출력 형식:**

```
CPU  12.3%
MEM  45.6%  (3.2 GB / 7.8 GB)
DISK 67.0%  (120.5 GB / 500.0 GB)
LOAD 0.50 0.80 1.20
UP   86400s
```

**`exec` 동작:**
1. `/api/agent/sessions` 호출 → 세션 없으면 `/api/agent/session` POST로 생성
2. WS(`/ws/<session_id>?token=<api_token>&otp=<otp>`) 연결
3. `["stdin", cmd + "\n"]` 전송
4. stdout 수신 → 200ms 무출력 or `--timeout` 초과 시 종료
5. 수신 내용 stdout 출력 (ANSI escape는 pass-through)
6. WS 종료

---

## 8. 웹 UI

### 8.1 레이아웃

```
┌──────────────────────────────────────────────────┐
│  [≡] CERBERUS  [+ ⚙️ 🐛 ⏻]                      │  ← 헤더 (40px)
├────────────┬─────────────────────────────────────┤
│ 사이드바   │                                     │
│ (230px)   │   메인 컨텐츠 영역                   │
│ ─────────  │                                     │
│ ▶ 터널     │   terminal_enabled=true:  터미널    │
│ ▶ 메트릭  │   terminal_enabled=false: 파일 상세  │
│ ▶ 파일     │                                     │
│ ▶ 매크로  │                                     │
│ ▶ 터미널  │   (terminal_enabled 시만 표시)       │
└────────────┴─────────────────────────────────────┘
```

**메인 컨텐츠 초기 상태:**
- `terminal_enabled=true`: 터미널이 메인 영역 점유, 사이드바 터미널 패널 선택됨
- `terminal_enabled=false`: 파일 트리 상세 뷰, 사이드바 파일 패널 선택됨

### 8.2 사이드바 구조 — 아코디언

탭 아님. 아코디언(개별 열기/닫기). 여러 패널 동시 열림 가능.

| 패널 | 내용 | 기본 상태 |
|---|---|---|
| 터널 | 활성 터널 목록 + URL 복사, 개통/종료 버튼 | 열림 |
| 메트릭 | CPU/MEM/DISK 바, 5초 자동 갱신 | 열림 |
| 파일 | 파일 트리, 업로드 버튼 | 열림 |
| 매크로 | 매크로 버튼 그리드 (terminal_enabled 시만) | — |
| 터미널 세션 | 터미널 탭/세션 목록 (terminal_enabled 시만) | 열림 |

### 8.3 반응형

- PC (≥1024px): `body { display: flex }` — 사이드바 항상 표시 (230px)
- 모바일 (<1024px): 사이드바 `position: fixed; transform: translateX(-100%)`, 햄버거 토글
- 모바일: 하단 바 — 특수키 3행(ESC/방향/F키) + CTRL/ALT/SHIFT modifier + 매크로 바

### 8.4 에러 표시

API 오류(4xx/5xx/네트워크 offline):
- Toast 메시지 (상단 중앙, 4초)
- 메트릭 패널: 로드 실패 시 "—" 표시, 재시도 버튼

### 8.5 헤더 버튼

| 버튼 | 동작 |
|---|---|
| `☰` (모바일) | 사이드바 토글 |
| `＋` | 새 터미널 탭 (terminal_enabled 시만) |
| `⚙️` | 설정 모달 |
| `🐛` | 디버그 패널 토글 |
| `⏻` | 로그아웃 (`/logout`) |

**디버그 패널 (3탭):**

| 탭 | 내용 |
|---|---|
| Console | 브라우저 JS `console.log` 미러 (최근 200줄) |
| Network | WS/API 요청 로그 (URL, 상태코드, 응답 ms) |
| System | `/api/metrics` 원시 JSON + `/health` 응답 |

---

## 9. 설정

### 9.1 우선순위

```
CLI 인자 > OS 환경변수 > .env 파일 > 기본값
```

`.env` 파일 없으면 기본값으로 동작 (파일 생성 안 함).  
`--init` 플래그로 `.env.sample` → `.env` 복사.  
`.env` 파일 경로 변경: `CB_ENV_FILE=/path/to/file` (OS 환경변수로만 지정).

### 9.2 환경변수 전체 목록

#### 서버

| 환경변수 | 기본값 | 설명 |
|---|---|---|
| `CB_PORT` | `8765` | 웹 서버 포트 |
| `CB_IDLE_TIMEOUT` | `5` | 터널 유휴 자동 종료 (분, 0=비활성) |
| `CB_SERVER_IDLE_MINUTES` | `5` | 서버 프로세스 유휴 자동 종료 (분, 0=비활성) |

#### 인증

| 환경변수 | 기본값 | 설명 |
|---|---|---|
| `CB_SESSION_HOURS` | `1` | 브라우저 세션 유지 시간 (시) |
| `CB_MAX_ATTEMPTS` | `5` | 로그인 최대 실패 횟수 |
| `CB_WINDOW_MINUTES` | `5` | 실패 횟수 집계 윈도우 (분) |
| `CB_LOCKOUT_MINUTES` | `15` | 잠금 지속 시간 (분) |

#### 터널

| 환경변수 | 기본값 | 설명 |
|---|---|---|
| `CB_TUNNEL_MODE` | `quick` | `quick` \| `named` (named는 미구현) |
| `CB_TUNNEL_NAME` | `""` | Named Tunnel 이름 (미구현) |
| `CB_TUNNEL_DOMAIN` | `""` | Named Tunnel 도메인 (미구현) |

#### 에이전트

| 환경변수 | 기본값 | 설명 |
|---|---|---|
| `CB_AGENT` | `1` | 에이전트 API 활성 (0=비활성) |

#### 파일 브라우저

| 환경변수 | 기본값 | 설명 |
|---|---|---|
| `CB_FILES_ROOT` | `~` | 파일 브라우저 루트 경로 |
| `CB_FILES_HIDDEN` | `0` | 숨김 파일 표시 (1=표시) |
| `CB_FILES_EXCLUDE` | `~/.cerberus,~/.ssh` | 제외 경로 (쉼표 구분, `~/.cerberus`는 항상 차단) |
| `CB_MAX_UPLOAD_BYTES` | `104857600` | 업로드 최대 크기 (100MB) |

#### 터미널

| 환경변수 | 기본값 | 설명 |
|---|---|---|
| `CB_TERMINAL` | `0` | 웹 터미널 활성 (1=활성) |
| `CB_TERMINAL_SHELL` | `/bin/bash` | 터미널 기본 쉘 |
| `CB_MAX_SESSIONS` | `50` | 최대 동시 터미널 세션 수 |

#### 세션 로그

| 환경변수 | 기본값 | 설명 |
|---|---|---|
| `CB_SESSION_LOG` | `1` | 세션 로그 기록 (0=비활성) |
| `CB_SESSION_LOG_DIR` | `~/.cerberus/sessions` | 로그 저장 디렉터리 |
| `CB_SESSION_LOG_MAX_MB` | `50` | 로그 파일 최대 크기 (MB) |
| `CB_SESSION_LOG_MAX_DAYS` | `7` | 로그 보관 기간 (일) |

#### 메트릭

| 환경변수 | 기본값 | 설명 |
|---|---|---|
| `CB_METRICS_DISK_PATH` | `""` | 디스크 측정 경로 (빈값=CB_FILES_ROOT) |

#### Telegram 봇

| 환경변수 | 기본값 | 설명 |
|---|---|---|
| `CB_TG_TOKEN` | `""` | Telegram 봇 토큰 |
| `CB_TG_CHAT_ID` | `0` | 허용 Chat ID (0=전체) |

#### Slack 봇

| 환경변수 | 기본값 | 설명 |
|---|---|---|
| `CB_SLACK_BOT` | `""` | Slack xoxb- 토큰 |
| `CB_SLACK_APP` | `""` | Slack xapp- 토큰 |
| `CB_SLACK_CHANNEL` | `#general` | 알림 채널 |

#### UI

| 환경변수 | 기본값 | 설명 |
|---|---|---|
| `CB_LANG` | `""` | UI 언어 (`ko` \| `en` \| 빈값=OS 자동 감지) |

### 9.3 런타임 파일 (`~/.cerberus/`)

| 파일 | 권한 | 내용 |
|---|---|---|
| `otp_secret` | 0600 | TOTP base32 시크릿 — **브라우저 로그인 전용** |
| `agent_otp_secret` | 0600 | TOTP base32 시크릿 — **에이전트 2FA 전용** |
| `api_token` | 0600 | 에이전트 API 토큰 |
| `device_key` | 0600 | cb_device 쿠키 서명 키 (로컬/Named Tunnel) |
| `cloudflared` | 0755 | 바이너리 캐시 |
| `macros.json` | — | 사용자 정의 매크로 (없으면 기본값 사용) |
| `tunnels.json` | — | 살아있는 터널 상태 (서버 재시작 복원용) |
| `telegram.pid` | — | 텔레그램 봇 프로세스 PID |
| `sessions/` | 0700 | 세션 로그 디렉터리 |
| `tunnel_logs/` | — | cloudflared 로그 디렉터리 (`cf_{port}.log`) |

---

## 10. 엔드포인트 전체 목록

인증 약어: **없음** = 인증 불필요 / **세션** = TOTP 세션 쿠키 전용 / **AgentAuth** = 세션 쿠키 또는 X-API-Token+OTP

| 메서드 | 경로 | 인증 | 설명 |
|---|---|---|---|
| `GET` | `/health` | 없음 | `{"status":"ok","uptime_sec":N}` |
| `GET` | `/login` | 없음 | 로그인 폼 |
| `POST` | `/login` | 없음 | OTP 검증 → 성공 시 `/` 리다이렉트 |
| `GET` | `/logout` | 없음 | 쿠키 삭제 → `/login` 리다이렉트 |
| `GET` | `/` | 세션 | 메인 UI |
| `GET` | `/api/tunnel/status` | AgentAuth | 터널 상태 JSON |
| `POST` | `/api/tunnel/open` | AgentAuth | `{"port":N}` |
| `POST` | `/api/tunnel/close` | AgentAuth | `{"port":N}` 또는 `{"all":true}` |
| `POST` | `/api/tunnel/lock` | AgentAuth | `{"port":N}` — track_activity 토글 |
| `GET` | `/api/idle-timeout` | AgentAuth | 유휴 타임아웃 조회 |
| `POST` | `/api/idle-timeout` | AgentAuth | 유휴 타임아웃 변경 |
| `GET` | `/api/metrics` | AgentAuth | 시스템 메트릭 JSON |
| `GET` | `/api/macros` | AgentAuth | 매크로 목록 조회 |
| `POST` | `/api/macros` | **세션** | 매크로 목록 변경 |
| `GET` | `/api/tree` | 세션 | 파일 트리 JSON |
| `GET` | `/api/download` | 세션 | 파일 다운로드 |
| `POST` | `/api/upload` | 세션 | 파일 업로드 |
| `GET` | `/api/settings` | 세션 | 서버 설정 조회 |
| `POST` | `/api/settings` | 세션 | 서버 설정 변경 + `.env` 저장 |
| `ws` | `/ws/<session_id>` | AgentAuth | 터미널 WebSocket |
| `GET` | `/api/agent/sessions` | AgentAuth | 세션 목록 |
| `POST` | `/api/agent/session` | AgentAuth | 세션 생성 |
| `DELETE` | `/api/agent/session/<id>` | AgentAuth | 세션 종료 |

---

## 11. 모듈 구조

```
cerberus/
├── main.py                 # 진입점
├── cerberus_ctl.py         # 독립 CLI 클라이언트 (stdlib + websockets)
├── telegram_daemon.py      # Telegram 봇 독립 실행 진입점
├── .env.sample             # 환경변수 예시
└── src/
    ├── config.py           # 설정 로드 (.env → os.environ → 기본값)
    ├── auth.py             # TOTP + 세션 + brute-force
    ├── tunnel.py           # 멀티 터널 + watchdog + 메트릭 폴링
    ├── files.py            # 파일 트리 + 업/다운로드
    ├── metrics.py          # 시스템 메트릭
    ├── terminal.py         # 선택적 터미널 + WS 멀티플렉서
    ├── macros.py           # 키보드 매크로 + macros.json 저장
    ├── agent.py            # 에이전트 API + 세션 로그
    ├── activity.py         # 서버 활동 타임스탬프
    ├── i18n.py             # 다국어 (ko/en)
    ├── web.py              # Tornado 앱 + 라우팅 + HTML
    └── bots/
        ├── telegram_bot.py
        └── slack_bot.py
```

---

## 12. 의존성

```
# 필수
tornado>=6.3
pyotp>=2.9

# 권장
psutil>=5.9           # 메트릭 (없으면 /proc fallback)
qrcode>=7.4           # QR 출력 (없으면 URI만)

# 선택 — 터미널 + 에이전트
terminado>=0.17

# cerberus_ctl exec 명령 (WS 클라이언트)
websockets>=12

# 선택 — 봇
python-telegram-bot[all]>=21
slack-bolt>=1.18

# 개발
pytest>=7.0
```

`cerberus_ctl.py` 는 websockets 외 stdlib만 사용.  
Telegram 봇은 시스템 Python(`/usr/bin/python3`) 또는 `python-telegram-bot` 설치 환경에서 실행.

---

## 13. 테스트 계획

| 대상 | 테스트 케이스 |
|---|---|
| TOTP | 유효 코드 통과, 만료 거부, valid_window 경계 |
| Brute-force | window_minutes 내 max_attempts회 실패 → 잠금, lockout 후 해제 |
| 세션 | 쿠키 없음 → /login 리다이렉트, WS → 401 |
| 기기 쿠키 | 로컬 접속 시 cb_device 발급, 재방문 TOTP 스킵, 재시작 후 cb_device 유지 |
| Quick Tunnel | 기기 쿠키 미발급 |
| 로그아웃 | /logout → cb_session+cb_device 삭제 → /login |
| 멀티 터널 | 포트별 독립 개통/종료, thread-safety |
| track_activity | 웹서버 포트 watchdog 대상, lock 시 제외 |
| cloudflared | SHA256 일치/불일치, 좀비 프로세스 감지, proc 비정상 종료 감지 |
| 메트릭 폴링 | total_requests 증가 → last_activity 갱신, 증가 없음 → 갱신 안 함 |
| 파일 트리 | 정상, 숨김 필터, traversal 거부, ~/.cerberus 하드코딩 차단 |
| 파일 업로드 | 정상, FILES_ROOT 밖 거부, max_upload_bytes 초과 거부, 동일 파일명 덮어쓰기 |
| 메트릭 | psutil / /proc 두 경로 모두 스키마 일치 |
| 매크로 | GET AgentAuth 정상, POST 세션 없으면 403, POST 세션 있으면 저장+macros.json 갱신 |
| WS 멀티플렉서 | 다중 클라이언트 연결, stdout 브로드캐스트 |
| 에이전트 WS 인증 | 토큰 없음/불일치 → 401, 정상 → 연결 |
| agent_enabled=false | /ws/ 토큰 인증 거부, /api/agent/* 503, 브라우저 /ws/ 정상 |
| 세션 로그 | pty stdout → 파일 기록, max_size_mb rotate, max_age_days 초과 삭제 |
| cerberus_ctl | exec 정상, 세션 없을 때 자동 생성, health |
| terminal_enabled=false | /ws/* 404, /api/agent/* 503 |
| Health | 인증 없이 200 |
| 서버 유휴 종료 | CB_SERVER_IDLE_MINUTES 경과 후 서버 프로세스 종료 |
| 환경변수 | 모든 CB_* 변수 load_config() 반영 확인 |
