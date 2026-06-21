"""
src/i18n.py — 다국어 지원 (영어 / 한국어)
우선순위: CLI --lang > .env CB_LANG > OS 로케일 > 기본값(ko)
"""
from __future__ import annotations

import json
import locale
import os
from typing import Any

SUPPORTED_LANGS = ["ko", "en"]
_DEFAULT_LANG = "ko"

# ── 번역 사전 ──────────────────────────────────────────────────────────────────
_STRINGS: dict[str, dict[str, Any]] = {
    "ko": {
        # 헤더 버튼
        "btn_menu":           "메뉴",
        "btn_new_term":       "새 터미널",
        "btn_settings":       "설정",
        "btn_debug":          "디버그",
        "btn_logout":         "로그아웃",
        # 사이드바 섹션
        "sec_tunnel":         "터널",
        "sec_metrics":        "메트릭",
        "sec_files":          "파일",
        "sec_terminal":       "터미널 세션",
        # 공통
        "loading":            "로딩 중...",
        "btn_save":           "저장",
        "btn_cancel":         "취소",
        "btn_close":          "닫기",
        "btn_delete":         "삭제",
        "btn_copy":           "복사",
        # 로그아웃 확인
        "confirm_logout":     "로그아웃 하시겠습니까?",
        # 터널
        "btn_open_port":      "🔓 포트 열기",
        "ph_port":            "포트",
        "tunnel_port":        "포트",
        "tunnel_permanent":   "영구",
        "tunnel_lock_hint":   "영구고정 (클릭시 자동종료 활성)",
        "tunnel_url_copy":    "URL 복사",
        "tunnel_close_hint":  "터널 닫기",
        # 파일
        "upload_target_none": "업로드 대상: (선택 안 됨)",
        "btn_upload":         "업로드",
        "upload_hint":        "디렉터리를 먼저 선택하세요",
        # 특수문자 바
        "title_up":           "위 화살표",
        "title_down":         "아래 화살표",
        "title_left":         "왼쪽 화살표",
        "title_right":        "오른쪽 화살표",
        "title_pipe":         "파이프",
        "title_tilde":        "틸드",
        "title_slash":        "슬래시",
        "title_dash":         "대시",
        "title_ctrl_a":       "Ctrl+A (줄 처음)",
        "title_ctrl_e":       "Ctrl+E (줄 끝)",
        "title_ctrl_c":       "Ctrl+C (인터럽트)",
        "title_ctrl_d":       "Ctrl+D (EOF)",
        "title_ctrl_u":       "Ctrl+U (줄 삭제)",
        # 매크로 모달
        "macro_add":          "매크로 추가",
        "macro_edit":         "매크로 편집",
        "macro_edit_add":     "매크로 추가/편집",
        "macro_lbl_name":     "버튼 이름",
        "macro_lbl_send":     "전송 텍스트",
        "macro_send_hint":    r"(\n=엔터 \x03=Ctrl+C \x04=Ctrl+D)",
        "macro_ph_label":     "예: ls -la",
        "macro_ph_send":      r"예: ls -la\n",
        # 설정 모달
        "settings_title":     "⚙️ 설정",
        "lbl_tg_token":       "Telegram 봇 토큰",
        "lbl_tg_chat":        "Telegram Chat ID",
        "lbl_idle_timeout":   "유휴 타임아웃 (분, 0=비활성)",
        "lbl_term_shell":     "터미널 쉘",
        "lbl_lang":           "언어 / Language",
        # JS 동적 문자열 (const I18N 로 주입)
        "js": {
            "toast_saved":          "저장됨",
            "toast_save_fail":      "저장 실패",
            "toast_settings_saved": "설정 저장됨 (Telegram은 재시작 후 적용)",
            "toast_no_session":     "터미널 세션 없음",
            "toast_connecting":     "터미널 연결 중...",
            "toast_enter_name":     "이름을 입력하세요",
            "toast_opened":         "개통됨",
            "toast_closed":         "닫힘",
            "toast_changed":        "변경됨",
            "toast_copied":         "복사됨",
            "toast_select_dir":     "디렉터리를 먼저 선택하세요",
            "toast_upload_done":    "업로드 완료",
            "toast_upload_fail":    "업로드 실패: ",
            "toast_upload_err":     "업로드 오류: ",
            "toast_drop_done":      "드롭 업로드 완료",
            "toast_drop_fail":      "드롭 업로드 실패",
            "toast_select_dir2":    "업로드할 디렉터리를 먼저 선택하세요",
            "tunnel_none":          "활성 터널 없음",
            "upload_target":        "업로드 대상: ",
            "api_error":            "API 오류 ",
            "network_error":        "네트워크 오류: ",
            "tunnel_port":          "포트",
            "tunnel_permanent":     " — 영구",
            "tunnel_lock_hint":     "영구고정 (클릭시 자동종료 활성)",
            "tunnel_auto_hint":     "자동종료 활성 (클릭시 잠금) — 유휴 ",
            "tunnel_auto_unit":     "s",
            "toast_invalid_port":   "포트 범위: 1-65535",
            "confirm_delete_macro": "매크로를 삭제하시겠습니까?",
            "tunnel_local":         "로컬:",
            "tunnel_ext":           "외부",
            "cpu_measuring":        "측정 중…",
            "session_none":         "없음",
            "msg_all_sessions_closed": "터미널 세션이 모두 종료됐습니다.",
            "msg_close_tunnels_q":  "활성 터널({{ports}})도 함께 종료할까요?",
            "toast_tunnels_closed": "터널이 종료됐습니다.",
            "term_reconnect_failed":"[연결 끊김 — 재연결 실패]",
            "term_reconnecting":    "[재연결 중... ({{n}}/{{m}})]",
            "term_reconnect_ok":    "[재연결 성공]",
            "btn_retry":            "재시도",
            "macro_edit_add":       "매크로 추가/편집",
        },
    },
    "en": {
        # Header buttons
        "btn_menu":           "Menu",
        "btn_new_term":       "New Terminal",
        "btn_settings":       "Settings",
        "btn_debug":          "Debug",
        "btn_logout":         "Logout",
        # Sidebar
        "sec_tunnel":         "Tunnel",
        "sec_metrics":        "Metrics",
        "sec_files":          "Files",
        "sec_terminal":       "Terminal Sessions",
        # Common
        "loading":            "Loading...",
        "btn_save":           "Save",
        "btn_cancel":         "Cancel",
        "btn_close":          "Close",
        "btn_delete":         "Delete",
        "btn_copy":           "Copy",
        # Logout confirm
        "confirm_logout":     "Are you sure you want to log out?",
        # Tunnel
        "btn_open_port":      "🔓 Open Port",
        "ph_port":            "Port",
        "tunnel_port":        "Port",
        "tunnel_permanent":   "permanent",
        "tunnel_lock_hint":   "Locked (click to enable auto-close)",
        "tunnel_url_copy":    "Copy URL",
        "tunnel_close_hint":  "Close Tunnel",
        # Files
        "upload_target_none": "Upload target: (none selected)",
        "btn_upload":         "Upload",
        "upload_hint":        "Select a directory first",
        # Special bar
        "title_up":           "Arrow Up",
        "title_down":         "Arrow Down",
        "title_left":         "Arrow Left",
        "title_right":        "Arrow Right",
        "title_pipe":         "Pipe",
        "title_tilde":        "Tilde",
        "title_slash":        "Slash",
        "title_dash":         "Dash",
        "title_ctrl_a":       "Ctrl+A (beginning of line)",
        "title_ctrl_e":       "Ctrl+E (end of line)",
        "title_ctrl_c":       "Ctrl+C (interrupt)",
        "title_ctrl_d":       "Ctrl+D (EOF)",
        "title_ctrl_u":       "Ctrl+U (clear line)",
        # Macro modal
        "macro_add":          "Add Macro",
        "macro_edit":         "Edit Macro",
        "macro_edit_add":     "Add / Edit Macros",
        "macro_lbl_name":     "Button Label",
        "macro_lbl_send":     "Send Text",
        "macro_send_hint":    r"(\n=Enter \x03=Ctrl+C \x04=Ctrl+D)",
        "macro_ph_label":     "e.g. ls -la",
        "macro_ph_send":      r"e.g. ls -la\n",
        # Settings modal
        "settings_title":     "⚙️ Settings",
        "lbl_tg_token":       "Telegram Bot Token",
        "lbl_tg_chat":        "Telegram Chat ID",
        "lbl_idle_timeout":   "Idle Timeout (minutes, 0=disabled)",
        "lbl_term_shell":     "Terminal Shell",
        "lbl_lang":           "Language / 언어",
        # JS dynamic strings
        "js": {
            "toast_saved":          "Saved",
            "toast_save_fail":      "Save failed",
            "toast_settings_saved": "Settings saved (Telegram requires restart)",
            "toast_no_session":     "No terminal session",
            "toast_connecting":     "Connecting...",
            "toast_enter_name":     "Please enter a name",
            "toast_opened":         "Opened",
            "toast_closed":         "Closed",
            "toast_changed":        "Changed",
            "toast_copied":         "Copied",
            "toast_select_dir":     "Select a directory first",
            "toast_upload_done":    "Upload complete",
            "toast_upload_fail":    "Upload failed: ",
            "toast_upload_err":     "Upload error: ",
            "toast_drop_done":      "Drop upload complete",
            "toast_drop_fail":      "Drop upload failed",
            "toast_select_dir2":    "Select a target directory first",
            "tunnel_none":          "No active tunnels",
            "upload_target":        "Upload target: ",
            "api_error":            "API error ",
            "network_error":        "Network error: ",
            "tunnel_port":          "Port",
            "tunnel_permanent":     " — permanent",
            "tunnel_lock_hint":     "Locked (click to enable auto-close)",
            "tunnel_auto_hint":     "Auto-close active (click to lock) — idle ",
            "tunnel_auto_unit":     "s",
            "toast_invalid_port":   "Port range: 1-65535",
            "confirm_delete_macro": "Delete this macro?",
            "tunnel_local":         "Local:",
            "tunnel_ext":           "ext",
            "cpu_measuring":        "measuring...",
            "session_none":         "None",
            "msg_all_sessions_closed": "All terminal sessions closed.",
            "msg_close_tunnels_q":  "Close active tunnels ({{ports}}) as well?",
            "toast_tunnels_closed": "Tunnels closed.",
            "term_reconnect_failed":"[Connection lost — reconnect failed]",
            "term_reconnecting":    "[Reconnecting... ({{n}}/{{m}})]",
            "term_reconnect_ok":    "[Reconnected]",
            "btn_retry":            "Retry",
            "macro_edit_add":       "Add / Edit Macros",
        },
    },
}


def detect_lang() -> str:
    """OS 로케일 기반 언어 코드 감지. ko → 'ko', 그 외 → 'en'."""
    try:
        for env in ("LANG", "LANGUAGE", "LC_ALL", "LC_MESSAGES"):
            val = os.environ.get(env, "")
            if val.startswith("ko"):
                return "ko"
        lc = locale.getdefaultlocale()[0] or ""
        if lc.startswith("ko"):
            return "ko"
    except Exception:
        pass
    return "en"


def resolve_lang(configured: str) -> str:
    """설정값 → 지원 언어 확정. 빈 문자열이면 OS 감지."""
    if configured in SUPPORTED_LANGS:
        return configured
    return detect_lang()


def get_strings(lang: str) -> dict:
    """언어별 번역 딕셔너리 반환 (js 키 포함). 미지원 언어 → _DEFAULT_LANG."""
    return _STRINGS.get(lang, _STRINGS[_DEFAULT_LANG])


def get_js_const(lang: str) -> str:
    """JS const I18N = {...} 에 주입할 JSON 문자열 반환."""
    return json.dumps(get_strings(lang).get("js", {}), ensure_ascii=False)


def apply_html(html: str, strings: dict) -> str:
    """HTML 내 [[key]] 마커를 번역값으로 치환."""
    for key, val in strings.items():
        if isinstance(val, str):
            html = html.replace(f"[[{key}]]", val)
    return html
