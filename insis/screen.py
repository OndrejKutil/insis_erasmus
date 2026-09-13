"""
Full-screen terminal rendering.

The app owns the whole screen and redraws it completely on every keystroke, so
what is displayed is always the current state.

Everything is drawn into the alternate screen buffer, so quitting restores the
terminal exactly as it was and leaves the user's scrollback untouched.
"""

import os
import shutil
import sys

# --- ANSI ------------------------------------------------------------------

ALT_ON = "\x1b[?1049h"
ALT_OFF = "\x1b[?1049l"
CURSOR_OFF = "\x1b[?25l"
CURSOR_ON = "\x1b[?25h"
HOME = "\x1b[H"
CLEAR = "\x1b[2J"

DIM = "\x1b[2m"
REVERSE = "\x1b[7m"
RESET = "\x1b[0m"
RED = "\x1b[31m"
GREEN = "\x1b[32m"
YELLOW = "\x1b[33m"
CYAN = "\x1b[36m"
MAGENTA = "\x1b[35m"

_enabled = False


def enable_ansi() -> bool:
    """
    Turn on VT processing so escape codes work in a Windows console.

    Modern Windows terminals support ANSI but not always by default; without
    this the screen fills with literal escape sequences.
    """
    global _enabled
    if _enabled:
        return True
    if os.name == "nt":
        try:
            import ctypes
            k = ctypes.windll.kernel32
            handle = k.GetStdHandle(-11)          # STD_OUTPUT_HANDLE
            mode = ctypes.c_uint32()
            if k.GetConsoleMode(handle, ctypes.byref(mode)):
                # ENABLE_VIRTUAL_TERMINAL_PROCESSING
                k.SetConsoleMode(handle, mode.value | 0x0004)
        except Exception:
            return False
    _enabled = True
    return True


def size() -> tuple[int, int]:
    cols, rows = shutil.get_terminal_size((100, 30))
    return max(60, min(cols, 200)), max(16, rows)


def width() -> int:
    return size()[0]


def supported() -> bool:
    try:
        return sys.stdout.isatty()
    except (AttributeError, ValueError):
        return False


# --- screen lifecycle ------------------------------------------------------

class FullScreen:
    """Context manager owning the alternate screen buffer."""

    def __init__(self, active: bool = True):
        self.active = active and supported()

    def __enter__(self):
        if self.active:
            enable_ansi()
            sys.stdout.write(ALT_ON + CURSOR_OFF + CLEAR + HOME)
            sys.stdout.flush()
        return self

    def __exit__(self, *exc):
        if self.active:
            sys.stdout.write(CURSOR_ON + ALT_OFF)
            sys.stdout.flush()
        return False


def clear() -> None:
    if supported():
        sys.stdout.write(CLEAR + HOME)
    sys.stdout.flush()


def show_cursor(on: bool) -> None:
    if supported():
        sys.stdout.write(CURSOR_ON if on else CURSOR_OFF)
        sys.stdout.flush()


# --- text helpers ----------------------------------------------------------

def visible_len(s: str) -> int:
    """Length ignoring ANSI escapes, so padding stays correct with colour."""
    out, i = 0, 0
    while i < len(s):
        if s[i] == "\x1b":
            j = s.find("m", i)
            if j == -1:
                break
            i = j + 1
            continue
        out += 1
        i += 1
    return out


def pad(s: str, n: int) -> str:
    gap = n - visible_len(s)
    return s + " " * gap if gap > 0 else s


def fit(text: str, n: int) -> str:
    """Pad to n, ellipsizing only when it genuinely does not fit."""
    text = text or ""
    if len(text) <= n:
        return text.ljust(n)
    return text[: max(1, n - 1)] + "…"


# --- the frame -------------------------------------------------------------

def rule(cols: int | None = None) -> str:
    """The one horizontal rule everything uses, so widths cannot drift apart."""
    cols = cols or size()[0]
    return f"{DIM}  {'─' * (cols - 4)}{RESET}"


def _window(body: list[str], height: int, focus: int | None) -> list[str]:
    """
    The slice of `body` that fits, scrolled to keep `focus` visible.

    A long list must never be silently cut off: rows below the fold would be
    invisible and moving the cursor into them would appear to do nothing.
    Anything hidden is announced.
    """
    if height <= 0 or len(body) <= height:
        return body

    start = 0
    if focus is not None:
        # Keep the focused line roughly centred once it would leave the view.
        start = max(0, min(focus - height // 2, len(body) - height))

    end = start + height
    shown = list(body[start:end])
    if start > 0:
        shown[0] = f"{DIM}  ▲ {start} more above{RESET}"
    if end < len(body):
        shown[-1] = f"{DIM}  ▼ {len(body) - end} more below{RESET}"
    return shown


def render(title: str, body: list[str], footer: list[str] = None,
           status: str = "", message: str = "", focus: int | None = None) -> None:
    """
    Draw one complete screen: title bar, body, then keys pinned to the bottom.

    The body is padded out so the footer always sits on the last lines, which
    keeps the layout from jumping around as content changes length. If the body
    does not fit it is scrolled to keep `focus` (a line index) on screen, with
    counts of what is hidden.
    """
    cols, rows = size()
    footer = footer or []
    out = [HOME + CLEAR] if supported() else []

    out.append(f"{REVERSE}{pad('  ' + title, cols - len(status) - 2)}"
               f"{status}  {RESET}")
    out.append("")

    body_rows = rows - len(footer) - 5
    shown = _window(body, body_rows, focus)
    out.extend(shown)

    filler = body_rows - len(shown)
    if filler > 0:
        out.extend([""] * filler)

    if message:
        out.append(f"  {message}")
    else:
        out.append("")

    out.append(rule(cols))
    for line in footer:
        out.append(f"  {line}")

    sys.stdout.write("\n".join(out))
    sys.stdout.flush()


def prompt_line(label: str, hidden: bool = False) -> str:
    """
    Ask for free text at the bottom of the screen.

    Single keypresses are wrong for a topic name or a password, so the cursor
    comes back and a normal line editor runs; the caller redraws afterwards.
    """
    show_cursor(True)
    try:
        if hidden:
            import getpass
            return getpass.getpass(f"  {label}: ").strip()
        return input(f"  {label}: ").strip()
    except (EOFError, KeyboardInterrupt):
        return ""
    finally:
        show_cursor(False)
