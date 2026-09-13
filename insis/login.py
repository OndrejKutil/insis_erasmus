"""
The interactive login, and the one thing worth remembering between runs.

Credentials are never persisted. They are prompted for on every start and only
ever live in memory, which is why nothing here can run unattended. That is a
deliberate trade, inherited from the enrolment watchdog: a password on disk
would be worse than a prompt.

The username *is* remembered, since it identifies you to nobody who could not
already read it off your InSIS pages, and retyping it every run is friction
for no gain.
"""

import getpass
import json
import os
from pathlib import Path

from .client import Insis

STATE_FILE = Path(__file__).resolve().parent.parent / "insis_erasmus_state.json"


def load_state() -> dict:
    if not STATE_FILE.exists():
        return {}
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def save_state(state: dict) -> None:
    STATE_FILE.write_text(
        json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")


def do_login(destination: str | None = None, title: str = "InSIS") -> Insis:
    """
    Prompt for credentials and log in. Returns a live session.

    `destination` is the page you actually want: InSIS carries it through the
    login form, so logging in lands you there instead of on a dashboard.

    INSIS_USER and INSIS_PASSWORD are honoured if set, for re-running a
    capture repeatedly during development without retyping. The 2FA code is
    still typed by hand every time - there is nothing to cache it in.
    """
    state = load_state()
    remembered = state.get("username", "")

    print()
    print(f"  {title}")
    print("  Nothing is stored: your password lives in memory for this run only.")
    print()

    insis = Insis()
    try:
        username = os.environ.get("INSIS_USER", "").strip()
        if not username:
            prompt = f"  username [{remembered}]: " if remembered else "  username: "
            username = input(prompt).strip() or remembered
        password = os.environ.get("INSIS_PASSWORD", "") or getpass.getpass(
            "  password (hidden): ")
    except (EOFError, KeyboardInterrupt):
        print()
        raise SystemExit(130)

    if not username or not password:
        print("\n  Need both a username and a password.")
        raise SystemExit(1)

    def ask_code() -> str:
        print("\n  InSIS wants your 6-digit authenticator code.")
        try:
            return input("  code: ").strip()
        except (EOFError, KeyboardInterrupt):
            return ""

    print("\n  logging in...")
    insis.login(username, password, get_code=ask_code, destination=destination)
    print("  logged in.")

    if username != remembered:
        state["username"] = username
        save_state(state)
    return insis
