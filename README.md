# CerberusBeacon

> 브라우저 하나로 원격 서버에 접속. 웹 터미널 + Cloudflare 터널 관리 + TOTP 2FA.

이름의 유래: 지하 세계를 지키는 세 머리 개 **Cerberus** — 세 가지(터미널·터널·봇)를 하나로 묶는 원격 접속 게이트.

## 특징

- **Web Terminal** — 브라우저에서 xterm.js 풀 터미널 (다중 탭, 모바일 수정키 지원)
- **Cloudflare Tunnel** — 포트별 터널(Quick Tunnel) 개통/종료, 유휴 자동 종료, 잠금 토글
- **TOTP 2FA** — Google Authenticator / Authy 호환. 에이전트 API용 OTP 시크릿 별도 분리
- **Telegram 봇** — `/open`, `/close`, `/exec`, `/sessions` 등 원격 제어
- **Slack 봇** — 터널·터미널 동일 제어
- **파일 브라우저** — 파일 트리 탐색, 다운로드/업로드
- **CLI 에이전트** — `cerberus_ctl.py`로 스크립트·AI 에이전트에서 제어
- **i18n** — 한국어/영어 UI (OS 로케일 자동 감지)

## 사전 준비

> **Linux (x86-64) 전용.** cloudflared 자동 다운로드와 웹 터미널(PTY)이 Linux에만 동작합니다.  
> macOS는 cloudflared를 수동 설치하면 터미널을 제외한 대부분 기능이 동작합니다. Windows 미지원.

- **Python 3.10 이상** — 확인: `python3 --version`
- **uv** — 설치:
  ```bash
  curl -LsSf https://astral.sh/uv/install.sh | sh
  ```

## Quickstart

```bash
# 1) 저장소 클론 + 의존성 설치
git clone https://github.com/TaewonyNet/CerberusBeacon.git
cd CerberusBeacon
uv sync

# 2) 설정 파일 생성 (선택 — 기본값으로 바로 실행해도 됩니다)
cp .env.sample .env
# .env 를 열어 필요한 값만 채우기

# 3) 시작 (웹 터미널 포함)
uv run python main.py --terminal

# 4) 터미널에 QR 코드 두 개가 출력됩니다:
#    ① 브라우저 로그인용 QR → Google Authenticator에 등록 (매번 출력)
#    ② 에이전트 OTP용 QR   → 별도 항목으로 등록 (최초 1회만 출력)
# 5) http://127.0.0.1:8765 접속 → ① 번 OTP 6자리로 로그인
```

최초 실행 시 `~/.cerberus/` 에 OTP 시크릿, API 토큰이 자동 생성됩니다.

### 선택 패키지

```bash
uv sync --extra telegram   # Telegram 봇
uv sync --extra slack      # Slack 봇
uv sync --extra bots       # Telegram + Slack
uv sync --extra all        # 전체 + 개발 도구
```

## 기본 사용법

### 서버 실행

```bash
uv run python main.py --terminal                        # 웹 터미널 포함
uv run python main.py --terminal --port 9000 --lang en  # 포트·언어 지정
uv run python main.py --init                            # .env.sample → .env 복사
```

### Cloudflare 터널

웹 UI 사이드바 → **"포트 열기"** 버튼. `cloudflared` 바이너리가 없으면 자동 다운로드 후 `*.trycloudflare.com` URL 발급.

- **Quick Tunnel** (기본·현재 유일 구현): 매번 랜덤 URL, 별도 설정 없음
- **Named Tunnel** (고정 도메인): **미구현**. `CB_TUNNEL_MODE/NAME/DOMAIN` 환경변수는 예약만 되어 있고 동작하지 않음

> 모든 터널은 `track_activity=True`(⏱)로 시작해 `CB_IDLE_TIMEOUT` 경과 시 자동 종료됩니다.
> 웹 UI/봇/CLI의 잠금 토글(🔒)로 영구 유지로 전환할 수 있습니다.

### CLI 에이전트 (`cerberus_ctl.py`)

```bash
# 서버 상태
uv run python cerberus_ctl.py health                        # 서버 연결 확인
uv run python cerberus_ctl.py metrics                       # CPU/MEM/DISK 현황

# 터널 제어
uv run python cerberus_ctl.py tunnel status                 # 활성 터널 목록
uv run python cerberus_ctl.py tunnel open                   # 기본 포트 터널 개통
uv run python cerberus_ctl.py tunnel open 9000              # 지정 포트
uv run python cerberus_ctl.py tunnel close 8765             # 터널 종료
uv run python cerberus_ctl.py tunnel close all              # 전체 종료
uv run python cerberus_ctl.py tunnel lock 8765              # 영구 유지 ↔ 자동 종료 토글
uv run python cerberus_ctl.py idle                          # 유휴 타임아웃 조회
uv run python cerberus_ctl.py idle 10                       # 10분으로 변경 (0=비활성)

# 터미널 세션
uv run python cerberus_ctl.py sessions                      # 세션 목록
uv run python cerberus_ctl.py new-session                   # 새 세션 생성
uv run python cerberus_ctl.py exec "git status"             # 명령 실행 (세션 없으면 자동 생성)
uv run python cerberus_ctl.py exec "git log" --timeout 10  # 타임아웃 지정
uv run python cerberus_ctl.py delete-session <session_id>   # 세션 종료

# 기타
uv run python cerberus_ctl.py macros                        # 서버 매크로 목록
```

**환경변수:** `CERBERUS_URL`(기본 `http://127.0.0.1:8765`), `CERBERUS_TOKEN`(기본 `~/.cerberus/api_token`)

### Telegram 봇

1. [@BotFather](https://t.me/BotFather) 에서 `/newbot` → 봇 토큰 복사 (`1234567890:AAHxxx...` 형식)
2. 봇에게 아무 메시지 전송 후 [@userinfobot](https://t.me/userinfobot) 에서 Chat ID 확인
3. `.env` 에 추가:
   ```bash
   CB_TG_TOKEN=1234567890:AAHxxx...
   CB_TG_CHAT_ID=123456789
   ```

| 명령 | 동작 |
|---|---|
| `/open [port]` | 터널 개통 |
| `/close [port\|all]` | 터널 종료 |
| `/status` | 활성 터널 목록 |
| `/lock <port>` | 영구 유지 🔒 ↔ 자동 종료 ⏱ |
| `/idle [분]` | 유휴 타임아웃 조회/변경 |
| `/exec <명령>` | 서버에서 명령 실행 |
| `/sessions` | 터미널 세션 목록 |
| `/new` | 새 세션 생성 |
| `/kill <세션ID>` | 세션 종료 |

## 설정 (`.env`)

`cp .env.sample .env` 후 필요한 항목만 주석 해제. 전체 항목은 `.env.sample` 참고.

**설정 변경 방법 3가지:**
1. `.env` 직접 편집 → 서버 재시작
2. **웹 UI ⚙️** (설정 모달) → 저장 버튼 → `.env` 자동 갱신 (재시작 불필요)
3. CLI 플래그(`--port`, `--lang` 등) → 해당 실행에만 적용

**주요 환경변수와 기본값:**

| 변수 | 기본값 | 설명 |
|---|---|---|
| `CB_PORT` | `8765` | 서버 포트 |
| `CB_IDLE_TIMEOUT` | `5` | 유휴 터널 자동 종료 (분, `0`=비활성) |
| `CB_SERVER_IDLE_MINUTES` | `5` | 서버 프로세스 유휴 자동 종료 (분, `0`=비활성) |
| `CB_TERMINAL` | `0` | 웹 터미널 (`1`=활성, `--terminal` 플래그와 동일) |
| `CB_TERMINAL_SHELL` | `/bin/bash` | 터미널 기본 쉘 |
| `CB_MAX_SESSIONS` | `50` | 최대 동시 터미널 세션 수 |
| `CB_AGENT` | `1` | 에이전트 API 활성 (`0`=비활성, `/api/agent/*` 차단) |
| `CB_LANG` | `` | UI 언어 (`ko`\|`en`\|빈값=OS 자동 감지) |
| `CB_FILES_ROOT` | `~` | 파일 브라우저 루트 |
| `CB_FILES_HIDDEN` | `0` | 숨김 파일 표시 (`1`=표시) |
| `CB_FILES_EXCLUDE` | `~/.cerberus,~/.ssh` | 제외 경로 (쉼표 구분, `~/.cerberus`는 항상 차단) |
| `CB_MAX_UPLOAD_BYTES` | `104857600` | 업로드 최대 크기 (100MB) |
| `CB_SESSION_HOURS` | `1` | 세션 유지 시간 (시) |
| `CB_MAX_ATTEMPTS` | `5` | 로그인 최대 실패 횟수 |
| `CB_WINDOW_MINUTES` | `5` | 실패 집계 윈도우 (분) |
| `CB_LOCKOUT_MINUTES` | `15` | 잠금 지속 시간 (분) |
| `CB_SESSION_LOG` | `1` | 터미널 세션 로그 기록 (`0`=비활성) |
| `CB_SESSION_LOG_DIR` | `~/.cerberus/sessions` | 세션 로그 디렉터리 |
| `CB_SESSION_LOG_MAX_MB` | `50` | 로그 파일 최대 크기 (MB, 초과 시 rotate) |
| `CB_SESSION_LOG_MAX_DAYS` | `7` | 로그 보관 기간 (일) |
| `CB_METRICS_DISK_PATH` | `` | 디스크 측정 경로 (빈값=`CB_FILES_ROOT`) |
| `CB_TG_TOKEN` | `` | Telegram 봇 토큰 |
| `CB_TG_CHAT_ID` | `` | Telegram Chat ID |
| `CB_SLACK_BOT` | `` | Slack xoxb- 토큰 |
| `CB_SLACK_APP` | `` | Slack xapp- 토큰 |
| `CB_SLACK_CHANNEL` | `#general` | Slack 채널 |

> `CB_TUNNEL_MODE`/`CB_TUNNEL_NAME`/`CB_TUNNEL_DOMAIN`은 예약되어 있으나 Named Tunnel 미구현으로 동작하지 않습니다.

`.env` 는 git 에 올라가지 않습니다.

**우선순위:** CLI 인자 > OS 환경변수 > `.env` > 기본값

## API 토큰 관리

```bash
uv run python main.py --show-token    # 현재 토큰 확인
uv run python main.py --rotate-token  # 토큰 재생성 (기존 즉시 무효화)
```

에이전트 API는 **API 토큰 + 에이전트 전용 OTP** 2단계 인증. 브라우저 로그인용 OTP와 완전히 분리.

## systemd 서비스 등록

```ini
# /etc/systemd/system/cerberus.service
[Unit]
Description=Cerberus Beacon
After=network.target

[Service]
Type=simple
User=<사용자>
WorkingDirectory=/path/to/CerberusBeacon
ExecStart=/path/to/CerberusBeacon/.venv/bin/python main.py --terminal
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now cerberus
sudo systemctl status cerberus
```

## 트러블슈팅

| 증상 | 원인 | 해결 |
|---|---|---|
| 터미널이 열리지 않음 | `--terminal` 플래그 누락 | `uv run python main.py --terminal` 또는 `.env` 에 `CB_TERMINAL=1` |
| cloudflared 다운로드 실패 | 네트워크 제한 | 수동 다운로드 후 `~/.cerberus/cloudflared` 에 복사 (`chmod +x`) |
| 로그인 5회 실패 잠금 | IP 기반 잠금 | 15분 후 자동 해제, 또는 서버 재시작 (메모리 기반) |
| 에이전트 인증 오류 | OTP 시크릿 불일치 | `~/.cerberus/api_token`, `~/.cerberus/agent_otp_secret` 확인 |
| 브라우저 OTP QR을 다시 보려면 | 시크릿 파일 삭제 시 재생성 | `rm ~/.cerberus/otp_secret && uv run python main.py` |

## 보안

- TOTP 2FA 필수 — 모든 브라우저 로그인
- 에이전트 API/WS는 **API 토큰 + 에이전트 OTP** 2FA 필수
- IP 기반 브루트포스 방지 (만료 카운터 자동 정리)
- 서버는 `127.0.0.1` 바인딩 — 외부 접근은 Cloudflare 암호화 터널 전용
- 에이전트 OTP 시크릿 분리 — 에이전트 환경 침해 시 브라우저 로그인은 안전
- 서버가 유휴로 자동 종료될 때 자동 종료 대상(⏱) 터널을 함께 차단 — 무감시 노출 방지 (영구🔒 터널은 유지)

자세한 내용: [SECURITY.md](SECURITY.md)

## License

[MIT](LICENSE) © 2026 CerberusBeacon contributors

### 서드파티 라이선스

CerberusBeacon은 어떤 서드파티 컴포넌트도 저장소에 번들하지 않습니다. 의존성은 `pip`/`uv`로 설치되거나 런타임에 내려받아 각자의 라이선스를 따릅니다. 전체 목록은 [THIRD_PARTY_LICENSES.md](THIRD_PARTY_LICENSES.md) 참고.

- 필수 의존성(tornado·pyotp·psutil·qrcode·terminado·websockets)은 모두 MIT 호환(Apache-2.0/BSD/MIT)입니다.
- **Telegram 봇**(선택)은 `python-telegram-bot`(**LGPL-3.0**)을 사용합니다. 번들·수정 없이 선택 설치하여 import만 하므로 CerberusBeacon 본체는 MIT를 유지합니다. (소스를 vendoring하면 LGPL 의무가 발생하니 주의)
- `cloudflared`(Apache-2.0)는 런타임에 Cloudflare GitHub 릴리스에서 자동 다운로드됩니다. Cloudflare Tunnel 사용은 Cloudflare 약관도 적용됩니다.
