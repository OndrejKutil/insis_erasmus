"""
Entry point for the InSIS Erasmus report reader.

    uv run main.py              # log in, pick filters, download reports
    uv run main.py --offline    # browse the last search's results, no login

Logs in with your username, password and 6-digit code, then hands over to a
full-screen UI: multi-select the years, countries and institutions you care
about, run the search, tick the reports you want and download them.

Nothing is stored but your username, so this cannot run unattended - the
password lives in memory for one run only.
"""

import argparse
import sys

from insis.app import App
from insis.client import Insis, InsisError
from insis.login import do_login
from insis.screen import FullScreen
from insis.store import Store


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--offline", action="store_true",
                    help="browse the last search's saved results without "
                         "logging in")
    args = ap.parse_args(argv)

    store = Store()

    if args.offline:
        stays = store.load_index()
        if not stays:
            print("\n  No saved results yet. Run without --offline first.")
            return 1
        app = App(Insis(), store)
        app.stays = stays
        app.selected = set()
        app.view = "results"
        app.message = f"{len(stays)} stays from the last search (offline)."
        with FullScreen():
            app.run()
        return 0

    try:
        insis = do_login(title="InSIS Erasmus reports")
    except InsisError as e:
        print(f"\n  Login failed: {e}")
        return 1

    app = App(insis, store)
    try:
        print("  reading the archive's filters...")
        app.load_archive()
    except InsisError as e:
        print(f"\n  Could not open the report archive: {e}")
        return 1

    with FullScreen():
        app.run()
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
