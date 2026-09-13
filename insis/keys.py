"""
Single-keypress input, on Windows and on the Pi.

The UI reacts to a single key without waiting for Enter. Free text (an ntfy
topic, a password) is read elsewhere with a normal line editor.

Falls back to line input when stdin is not a terminal - a pipe, or a
non-interactive SSH command - so a raw-mode read cannot hang a headless run.
"""

import sys

try:                                    # Windows
    import msvcrt
except ImportError:                     # POSIX
    msvcrt = None

try:                                    # POSIX
    import termios
    import tty
except ImportError:                     # Windows
    termios = None
    tty = None


def interactive() -> bool:
    """Can we actually read single keys from a terminal?"""
    try:
        return sys.stdin.isatty()
    except (AttributeError, ValueError):
        return False


def read_key() -> str:
    """
    Block for one keypress and return it.

    Returns a single character, or a name for the keys that matter:
    'ENTER', 'ESC', 'BACKSPACE', 'UP', 'DOWN', 'LEFT', 'RIGHT'.
    Ctrl-C raises KeyboardInterrupt, as it would in cooked mode.
    """
    if not interactive():
        line = sys.stdin.readline()
        if not line:
            raise EOFError
        line = line.rstrip("\n")
        return line[0] if line else "ENTER"

    if msvcrt is not None:
        ch = msvcrt.getwch()
        if ch in ("\x00", "\xe0"):      # arrow / function key prefix
            code = msvcrt.getwch()
            return {"H": "UP", "P": "DOWN", "K": "LEFT", "M": "RIGHT"}.get(
                code, "")
        return _normalise(ch)

    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        ch = sys.stdin.read(1)
        if ch == "\x1b":                # escape sequence, maybe an arrow
            seq = sys.stdin.read(2)
            return {"[A": "UP", "[B": "DOWN", "[D": "LEFT",
                    "[C": "RIGHT"}.get(seq, "ESC")
        return _normalise(ch)
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)


def _normalise(ch: str) -> str:
    if ch == "\x03":
        raise KeyboardInterrupt
    if ch == "\x04":
        raise EOFError
    if ch in ("\r", "\n"):
        return "ENTER"
    if ch in ("\x08", "\x7f"):
        return "BACKSPACE"
    if ch == "\x1b":
        return "ESC"
    return ch
