#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Claude-Gram v2 Python Installer
# Fork: claude-gram by tg: @justidev, orig by @ripcats
#
# Recommended install via Claude Code marketplace (no symlinks needed):
#   /plugin marketplace add justidev-heroku/claude-gram-v2
#   /plugin install claude-gram@justidev-marketplace
#   /claude-gram:init
#
# This script is the legacy manual install path.

import os
import sys
import re
import shutil
import subprocess
from pathlib import Path
import time
import threading

# Включение ANSI цветов на Windows
if sys.platform == "win32":
    import ctypes
    kernel32 = ctypes.windll.kernel32
    kernel32.SetConsoleMode(kernel32.GetStdHandle(-11), 7)

# Цветовая палитра
CLR_RESET = "\x1b[0m"
CLR_GREEN = "\x1b[38;2;0;255;128m"
CLR_YELLOW = "\x1b[38;2;255;220;0m"
CLR_RED = "\x1b[38;2;255;64;64m"
CLR_CYAN = "\x1b[38;2;0;220;255m"

# Генератор трехцветного градиента Claude (оранжевый-белый-красный)
def gradient_text(text, start_rgb=(235, 94, 40), mid_rgb=(255, 255, 255), end_rgb=(185, 28, 28)):
    lines = text.splitlines()
    gradient_lines = []
    for line in lines:
        n = len(line)
        if n == 0:
            gradient_lines.append("")
            continue
        line_colored = []
        for i, char in enumerate(line):
            if i < n / 2:
                factor = i / (n / 2)
                r = int(start_rgb[0] + (mid_rgb[0] - start_rgb[0]) * factor)
                g = int(start_rgb[1] + (mid_rgb[1] - start_rgb[1]) * factor)
                b = int(start_rgb[2] + (mid_rgb[2] - start_rgb[2]) * factor)
            else:
                factor = (i - n / 2) / (n / 2)
                r = int(mid_rgb[0] + (end_rgb[0] - mid_rgb[0]) * factor)
                g = int(mid_rgb[1] + (end_rgb[1] - mid_rgb[1]) * factor)
                b = int(mid_rgb[2] + (end_rgb[2] - mid_rgb[2]) * factor)
            line_colored.append(f"\x1b[38;2;{r};{g};{b}m{char}")
        line_colored.append(CLR_RESET)
        gradient_lines.append("".join(line_colored))
    return "\n".join(gradient_lines)

ASCII_ART = r"""
 _____ _                 _          ____                     
/  ___| |               | |        / ___|                    
| |   | | __ _ _   _  __| | ___   | |  _ _ __ __ _ _ __ ___  
| |   | |/ _` | | | |/ _` |/ _ \  | | |_| '__/ _` | '_ ` _ \ 
| |___| | (_| | |_| | (_| |  __/  | |__| | | | (_| | | | | | |
\_____|_|\__,_|\__,_|\__,_|\___|   \____|_|_|  \__,_|_| |_| |_| v2
"""

def print_banner():
    os.system('cls' if sys.platform == 'win32' else 'clear')
    cols = shutil.get_terminal_size().columns
    if cols >= 65:
        print(gradient_text(ASCII_ART))
        width = 65
    else:
        print(gradient_text("  === Claude-Gram v2 ==="))
        width = 40

    print(gradient_text("  " + "=" * (width - 4)))
    print(f"  {CLR_GREEN}Claude-Gram v2 – Автоматический инсталлятор бота и MCP канала{CLR_RESET}")
    print(f"  {CLR_CYAN}Разработчик (Оригинал): @ripcats | Автор форка: tg @justidev{CLR_RESET}")
    print(gradient_text("  " + "=" * (width - 4)))
    print()

def print_claudgramik():
    """Статичный фоллбэк маскота (без анимации)."""
    esc = "\x1b"
    c_orange = f"{esc}[38;2;235;94;40m"
    c_reset = CLR_RESET
    c_cyan = CLR_CYAN
    
    print(f"  {c_orange}  ████████████  {c_reset}    Привет! Я Клодграмик.")
    print(f"  {c_orange}  ██  ████  ██  {c_reset}    – Начнём настройку {c_cyan}Claude-Gram v2{c_reset}?")
    print(f"  {c_orange}████████████████{c_reset}")
    print(f"  {c_orange}  ████████████  {c_reset}")
    print(f"  {c_orange}  ██ ██  ██ ██  {c_reset}")
    print()


# ---------------------------------------------------------------------------
# Анимированный маскот «Клодграмик» — покачивание + моргание
# ---------------------------------------------------------------------------

_MASCOT_EYES_OPEN = [
    "  ████████████  ",
    "  ██  ████  ██  ",
    "████████████████",
    "  ████████████  ",
    "  ██ ██  ██ ██  ",
]

_MASCOT_EYES_CLOSED = [
    "  ████████████  ",
    "  ██████████████",
    "████████████████",
    "  ████████████  ",
    "  ██ ██  ██ ██  ",
]

_MASCOT_LEGS_UP = [
    "  ████████████  ",
    "  ██  ████  ██  ",
    "████████████████",
    "  ████████████  ",
    "    ████████    ",
]

# (кадр, вертикальный_сдвиг)
_ANIM_SEQUENCE = [
    (_MASCOT_EYES_OPEN,   0),   # стоит
    (_MASCOT_EYES_OPEN,   0),   # стоит (пауза)
    (_MASCOT_EYES_OPEN,  -1),   # подпрыгивает
    (_MASCOT_LEGS_UP,    -1),   # в воздухе, ноги поджаты
    (_MASCOT_EYES_OPEN,   0),   # приземление
    (_MASCOT_EYES_OPEN,   0),   # стоит
    (_MASCOT_EYES_CLOSED, 0),   # моргание
    (_MASCOT_EYES_CLOSED, 0),   # моргание (задержка)
    (_MASCOT_EYES_OPEN,   0),   # глаза открыты
]

_anim_stop = threading.Event()
_anim_thread = None
# Количество строк, занятых анимацией (для очистки)
_ANIM_HEIGHT = 9  # 5 строк маскота + 2 отступа + 2 текста


def _render_mascot_frame(frame_lines, y_offset, cols):
    """Рендерит один кадр маскота по центру с вертикальным сдвигом."""
    esc = "\x1b"
    c_orange = f"{esc}[38;2;235;94;40m"
    c_dark   = f"{esc}[38;2;180;60;20m"
    c_reset  = CLR_RESET
    c_cyan   = CLR_CYAN

    mascot_width = 16  # ширина самой широкой строки маскота
    text_right_1 = f"Привет! Я Клодграмик."
    text_right_2 = f"– Начнём настройку {c_cyan}Claude-Gram v2{c_reset}?"

    pad = max(0, (cols - mascot_width - 40) // 2)
    prefix = " " * pad

    output_lines = []
    # Верхний отступ (сдвиг)
    blank_top = 1 + y_offset
    blank_bottom = 1 - y_offset
    for _ in range(max(0, blank_top)):
        output_lines.append("")

    for i, line in enumerate(frame_lines):
        # Градиент: верхние строки светлее, нижние — темнее
        if i < 2:
            color = c_orange
        elif i == 2:
            color = c_orange
        else:
            color = c_dark

        right_text = ""
        if i == 0:
            right_text = f"    {c_reset}{text_right_1}"
        elif i == 1:
            right_text = f"    {c_reset}{text_right_2}"

        output_lines.append(f"{prefix}  {color}{line}{c_reset}{right_text}")

    for _ in range(max(0, blank_bottom)):
        output_lines.append("")

    return output_lines


def _animation_loop():
    """Фоновый цикл анимации маскота."""
    cols = shutil.get_terminal_size().columns
    esc = "\x1b"
    hide_cursor = f"{esc}[?25l"
    show_cursor = f"{esc}[?25h"

    sys.stdout.write(hide_cursor)
    sys.stdout.flush()

    # Запоминаем текущую позицию курсора — будем рисовать ниже баннера
    # Печатаем пустые строки для резерва места
    for _ in range(_ANIM_HEIGHT):
        sys.stdout.write("\n")
    # Поднимаемся обратно вверх
    sys.stdout.write(f"{esc}[{_ANIM_HEIGHT}A")
    sys.stdout.flush()

    frame_idx = 0
    while not _anim_stop.is_set():
        frame_lines, y_offset = _ANIM_SEQUENCE[frame_idx % len(_ANIM_SEQUENCE)]
        rendered = _render_mascot_frame(frame_lines, y_offset, cols)

        # Сохраняем позицию курсора
        sys.stdout.write(f"{esc}7")
        # Рисуем кадр
        for i, line in enumerate(rendered):
            # Очистка строки + запись
            sys.stdout.write(f"{esc}[{i + 1}B")  # вниз на i
        # Возвращаемся к сохраненной позиции и рисуем
        sys.stdout.write(f"{esc}8")

        # Проще: просто перерисовываем блок целиком
        sys.stdout.write(f"{esc}7")  # save
        for line in rendered:
            # Перейти на начало строки, очистить, напечатать
            sys.stdout.write(f"\r{esc}[2K{line}\n")
        # Вернуться назад
        sys.stdout.write(f"{esc}8")
        sys.stdout.flush()

        frame_idx += 1
        _anim_stop.wait(0.2)

    sys.stdout.write(show_cursor)
    sys.stdout.flush()


def start_mascot_animation():
    """Запускает анимацию маскота в фоновом потоке."""
    global _anim_thread
    if not sys.stdout.isatty():
        # Не в терминале — показываем статичного маскота
        print_claudgramik()
        return
    _anim_stop.clear()
    _anim_thread = threading.Thread(target=_animation_loop, daemon=True)
    _anim_thread.start()


def stop_mascot_animation():
    """Останавливает анимацию и очищает область маскота."""
    global _anim_thread
    if _anim_thread is None:
        return
    _anim_stop.set()
    _anim_thread.join(timeout=1)
    _anim_thread = None
    esc = "\x1b"
    # Очищаем область анимации
    for _ in range(_ANIM_HEIGHT):
        sys.stdout.write(f"\r{esc}[2K\n")
    # Возвращаемся наверх
    sys.stdout.write(f"{esc}[{_ANIM_HEIGHT}A")
    sys.stdout.flush()


def resolve_service_user(home_dir) -> str:
    """Владелец HOME — именно он должен быть User= в юните, иначе сервис
    не прочитает свой же HOME. Логин из tty тут не годится: он падает без
    управляющего терминала и под sudo возвращает вызывающего, а не владельца."""
    try:
        import pwd
        return pwd.getpwuid(os.stat(str(home_dir)).st_uid).pw_name
    except Exception:
        return "root"


def ensure_login_user() -> None:
    """Идемпотентно создаёт системного пользователя claude-login.
    Под ним выполняется изолированный OAuth-флоу команды /login в боте."""
    if not sys.platform.startswith("linux"):
        return
    import pwd
    try:
        pwd.getpwnam("claude-login")
        print(f"{CLR_GREEN}✅ Системный пользователь claude-login уже существует.{CLR_RESET}")
        return
    except KeyError:
        pass
    try:
        subprocess.run(["sudo", "useradd", "-m", "-s", "/bin/bash", "claude-login"], check=True)
        print(f"{CLR_GREEN}✅ Системный пользователь claude-login создан.{CLR_RESET}")
    except Exception as e:
        print(
            f"{CLR_YELLOW}⚠️ Не удалось создать пользователя claude-login: {e}\n"
            f"   Команда /login не будет работать, пока пользователь не создан вручную:\n"
            f"   sudo useradd -m -s /bin/bash claude-login{CLR_RESET}"
        )


# Launcher template written to /usr/local/bin/claude-telegram-bot (and its Windows copy).
# Placeholders ##HOME## / ##INSTALL_DIR## are substituted by render_wrapper().
WRAPPER_TEMPLATE = r"""#!/usr/bin/env python3
import json
import os
import re
import select
import shutil
import sys
import time
import urllib.request
import signal
from pathlib import Path

HOME_DIR = Path("##HOME##")
CLAUDE_JSON_PATH = HOME_DIR / ".claude.json"
# Owner state dir: holds .env (bot token), the owner's access.json and the guests/ tree.
OWNER_STATE_DIR = Path("##HOME##/.claude/channels/telegram")
# Per-instance state dir. Owner: OWNER_STATE_DIR. Guest: OWNER_STATE_DIR/guests/<uid>.
# Rebound in main() for --guest mode (see _use_state_dir).
STATE_DIR = OWNER_STATE_DIR
ACTIVE_CLI_FILE = OWNER_STATE_DIR / "active_cli"
LOG_FILE_PATH = STATE_DIR / "bot.log"
LAST_MODEL_FILE = STATE_DIR / "last_launched_model"
SESSION_LIMIT_FILE = STATE_DIR / "last_session_limit"

GUEST_PREAMBLE = (
    "You are a guest instance of this Telegram bot: you work for Telegram user {uid}, "
    "who is the decision-maker for this session (the bot owner has pre-authorized them). "
    "Your working directory is {work_dir}. "
    "Do not modify or stop the Telegram bridge itself (~/.claude/channels/telegram, "
    "the claude-gram plugin, claude-telegram*.service units)."
)

def parse_args(argv):
    # `claude-telegram-bot` -> None (owner). `claude-telegram-bot --guest <uid>` -> "<uid>".
    if not argv:
        return None
    if len(argv) == 2 and argv[0] == "--guest" and re.fullmatch(r"[0-9]+", argv[1]):
        return argv[1]
    sys.stderr.write("usage: claude-telegram-bot [--guest <numeric uid>]\n")
    raise SystemExit(2)

def guest_paths(home, uid, guests_dir_env=None):
    # Returns (state_dir, work_dir) for a guest instance. Pure.
    if not re.fullmatch(r"[0-9]+", str(uid)):
        raise ValueError(f"invalid guest uid: {uid!r}")
    home = Path(home)
    state_dir = home / ".claude" / "channels" / "telegram" / "guests" / str(uid)
    base = Path(guests_dir_env) if guests_dir_env else home / "guests"
    return state_dir, base / str(uid)

def _use_state_dir(state_dir) -> None:
    global STATE_DIR, LOG_FILE_PATH, LAST_MODEL_FILE, SESSION_LIMIT_FILE
    STATE_DIR = Path(state_dir)
    LOG_FILE_PATH = STATE_DIR / "bot.log"
    LAST_MODEL_FILE = STATE_DIR / "last_launched_model"
    SESSION_LIMIT_FILE = STATE_DIR / "last_session_limit"

def read_guest_bot_token(state_dir) -> str:
    # Guest's own bot (Managed Bots) from <state_dir>/bot.env. "" if absent or malformed.
    try:
        path = Path(state_dir) / "bot.env"
        if path.exists():
            for line in path.read_text("utf-8").splitlines():
                if line.startswith("TELEGRAM_BOT_TOKEN="):
                    token = line.split("=", 1)[1].strip()
                    return token if re.fullmatch(r"[0-9]+:[A-Za-z0-9_-]{20,}", token) else ""
    except Exception:
        pass
    return ""

def read_bot_token() -> str:
    # Owner's token from OWNER_STATE_DIR/.env. Used for guests only when they have no own bot.
    env_path = OWNER_STATE_DIR / ".env"
    try:
        if env_path.exists():
            for line in env_path.read_text("utf-8").splitlines():
                if line.startswith("TELEGRAM_BOT_TOKEN="):
                    return line.split("=", 1)[1].strip()
    except Exception:
        pass
    return ""

def read_model(settings_path) -> str:
    try:
        if Path(settings_path).exists():
            model = json.loads(Path(settings_path).read_text("utf-8")).get("model")
            if isinstance(model, str):
                return model
    except Exception:
        pass
    return ""

def read_guest_instructions() -> str:
    try:
        path = OWNER_STATE_DIR / "guests" / "instructions.md"
        if path.exists():
            return path.read_text("utf-8")
    except Exception:
        pass
    return ""

def build_guest_system_prompt(uid, work_dir, instructions_text) -> str:
    parts = [GUEST_PREAMBLE.format(uid=uid, work_dir=work_dir)]
    if instructions_text and instructions_text.strip():
        parts.append(instructions_text.strip())
    return "\n\n".join(parts)

def build_guest_launch(uid, state_dir, work_dir, model_val, session_args, instructions_text, token, base_env, own_bot=False):
    # Pure: returns (cmd, env) for a guest claude process.
    state_dir = Path(state_dir)
    settings_file = state_dir / "settings.json"
    env = dict(base_env)
    env.pop("CLAUDE_CODE_CHILD_SESSION", None)
    env.pop("CLAUDE_CODE_ENTRYPOINT", None)
    env["CLAUDE_TELEGRAM_BACKGROUND"] = "1"
    env["CLAUDE_GRAM_GUEST_UID"] = str(uid)
    env["TELEGRAM_STATE_DIR"] = str(state_dir)
    env["CLAUDE_GRAM_SETTINGS_FILE"] = str(settings_file)
    if token:
        env["TELEGRAM_BOT_TOKEN"] = token
    if own_bot:
        env["CLAUDE_GRAM_GUEST_OWN_BOT"] = "1"
    else:
        env.pop("CLAUDE_GRAM_GUEST_OWN_BOT", None)
    cmd = ["claude", "--channels", "plugin:claude-gram@justidev-marketplace",
           "--settings", str(settings_file)]
    if model_val:
        cmd.extend(["--model", model_val])
    cmd.extend(session_args)
    cmd.extend(["--append-system-prompt",
                build_guest_system_prompt(uid, work_dir, instructions_text)])
    return cmd, env

def ensure_trusted(claude_json_path, project_dir) -> bool:
    # Mark project_dir as trusted (and skip first-run onboarding/theme) in ~/.claude.json.
    # Only rewrites when something changes; never creates the file. Never raises.
    # Returns True when the file was changed.
    try:
        path = Path(claude_json_path)
        if not path.exists():
            return False
        data = json.loads(path.read_text("utf-8"))
        if not isinstance(data, dict):
            return False
        changed = False
        if "projects" not in data:
            data["projects"] = {}
            changed = True
        projects = data["projects"]
        if not isinstance(projects, dict):
            return False
        key = str(project_dir)
        proj = projects.get(key)
        if not isinstance(proj, dict):
            proj = {}
            projects[key] = proj
            changed = True
        if proj.get("hasTrustDialogAccepted") is not True:
            proj["hasTrustDialogAccepted"] = True
            changed = True
        if not data.get("hasCompletedOnboarding"):
            data["hasCompletedOnboarding"] = True
            changed = True
        if not data.get("theme"):
            data["theme"] = "dark"
            changed = True
        if not changed:
            return False
        tmp = path.with_name(path.name + f".tmp-{os.getpid()}")
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), "utf-8")
        try:
            shutil.copymode(path, tmp)
        except Exception:
            pass
        os.replace(tmp, path)
        return True
    except Exception as e:
        try:
            sys.stderr.write(f"ensure_trusted failed for {claude_json_path}: {e}\n")
        except Exception:
            pass
        return False

def resolve_claude_bin() -> str:
    # PATH в systemd-юните урезан, поэтому кроме which проверяем типовые пути.
    found = shutil.which("claude")
    if found:
        return found
    for cand in ("/usr/local/bin/claude", "/usr/bin/claude",
                 os.path.expanduser("~/.local/bin/claude"), "##HOME##/.local/bin/claude"):
        if os.path.exists(cand):
            return cand
    return "claude"

def get_active_cli() -> str:
    if ACTIVE_CLI_FILE.exists():
        return ACTIVE_CLI_FILE.read_text("utf-8").strip()
    return "claude"

def send_telegram_alert(text: str, reply_markup: dict = None) -> None:
    try:
        access_path = STATE_DIR / "access.json"
        if not access_path.exists():
            return
        access = json.loads(access_path.read_text("utf-8"))
        if not access.get("allowFrom"):
            return
        chat_id = access["allowFrom"][0]

        thread_path = STATE_DIR / "session_thread_id"
        thread_id = None
        if thread_path.exists():
            val = thread_path.read_text("utf-8").strip()
            if val and val != "None":
                thread_id = int(val)

        token = read_bot_token()
        if not token:
            return

        payload = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML"
        }
        if thread_id is not None:
            payload["message_thread_id"] = thread_id
        if reply_markup:
            payload["reply_markup"] = reply_markup

        req = urllib.request.Request(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=5) as response:
            response.read()
    except Exception as e:
        sys.stderr.write(f"Failed to send Telegram alert: {e}\n")

def get_active_session_id() -> str:
    active_sess_file = STATE_DIR / "active_session_id"
    if active_sess_file.exists():
        try:
            val = active_sess_file.read_text("utf-8").strip()
            if val and val != "new":
                return val
        except Exception:
            pass
    return ""

def should_fork_session(model_val: str, last_model: str) -> bool:
    # claude --resume не применяет --model к уже идущей сессии — без форка
    # смена модели через /model молча не сработает.
    if not model_val:
        return False
    if not last_model:
        return False
    return model_val != last_model

def build_session_args(active_id: str, model_val: str, last_model: str, new_uuid: str):
    # Decide session-related CLI args. Pure, for testability.
    # Returns (args_list, persist_id, fork_for_model_change):
    #   - no active session -> start fresh with --session-id, persist the new uuid
    #   - active session, model unchanged -> --resume, keep file as-is
    #   - active session, model changed -> --resume --fork-session (claude mints a
    #     new id which the resume-hint capture will persist), keep file as-is
    if not active_id:
        return (["--session-id", new_uuid], new_uuid, False)
    if should_fork_session(model_val, last_model):
        return (["--resume", active_id, "--fork-session"], "", True)
    return (["--resume", active_id], "", False)

def classify_pty_alert(pty_buffer: str):
    # Classify an error surfaced in Claude's PTY output. Pure, for testability.
    # Returns (matched_alert, kill_after_alert, is_auth, no_conversation, session_limit_reset):
    #   - matched_alert: HTML alert text, or None when nothing matched.
    #   - kill_after_alert: True only for TERMINAL conditions (billing/credit/
    #     weekly/5-hour/auth). Transient errors (rate limit, overloaded) and the
    #     session limit return False so the session is left alive.
    #   - is_auth: stale/invalid credentials — caller adds a restart backoff.
    #   - no_conversation: "no conversation found to continue" — caller resets
    #     the session id to "new" and restarts fresh.
    #   - session_limit_reset: reset-time string (may be "") when a LIVE session
    #     limit banner is recognised; None otherwise. Signals the caller to send
    #     the two-button (ack / resume) alert instead of killing the session.
    lower_buf = pty_buffer.lower()

    def is_fresh(kws: list) -> bool:
        max_idx = -1
        for kw in kws:
            idx = lower_buf.rfind(kw)
            if idx > max_idx:
                max_idx = idx
        if max_idx == -1:
            return False
        suffix = pty_buffer[max_idx:]
        return "❯" not in suffix

    matched_alert = None
    kill_after_alert = False
    is_auth = False
    no_conversation = False
    session_limit_reset = None

    if ("ratelimiterror" in lower_buf or "rate limit reached" in lower_buf or "rate limit exceeded" in lower_buf) and is_fresh(["ratelimiterror", "rate limit reached", "rate limit exceeded"]):
        matched_alert = "⚠️ <b>Claude Code: Превышен лимит запросов (Rate Limit).</b> Пожалуйста, подождите."
    elif ("overloadederror" in lower_buf or ("overloaded" in lower_buf and "error" in lower_buf)) and is_fresh(["overloadederror", "overloaded"]):
        matched_alert = "⚠️ <b>Claude Code: Серверы Anthropic перегружены (Overloaded).</b> Пожалуйста, повторите попытку."
    elif "billing limit" in lower_buf and is_fresh(["billing limit"]):
        matched_alert = "⚠️ <b>Claude Code: Достигнут лимит оплаты (Billing Limit).</b>"
        kill_after_alert = True
    elif ("credit balance too low" in lower_buf or "insufficient credit" in lower_buf or "insufficient funds" in lower_buf) and is_fresh(["credit balance too low", "insufficient credit", "insufficient funds"]):
        matched_alert = "⚠️ <b>Claude Code: Недостаточно средств на балансе API.</b>"
        kill_after_alert = True
    elif (("reached your weekly limit" in lower_buf or "weekly limit reached" in lower_buf or "weekly budget exceeded" in lower_buf or
          (("weekly limit" in lower_buf or "weekly budget" in lower_buf) and
           ("what do you want to do" in lower_buf or "upgrade your plan" in lower_buf or "stop and wait" in lower_buf))) and not (
          ("plan's weekly usage limit" in lower_buf or "weekly usage limit on" in lower_buf) and not ("what do you want to do" in lower_buf or "upgrade your plan" in lower_buf))) and is_fresh(["reached your weekly limit", "weekly limit reached", "weekly budget exceeded", "weekly limit", "weekly budget"]):
        matched_alert = "⚠️ <b>Claude Code: Достигнут недельный лимит использования.</b>"
        kill_after_alert = True
    elif (("reached your 5-hour limit" in lower_buf or "5-hour limit reached" in lower_buf or "5-hour budget exceeded" in lower_buf or
          (("5-hour limit" in lower_buf or "5-hour budget" in lower_buf or "5-hour window" in lower_buf) and
           ("what do you want to do" in lower_buf or "upgrade your plan" in lower_buf or "stop and wait" in lower_buf))) and not (
          ("plan's 5-hour usage limit" in lower_buf or "5-hour usage limit on" in lower_buf) and not ("what do you want to do" in lower_buf or "upgrade your plan" in lower_buf))) and is_fresh(["reached your 5-hour limit", "5-hour limit reached", "5-hour budget exceeded", "5-hour limit", "5-hour budget", "5-hour window"]):
        matched_alert = "⚠️ <b>Claude Code: Достигнут 5-часовой лимит использования. Пожалуйста, подождите сброса лимита.</b>"
        kill_after_alert = True
    elif ("hit your session limit" in lower_buf and "resets" in lower_buf and
          ("/upgrade" in lower_buf or "usage-credits" in lower_buf or "/usage" in lower_buf) and
          is_fresh(["hit your session limit", "session limit"])):
        # LIVE session-limit banner only. The echoed agent error
        # ("...API error: You've hit your session limit") lacks resets/upgrade
        # and must NOT trigger. Do not kill — wait for reset + resume button.
        reset_str = ""
        m = re.search(r"resets\s+(\d{1,2}:\d{2}\s*[ap]m(?:\s*\([^)]*\))?)", pty_buffer, re.IGNORECASE)
        if m:
            reset_str = m.group(1).strip()
        session_limit_reset = reset_str
        matched_alert = ("⏳ <b>Claude Code: Достигнут лимит сессии.</b>" +
                         (f" Сброс: <b>{reset_str}</b>." if reset_str else "") +
                         " Дождитесь сброса и нажмите «Продолжить работу».")
        kill_after_alert = False
    elif ("invalid authentication credentials" in lower_buf or "api error: 401" in lower_buf or "please run /login" in lower_buf) and is_fresh(["invalid authentication credentials", "api error: 401", "please run /login"]):
        matched_alert = "⚠️ <b>Сессия устарела или недействительна.</b> Пожалуйста, выполните повторную авторизацию с помощью команды /login."
        is_auth = True
        kill_after_alert = True
    elif "no conversation found to continue" in lower_buf or "no conversation found with session id" in lower_buf:
        no_conversation = True

    return matched_alert, kill_after_alert, is_auth, no_conversation, session_limit_reset

def delete_thinking_message() -> None:
    try:
        thinking_path = STATE_DIR / "thinking_msg_id"
        if not thinking_path.exists():
            return
        msg_id = thinking_path.read_text("utf-8").strip()
        if not msg_id:
            return

        access_path = STATE_DIR / "access.json"
        if not access_path.exists():
            return
        access = json.loads(access_path.read_text("utf-8"))
        if not access.get("allowFrom"):
            return
        chat_id = access["allowFrom"][0]

        token = read_bot_token()
        if not token:
            return

        payload = {
            "chat_id": chat_id,
            "message_id": int(msg_id)
        }
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{token}/deleteMessage",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=5) as response:
            response.read()
        thinking_path.unlink(missing_ok=True)
    except Exception as e:
        sys.stderr.write(f"Failed to delete thinking message: {e}\n")

def clean_mcp_auth_cache() -> None:
    # Remove plugin:claude-gram:telegram from MCP auth cache to prevent false auth blocks.
    try:
        cache_path = Path("##HOME##/.claude/mcp-needs-auth-cache.json")
        if not cache_path.exists():
            return
        data = json.loads(cache_path.read_text("utf-8"))
        if "plugin:claude-gram:telegram" in data:
            del data["plugin:claude-gram:telegram"]
            cache_path.write_text(json.dumps(data), "utf-8")
    except Exception:
        pass


# Interactive startup dialogs that can block a fresh claude process on the PTY.
# needle: lowercase, whitespace-free text as it appears after ANSI cleanup.
# keys: byte chunks written to the PTY in order, STARTUP_DIALOG_KEY_DELAY apart.
STARTUP_DIALOGS = [
    # "Make auto mode your default permission mode?" -> "No, keep bypass permissions"
    ("makeautomodeyourdefaultpermissionmode?", [b"\x1b[B", b"\r"]),
]
STARTUP_DIALOG_COOLDOWN = 5.0
STARTUP_DIALOG_MAX_ANSWERS = 5
STARTUP_DIALOG_KEY_DELAY = 0.3


def _match_startup_dialog(buf: str):
    norm = re.sub(r"\s+", "", buf).lower()
    for needle, keys in STARTUP_DIALOGS:
        if needle in norm:
            return needle, keys
    return None


def detect_startup_dialog(buf: str):
    # Key chunks answering a known startup dialog found in buf, else None.
    hit = _match_startup_dialog(buf)
    return list(hit[1]) if hit else None


def main() -> int:
    guest_uid = parse_args(sys.argv[1:])
    os.environ["CLAUDE_TELEGRAM_BACKGROUND"] = "1"
    clean_mcp_auth_cache()
    cli = get_active_cli()
    has_creds = Path("##HOME##/.claude/.credentials.json").exists()

    if guest_uid is not None:
        # Guest instance: never fall back to standalone server.py (that would be a second poller).
        if sys.platform == "win32":
            sys.stderr.write("Guest instances are not supported on Windows.\n")
            return 1
        if cli == "agy" or not has_creds:
            sys.stderr.write("Guest instance: no active Claude credentials. Exiting.\n")
            return 1
        state_dir, work_dir = guest_paths(str(HOME_DIR), guest_uid, os.environ.get("CLAUDE_GRAM_GUESTS_DIR"))
        own_token = read_guest_bot_token(state_dir)
        token = own_token or read_bot_token()
        own_bot = bool(own_token)
        if not token:
            sys.stderr.write("Guest instance: TELEGRAM_BOT_TOKEN missing in owner .env. Exiting.\n")
            return 1
        _use_state_dir(state_dir)
        try:
            STATE_DIR.mkdir(parents=True, exist_ok=True)
            work_dir.mkdir(parents=True, exist_ok=True)
            settings_file = STATE_DIR / "settings.json"
            if not settings_file.exists():
                settings_file.write_text("{}\n", encoding="utf-8")
        except Exception as e:
            sys.stderr.write(f"Guest instance: cannot prepare state dir: {e}\n")
            return 1
        ensure_trusted(CLAUDE_JSON_PATH, work_dir)
        os.chdir(work_dir)
        model_val = read_model(STATE_DIR / "settings.json")
    else:
        token = ""
        work_dir = None
        if cli == "agy" or not has_creds:
            sys.stderr.write("No active credentials found. Running server.py standalone...\n")
            cmd = ["python3" if sys.platform != "win32" else sys.executable, "##INSTALL_DIR##/server.py"]
            os.execvp(cmd[0], cmd)
            return 0
        ensure_trusted(CLAUDE_JSON_PATH, HOME_DIR)
        model_val = read_model(HOME_DIR / ".claude" / "settings.json")

    cmd = ["claude", "--channels", "plugin:claude-gram@justidev-marketplace"]
    if guest_uid is None and model_val:
        cmd.extend(["--model", model_val])

    # Resume the previous session when one exists; start fresh only when it does
    # not (first run, or after a "no conversation found" recovery reset).
    import uuid
    active_id = get_active_session_id()
    last_model = ""
    try:
        if LAST_MODEL_FILE.exists():
            last_model = LAST_MODEL_FILE.read_text("utf-8").strip()
    except Exception:
        pass
    session_args, persist_id, fork_for_model_change = build_session_args(
        active_id, model_val or "", last_model, str(uuid.uuid4())
    )
    if persist_id:
        try:
            active_sess_file = STATE_DIR / "active_session_id"
            active_sess_file.parent.mkdir(parents=True, exist_ok=True)
            active_sess_file.write_text(persist_id, "utf-8")
        except Exception:
            pass
    if guest_uid is not None:
        cmd, env = build_guest_launch(
            guest_uid, STATE_DIR, work_dir, model_val, session_args,
            read_guest_instructions(), token, dict(os.environ), own_bot=own_bot)
        os.environ.clear()
        os.environ.update(env)
    else:
        cmd.extend(session_args)

    if guest_uid is None and sys.platform == "win32":
        # Windows PTY is not supported, run server.py standalone as fallback in wrapper
        sys.stderr.write("Running Claude-Gram server.py standalone on Windows...\n")
        cmd = [sys.executable, "##INSTALL_DIR##/server.py"]
        os.execvp(sys.executable, cmd)
        return 0

    claude_bin = resolve_claude_bin()
    pid, fd = os.forkpty()
    if pid == 0:
        os.execvp(claude_bin, cmd)
        os._exit(127)

    def sig_handler(signum, frame):
        try:
            os.kill(pid, signal.SIGTERM)
            time.sleep(0.5)
            os.kill(pid, signal.SIGKILL)
        except Exception:
            pass
        sys.exit(143 if signum == signal.SIGTERM else 130)

    signal.signal(signal.SIGTERM, sig_handler)
    signal.signal(signal.SIGINT, sig_handler)

    ansi_escape = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')
    cursor_move = re.compile(r'\x1B\[\d*(?:;\d*)*[A-GfHiIJKSTst]')
    resume_hint_re = re.compile(
        r'claude --resume ([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})'
    )
    pty_buffer = ""
    startup_dialog_counts = {}
    startup_dialog_last = {}
    last_alert_time = 0.0
    auth_failed = False
    process_start_time = time.time()
    startup_cleared = False
    forked_session_captured = not fork_for_model_change

    log_file = None
    try:
        LOG_FILE_PATH.parent.mkdir(parents=True, exist_ok=True)
        log_file = open(LOG_FILE_PATH, "a", encoding="utf-8")
    except Exception:
        pass

    while True:
        try:
            r, _, _ = select.select([fd], [], [], 1.0)
        except (OSError, ValueError):
            break
        if fd in r:
            try:
                data = os.read(fd, 65536)
            except OSError:
                break
            if not data:
                break

            decoded = data.decode("utf-8", errors="ignore")
            sys.stdout.write(decoded)
            sys.stdout.flush()
            if log_file:
                try:
                    log_file.write(decoded)
                    log_file.flush()
                except Exception:
                    pass
            clean_data = cursor_move.sub(" ", decoded)
            clean_data = ansi_escape.sub("", clean_data)
            clean_data = re.sub(r'[ \t]+', ' ', clean_data)
            pty_buffer += clean_data
            if len(pty_buffer) > 10000:
                pty_buffer = pty_buffer[-10000:]

            hit = _match_startup_dialog(pty_buffer)
            if hit:
                dialog_needle, dialog_keys = hit
                dlg_now = time.time()
                if (startup_dialog_counts.get(dialog_needle, 0) < STARTUP_DIALOG_MAX_ANSWERS
                        and dlg_now - startup_dialog_last.get(dialog_needle, 0.0) > STARTUP_DIALOG_COOLDOWN):
                    try:
                        for chunk in dialog_keys:
                            os.write(fd, chunk)
                            time.sleep(STARTUP_DIALOG_KEY_DELAY)
                    except OSError:
                        pass
                    startup_dialog_counts[dialog_needle] = startup_dialog_counts.get(dialog_needle, 0) + 1
                    startup_dialog_last[dialog_needle] = dlg_now
                    sys.stderr.write(f"auto-answered startup dialog: {dialog_needle}\n")
                    sys.stderr.flush()
                    pty_buffer = ""

            if not forked_session_captured:
                m = resume_hint_re.search(pty_buffer)
                if m:
                    new_sess_id = m.group(1)
                    try:
                        active_sess_file = STATE_DIR / "active_session_id"
                        active_sess_file.parent.mkdir(parents=True, exist_ok=True)
                        active_sess_file.write_text(new_sess_id, "utf-8")
                    except Exception:
                        pass
                    forked_session_captured = True

            now = time.time()
            if not startup_cleared and now - process_start_time > 8.0:
                pty_buffer = ""
                startup_cleared = True
                try:
                    if model_val:
                        LAST_MODEL_FILE.parent.mkdir(parents=True, exist_ok=True)
                        LAST_MODEL_FILE.write_text(model_val, "utf-8")
                except Exception:
                    pass

            # A missing/dead session id is fatal within the first seconds, long
            # before startup_cleared — check it on the raw buffer regardless.
            if "no conversation found" in pty_buffer.lower():
                try:
                    active_sess_file = STATE_DIR / "active_session_id"
                    active_sess_file.write_text("new", encoding="utf-8")
                except Exception:
                    pass
                try:
                    os.kill(pid, signal.SIGKILL)
                except Exception:
                    pass
                sys.exit(1)

            if startup_cleared and now - last_alert_time > 15.0:
                matched_alert, kill_after_alert, is_auth, no_conversation, session_limit_reset = classify_pty_alert(pty_buffer)

                if session_limit_reset is not None:
                    # Live session-limit banner: never kill. Dedupe on the reset
                    # signature so the same banner (repainted in the PTY) is not
                    # re-sent. Buttons let the owner ack or resume after reset.
                    signature = session_limit_reset or "unknown"
                    prev_signature = ""
                    try:
                        if SESSION_LIMIT_FILE.exists():
                            prev_signature = SESSION_LIMIT_FILE.read_text("utf-8").strip()
                    except Exception:
                        prev_signature = ""

                    if signature != prev_signature:
                        try:
                            SESSION_LIMIT_FILE.write_text(signature, encoding="utf-8")
                        except Exception:
                            pass
                        delete_thinking_message()
                        send_telegram_alert(matched_alert, reply_markup={"inline_keyboard": [[
                            {"text": "✅ Понял", "callback_data": "limit:ack"},
                            {"text": "▶️ Продолжить работу", "callback_data": "limit:resume"},
                        ]]})
                        last_alert_time = now

                    # Scrub keywords so the same buffer is not re-matched.
                    for keyword in ["hit your session limit", "session limit"]:
                        pty_buffer = re.sub(re.escape(keyword), f"[processed_{keyword}]", pty_buffer, flags=re.IGNORECASE)
                    # Skip the generic matched_alert branch below.
                    continue

                if no_conversation:
                    try:
                        active_sess_file = STATE_DIR / "active_session_id"
                        active_sess_file.write_text("new", encoding="utf-8")
                    except Exception:
                        pass
                    try:
                        os.kill(pid, signal.SIGKILL)
                    except Exception:
                        pass
                    sys.exit(1)

                if is_auth:
                    auth_failed = True

                if matched_alert:
                    delete_thinking_message()
                    send_telegram_alert(matched_alert)
                    last_alert_time = now
                    if kill_after_alert:
                        try:
                            os.kill(pid, signal.SIGKILL)
                        except Exception:
                            pass
                        # On stale/invalid auth, back off before systemd restarts
                        # us (RestartSec=5) so we don't hammer a dead credential.
                        if auth_failed:
                            time.sleep(30)
                        sys.exit(1)
                    for keyword in ["ratelimiterror", "rate limit reached", "rate limit exceeded",
                                    "overloadederror", "overloaded", "billing limit",
                                    "credit balance too low", "insufficient credit", "insufficient funds",
                                    "weekly limit", "weekly budget", "5-hour limit", "5-hour budget", "5-hour window",
                                    "hit your session limit", "session limit",
                                    "invalid authentication credentials", "api error: 401", "please run /login"]:
                        pty_buffer = re.sub(re.escape(keyword), f"[processed_{keyword}]", pty_buffer, flags=re.IGNORECASE)

    _, status = os.waitpid(pid, 0)
    if auth_failed:
        time.sleep(30)
    if os.WIFEXITED(status):
        return os.WEXITSTATUS(status)
    return 1

if __name__ == "__main__":
    sys.exit(main())
"""

def render_wrapper(home_dir, install_dir) -> str:
    code = WRAPPER_TEMPLATE.replace("##HOME##", str(home_dir).replace("\\", "\\\\"))
    code = code.replace("##INSTALL_DIR##", str(install_dir).replace("\\", "\\\\"))
    return code

def render_guest_unit(user, home, path_env, wrapper_path) -> str:
    # systemd template unit: one instance per guest, e.g. claude-telegram-guest@123.service
    return f"""[Unit]
Description=Claude Code Telegram guest instance %i
After=network-online.target claude-telegram.service
Wants=network-online.target

[Service]
Type=simple
User={user}
WorkingDirectory={home}
Environment=HOME={home}
Environment=CLAUDE_ALLOW_ROOT=1
Environment=PATH={path_env}
UnsetEnvironment=CLAUDE_CODE_CHILD_SESSION CLAUDE_CODE_ENTRYPOINT
ExecStart={wrapper_path} --guest %i
Restart=always
RestartSec=5
TimeoutStopSec=20
KillMode=control-group

[Install]
WantedBy=multi-user.target
"""

def main():
    print_banner()
    start_mascot_animation()
    time.sleep(2.5)  # маскот покачивается пару секунд
    stop_mascot_animation()
    print_claudgramik()  # статичный маскот остаётся на экране
    
    # 1. Интерактивный опрос / Автоопределение
    print(f"{CLR_CYAN}[1/5] Настройка Telegram конфигурации:{CLR_RESET}")
    
    HOME_DIR = Path.home()
    STATE_DIR = HOME_DIR / ".claude" / "channels" / "telegram"
    existing_token = None
    existing_owner = None
    
    # Пытаемся прочитать существующие настройки
    try:
        import json
        env_file = STATE_DIR / ".env"
        if env_file.exists():
            for line in env_file.read_text("utf-8").splitlines():
                if line.startswith("TELEGRAM_BOT_TOKEN="):
                    t = line.split("=", 1)[1].strip()
                    if re.match(r"^[0-9]+:[a-zA-Z0-9_-]+$", t):
                        existing_token = t
                        break
    except Exception:
        pass
        
    try:
        import json
        access_file = STATE_DIR / "access.json"
        if access_file.exists():
            data = json.loads(access_file.read_text("utf-8"))
            if data.get("allowFrom") and isinstance(data["allowFrom"], list) and len(data["allowFrom"]) > 0:
                o = str(data["allowFrom"][0]).strip()
                if re.match(r"^[0-9]+$", o):
                    existing_owner = o
    except Exception:
        pass

    if existing_token and existing_owner:
        print(f"{CLR_GREEN}✅ Найдена существующая конфигурация!{CLR_RESET}")
        print(f"  🔑 Токен: {CLR_YELLOW}{existing_token[:6]}...{existing_token[-6:]}{CLR_RESET}")
        print(f"  👤 ID владельца: {CLR_YELLOW}{existing_owner}{CLR_RESET}")
        bot_token = existing_token
        owner_id = existing_owner
    else:
        while True:
            bot_token = input(f"🔑 {CLR_YELLOW}Введите Telegram Bot Token:{CLR_RESET} ").strip()
            if re.match(r"^[0-9]+:[a-zA-Z0-9_-]+$", bot_token):
                break
            print(f"{CLR_RED}❌ Неверный формат токена! Пример: 123456789:ABCdefGhIJKlmNoPQRsTUVwxyZ{CLR_RESET}")

        while True:
            owner_id = input(f"👤 {CLR_YELLOW}Введите Ваш Telegram ID (только цифры):{CLR_RESET} ").strip()
            if re.match(r"^[0-9]+$", owner_id):
                break
            print(f"{CLR_RED}❌ ID должен состоять только из цифр!{CLR_RESET}")
        
    print()

    # 2. Установка pip-пакетов
    print(f"{CLR_CYAN}[2/5] Установка зависимостей Python...{CLR_RESET}")
    
    # Пытаемся обновить pip, но не падаем при неудаче
    try:
        subprocess.run([sys.executable, "-m", "pip", "install", "--upgrade", "pip", "--break-system-packages"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass

    # Умная установка пакетов с поддержкой --break-system-packages
    packages = ["aiogram", "orjson", "aiohttp", "curl_cffi", "playwright"]
    installed = False
    
    try:
        # Пробуем установить с флагом --break-system-packages (для Debian/Ubuntu PEP 668)
        subprocess.run([sys.executable, "-m", "pip", "install"] + packages + ["--break-system-packages"], check=True)
        installed = True
    except subprocess.CalledProcessError:
        pass

    if not installed:
        try:
            # Если не вышло (например, старый pip), пробуем без флага
            subprocess.run([sys.executable, "-m", "pip", "install"] + packages, check=True)
        except Exception as e:
            print(f"{CLR_RED}❌ Ошибка при установке зависимостей: {e}{CLR_RESET}")
            sys.exit(1)

    # Инициализация playwright
    try:
        subprocess.run(["playwright", "install", "chromium"], check=True)
    except Exception as e:
        print(f"{CLR_RED}❌ Ошибка при установке браузера Playwright: {e}{CLR_RESET}")
        sys.exit(1)

    # Пути
    INSTALL_DIR = Path(__file__).parent.resolve()
    HOME_DIR = Path.home()
    STATE_DIR = HOME_DIR / ".claude" / "channels" / "telegram"
    STATE_DIR.mkdir(parents=True, exist_ok=True)

    # Запись настроек
    (STATE_DIR / ".env").write_text(f"TELEGRAM_BOT_TOKEN={bot_token}\n", encoding="utf-8")
    (STATE_DIR / "access.json").write_text(f'{{"allowFrom": ["{owner_id}"]}}\n', encoding="utf-8")
    print(f"{CLR_GREEN}✅ Конфигурация успешно сохранена в {STATE_DIR}{CLR_RESET}")

    # 3. Создание обертки запуска
    print(f"\n{CLR_CYAN}[3/5] Создание обертки запуска бота...{CLR_RESET}")
    
    wrapper_code = render_wrapper(HOME_DIR, INSTALL_DIR)


    if sys.platform == "win32":
        WRAPPER_PATH = STATE_DIR / "claude-telegram-bot.py"
        WRAPPER_PATH.write_text(wrapper_code, encoding="utf-8")
        
        BAT_PATH = STATE_DIR / "start-bot.bat"
        BAT_PATH.write_text(f'@echo off\n"{sys.executable}" "{WRAPPER_PATH}"\n', encoding="utf-8")
        print(f"{CLR_GREEN}✅ Скрипты запуска успешно созданы в {STATE_DIR}{CLR_RESET}")
    else:
        WRAPPER_PATH = Path("/usr/local/bin/claude-telegram-bot")
        try:
            temp_file = Path("/tmp/claude-telegram-bot.tmp")
            temp_file.write_text(wrapper_code, encoding="utf-8")
            
            if os.access("/usr/local/bin", os.W_OK):
                shutil.move(str(temp_file), str(WRAPPER_PATH))
                WRAPPER_PATH.chmod(0o755)
            else:
                subprocess.run(["sudo", "mv", str(temp_file), str(WRAPPER_PATH)], check=True)
                subprocess.run(["sudo", "chmod", "755", str(WRAPPER_PATH)], check=True)
            print(f"{CLR_GREEN}✅ Обертка запуска успешно установлена в {WRAPPER_PATH}{CLR_RESET}")
        except Exception as e:
            print(f"{CLR_RED}❌ Ошибка создания запускаемой обертки: {e}{CLR_RESET}")

    # 3.5 Регистрация маркетплейса justidev-marketplace и одобрение плагина
    print(f"\n{CLR_CYAN}[3.5] Регистрация плагина в настройках Claude Code...{CLR_RESET}")
    
    # Создаем папку маркетплейса и символическую ссылку
    marketplace_dir = HOME_DIR / "justidev-marketplace"
    try:
        marketplace_dir.mkdir(parents=True, exist_ok=True)
        
        # Создаем манифест маркетплейса .claude-plugin/marketplace.json
        import json
        m_meta_dir = marketplace_dir / ".claude-plugin"
        m_meta_dir.mkdir(parents=True, exist_ok=True)
        m_json_path = m_meta_dir / "marketplace.json"
        m_json_data = {
            "name": "justidev-marketplace",
            "owner": {
                "name": "justidev",
                "url": "https://t.me/justidev"
            },
            "plugins": [
                {
                    "name": "claude-gram",
                    "source": "claude-gram",
                    "description": "Self-hosted Telegram channel bridge for Claude Code — text, files, photos, HTML formatting, multi-account, auto-updates."
                }
            ]
        }
        m_json_path.write_text(json.dumps(m_json_data, indent=2), encoding="utf-8")
        print(f"{CLR_GREEN}✅ Манифест маркетплейса создан: {m_json_path}{CLR_RESET}")

        plugin_link = marketplace_dir / "claude-gram"
        
        # Если ссылка или папка уже есть, удалим её перед созданием
        if plugin_link.exists() or plugin_link.is_symlink():
            if plugin_link.is_symlink():
                plugin_link.unlink()
            elif plugin_link.is_dir() and not plugin_link.is_symlink():
                shutil.rmtree(plugin_link)
            else:
                plugin_link.unlink()

        if sys.platform != "win32":
            plugin_link.symlink_to(INSTALL_DIR)
        else:
            os.symlink(INSTALL_DIR, plugin_link, target_is_directory=True)
        print(f"{CLR_GREEN}✅ Символическая ссылка локального маркетплейса создана: {plugin_link} -> {INSTALL_DIR}{CLR_RESET}")
    except Exception as e:
        print(f"{CLR_RED}⚠️ Не удалось настроить локальный маркетплейс: {e}{CLR_RESET}")

    # Создаем папку маркетплейсов плагина и символическую ссылку
    marketplaces_dir = HOME_DIR / ".claude" / "plugins" / "marketplaces"
    try:
        marketplaces_dir.mkdir(parents=True, exist_ok=True)
        version_link = marketplaces_dir / "justidev-marketplace"
        
        if version_link.exists() or version_link.is_symlink():
            if version_link.is_symlink():
                version_link.unlink()
            elif version_link.is_dir() and not version_link.is_symlink():
                shutil.rmtree(version_link)
            else:
                version_link.unlink()

        if sys.platform != "win32":
            version_link.symlink_to(INSTALL_DIR)
        else:
            os.symlink(INSTALL_DIR, version_link, target_is_directory=True)
        print(f"{CLR_GREEN}✅ Символическая ссылка в маркетплейсах Claude Code создана: {version_link} -> {INSTALL_DIR}{CLR_RESET}")
    except Exception as e:
        print(f"{CLR_RED}⚠️ Не удалось настроить маркетплейс плагина: {e}{CLR_RESET}")

    try:
        import json
        settings_path = HOME_DIR / ".claude" / "settings.json"
        settings_data = {}
        if settings_path.exists():
            try:
                settings_data = json.loads(settings_path.read_text("utf-8"))
            except Exception:
                pass
        
        # Добавляем маркетплейс justidev-marketplace с динамическим путем
        if "extraKnownMarketplaces" not in settings_data:
            settings_data["extraKnownMarketplaces"] = {}
        
        settings_data["extraKnownMarketplaces"]["justidev-marketplace"] = {
            "source": {
                "source": "directory",
                "path": str(marketplace_dir)
            }
        }
        
        # Очистим старые неиспользуемые маркетплейсы
        for old_market in ["justi-modules", "ripcats-marketplace"]:
            if old_market in settings_data["extraKnownMarketplaces"]:
                del settings_data["extraKnownMarketplaces"][old_market]

        # Включаем плагин
        if "enabledPlugins" not in settings_data:
            settings_data["enabledPlugins"] = {}
        settings_data["enabledPlugins"]["claude-gram@justidev-marketplace"] = True

        # Очистим старые неиспользуемые плагины
        for old_plug in ["claude-gram-v2@justi-modules", "claude-gram@ripcats-marketplace"]:
            if old_plug in settings_data["enabledPlugins"]:
                del settings_data["enabledPlugins"][old_plug]
            
        settings_path.parent.mkdir(parents=True, exist_ok=True)
        settings_path.write_text(json.dumps(settings_data, indent=2), encoding="utf-8")
        print(f"{CLR_GREEN}✅ Настройки settings.json успешно обновлены в {settings_path}{CLR_RESET}")

        # Обновляем installed_plugins.json
        installed_plugins_path = HOME_DIR / ".claude" / "plugins" / "installed_plugins.json"
        installed_data = {"version": 2, "plugins": {}}
        if installed_plugins_path.exists():
            try:
                installed_data = json.loads(installed_plugins_path.read_text("utf-8"))
            except Exception:
                pass
        
        if "plugins" not in installed_data:
            installed_data["plugins"] = {}
            
        installed_data["plugins"]["claude-gram@justidev-marketplace"] = [
            {
                "scope": "user",
                "installPath": str(HOME_DIR / ".claude" / "plugins" / "marketplaces" / "justidev-marketplace"),
                "version": "2.0.0",
                "installedAt": "2026-06-30T08:00:00.000Z",
                "lastUpdated": "2026-06-30T08:00:00.000Z"
            }
        ]
        
        # Очистим старые плагины из реестра установленных
        for old_plug in ["claude-gram-v2@justi-modules", "claude-gram@ripcats-marketplace"]:
            if old_plug in installed_data["plugins"]:
                del installed_data["plugins"][old_plug]
                
        installed_plugins_path.write_text(json.dumps(installed_data, indent=2), encoding="utf-8")
        print(f"{CLR_GREEN}✅ Реестр установленных плагинов обновлен в {installed_plugins_path}{CLR_RESET}")

        # Обновляем known_marketplaces.json
        known_marketplaces_path = HOME_DIR / ".claude" / "plugins" / "known_marketplaces.json"
        known_data = {}
        if known_marketplaces_path.exists():
            try:
                known_data = json.loads(known_marketplaces_path.read_text("utf-8"))
            except Exception:
                pass
                
        known_data["justidev-marketplace"] = {
            "source": {
                "source": "directory",
                "path": str(marketplace_dir)
            },
            "installLocation": str(marketplace_dir),
            "lastUpdated": "2026-06-30T08:00:00.000Z"
        }
        
        # Очистим старые маркетплейсы из реестра
        for old_market in ["justi-modules", "ripcats-marketplace"]:
            if old_market in known_data:
                del known_data[old_market]
            
        known_marketplaces_path.write_text(json.dumps(known_data, indent=2), encoding="utf-8")
        print(f"{CLR_GREEN}✅ Реестр известных маркетплейсов обновлен в {known_marketplaces_path}{CLR_RESET}")

    except Exception as e:
        print(f"{CLR_RED}⚠️ Ошибка регистрации плагина в реестрах: {e}{CLR_RESET}")

    # Добавляем предустановленные разрешения в settings.local.json
    try:
        settings_local_path = HOME_DIR / ".claude" / "settings.local.json"
        local_data = {}
        if settings_local_path.exists():
            try:
                local_data = json.loads(settings_local_path.read_text("utf-8"))
            except Exception:
                pass
        
        if "permissions" not in local_data or not isinstance(local_data["permissions"], dict):
            local_data["permissions"] = {}
        if "allow" not in local_data["permissions"] or not isinstance(local_data["permissions"]["allow"], list):
            local_data["permissions"]["allow"] = []
            
        required_perms = [
            "mcp__claude-gram@justidev-marketplace__telegram-reply",
            "mcp__claude-gram@justidev-marketplace__*"
        ]
        modified = False
        for perm in required_perms:
            if perm not in local_data["permissions"]["allow"]:
                local_data["permissions"]["allow"].append(perm)
                modified = True
                
        if modified or not settings_local_path.exists():
            settings_local_path.parent.mkdir(parents=True, exist_ok=True)
            settings_local_path.write_text(json.dumps(local_data, indent=2), encoding="utf-8")
            print(f"{CLR_GREEN}✅ Предустановленные разрешения плагина добавлены в {settings_local_path}{CLR_RESET}")
    except Exception as e:
        print(f"{CLR_RED}⚠️ Ошибка предустановки разрешений плагина: {e}{CLR_RESET}")

    # Запускаем интерактивное одобрение плагина перед стартом службы
    try:
        print(f"\n{CLR_YELLOW}👉 Сейчас запустится Claude Code для одобрения плагина.{CLR_RESET}")
        print(f"{CLR_YELLOW}Пожалуйста, введите 'y' и нажмите Enter, когда появится запрос на подтверждение.{CLR_RESET}\n")
        time.sleep(2)
        subprocess.run(["claude", "--channels", "plugin:claude-gram@justidev-marketplace", "-c", "exit"])
    except Exception as e:
        print(f"{CLR_RED}⚠️ Не удалось запустить интерактивное одобрение плагина: {e}{CLR_RESET}")

    # 4. Настройка автозапуска / службы
    print(f"\n{CLR_CYAN}[4/5] Настройка фоновой службы автозапуска...{CLR_RESET}")
    
    if sys.platform == "win32":
        # Настройка планировщика задач Windows
        task_name = "ClaudeGramBot"
        cmd = ["schtasks", "/Create", "/TN", task_name, "/TR", f'cmd.exe /c "{BAT_PATH}"', "/SC", "ONLOGON", "/F"]
        try:
            subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
            print(f"{CLR_GREEN}✅ Задача '{task_name}' успешно зарегистрирована в Планировщике задач Windows!{CLR_RESET}")
        except Exception:
            # Альтернатива: Автозагрузка
            startup_dir = Path(os.environ["APPDATA"]) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"
            try:
                # Создаем vbs скрипт для скрытого запуска
                vbs_path = startup_dir / "Claude-Gram.vbs"
                vbs_path.write_text(f'CreateObject("Wscript.Shell").Run "cmd.exe /c {BAT_PATH}", 0, True', encoding="utf-8")
                print(f"{CLR_GREEN}✅ Скрипт запуска добавлен в папку автозагрузки Windows.{CLR_RESET}")
            except Exception as e:
                print(f"{CLR_RED}❌ Не удалось настроить автозапуск: {e}{CLR_RESET}")

    elif sys.platform == "darwin":
        # launchd на macOS
        plist_path = HOME_DIR / "Library" / "LaunchAgents" / "me.ripcats.claude-telegram.plist"
        plist_content = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>me.ripcats.claude-telegram</string>
    <key>ProgramArguments</key>
    <array>
        <string>{WRAPPER_PATH}</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>EnvironmentVariables</key>
    <dict>
        <key>HOME</key>
        <string>{HOME_DIR}</string>
        <key>PATH</key>
        <string>/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin</string>
    </dict>
</dict>
</plist>
"""
        try:
            plist_path.parent.mkdir(parents=True, exist_ok=True)
            plist_path.write_text(plist_content, encoding="utf-8")
            subprocess.run(["launchctl", "unload", str(plist_path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            subprocess.run(["launchctl", "load", "-w", str(plist_path)], check=True)
            print(f"{CLR_GREEN}✅ launchd агент успешно запущен в macOS!{CLR_RESET}")
        except Exception as e:
            print(f"{CLR_RED}❌ Не удалось настроить launchd агент: {e}{CLR_RESET}")

    else:
        # Пользователь claude-login нужен флоу /login — создаём до генерации юнита.
        ensure_login_user()

        # systemd в Linux
        if Path("/run/systemd/system").exists() and shutil.which("systemctl"):
            service_content = f"""[Unit]
Description=Claude Code Telegram channel (always-on)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User={resolve_service_user(HOME_DIR)}
WorkingDirectory={HOME_DIR}
Environment=HOME={HOME_DIR}
Environment=CLAUDE_ALLOW_ROOT=1
Environment=PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
ExecStart={WRAPPER_PATH}
Restart=always
RestartSec=5
TimeoutStopSec=20
KillMode=control-group

[Install]
WantedBy=multi-user.target
"""
            service_file = Path("/etc/systemd/system/claude-telegram.service")
            try:
                temp_service = Path("/tmp/claude-telegram.service.tmp")
                temp_service.write_text(service_content, encoding="utf-8")
                
                subprocess.run(["sudo", "mv", str(temp_service), str(service_file)], check=True)
                subprocess.run(["sudo", "systemctl", "daemon-reload"], check=True)
                subprocess.run(["sudo", "systemctl", "enable", "claude-telegram.service"], check=True)
                subprocess.run(["sudo", "systemctl", "restart", "claude-telegram.service"], check=True)
                print(f"{CLR_GREEN}✅ systemd служба claude-telegram успешно запущена!{CLR_RESET}")
            except Exception as e:
                print(f"{CLR_RED}❌ Ошибка при настройке systemd службы: {e}{CLR_RESET}")

            # Guest template unit (instances are enabled manually: systemctl enable --now claude-telegram-guest@<uid>)
            guest_service_content = render_guest_unit(
                resolve_service_user(HOME_DIR), HOME_DIR,
                "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin", WRAPPER_PATH)
            guest_service_file = Path("/etc/systemd/system/claude-telegram-guest@.service")
            try:
                temp_guest = Path("/tmp/claude-telegram-guest@.service.tmp")
                temp_guest.write_text(guest_service_content, encoding="utf-8")
                subprocess.run(["sudo", "mv", str(temp_guest), str(guest_service_file)], check=True)
                subprocess.run(["sudo", "systemctl", "daemon-reload"], check=True)
                print(f"{CLR_GREEN}✅ Шаблон гостевых служб claude-telegram-guest@ установлен!{CLR_RESET}")
            except Exception as e:
                print(f"{CLR_RED}⚠️ Не удалось установить шаблон гостевой службы: {e}{CLR_RESET}")
        elif shutil.which("pm2"):
            try:
                subprocess.run(["pm2", "delete", "claude-telegram"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                subprocess.run(["pm2", "start", str(WRAPPER_PATH), "--name", "claude-telegram", "--update-env"], check=True)
                subprocess.run(["pm2", "save"], check=True)
                print(f"{CLR_GREEN}✅ Бот успешно зарегистрирован в PM2!{CLR_RESET}")
            except Exception as e:
                print(f"{CLR_RED}❌ Ошибка при регистрации в PM2: {e}{CLR_RESET}")
        else:
            # Fallback: nohup
            run_script = STATE_DIR / "run_daemon.sh"
            daemon_content = f"""#!/usr/bin/env bash
while true; do
    echo "[$(date)] Starting Claude-Gram bot..." >> "{STATE_DIR}/daemon.log"
    {WRAPPER_PATH} >> "{STATE_DIR}/daemon.log" 2>&1
    sleep 5
done
"""
            try:
                run_script.write_text(daemon_content, encoding="utf-8")
                run_script.chmod(0o755)
                subprocess.Popen(["nohup", str(run_script)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
                print(f"{CLR_GREEN}✅ Бот запущен в фоновом режиме через nohup (лог в {STATE_DIR}/daemon.log){CLR_RESET}")
            except Exception as e:
                print(f"{CLR_RED}❌ Не удалось настроить запуск через nohup: {e}{CLR_RESET}")

    # 5. Завершение
    print()
    print(gradient_text("  ========================================================================="))
    print(f"  🎉 {CLR_GREEN}Установка Claude-Gram v2 успешно завершена!{CLR_RESET}")
    print(f"  👤 Владелец: ID {CLR_CYAN}{owner_id}{CLR_RESET}")
    print(f"  👉 Отправьте боту команду {CLR_YELLOW}/start{CLR_RESET} в Telegram для начала работы.")
    print(gradient_text("  ========================================================================="))
    print()

if __name__ == "__main__":
    main()
