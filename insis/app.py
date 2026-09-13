"""
The full-screen front end.

Same shape as the enrolment watchdog: the app owns the terminal, redraws
everything on every keystroke, and runs in the alternate screen buffer so
quitting gives the terminal back untouched. Single keypresses drive it; only
free text (the option search box) falls back to a line editor.

Four screens, in the order you move through them:

    filters  -> pick years, countries, institutions...   (Enter opens one)
    options  -> Space toggles, as many as you like       (Esc goes back)
    results  -> the stays found, Space toggles           (d downloads)
    progress -> what is happening, while it happens

The multi-select is the whole point, and it is also the thing InSIS cannot do:
its form takes one value per field, so picking three years means three
searches. The plan is shown before it runs.
"""

import time
import unicodedata

from . import keys, parse, screen, search
from .client import Insis, InsisError, SessionExpired, Timeout
from .login import load_state, save_state
from .models import Filter, Stay
from .screen import CYAN, DIM, GREEN, RED, RESET, REVERSE, YELLOW, fit
from .store import Store

TITLE = "InSIS Erasmus reports"

# A report is one request to a university's server. Nothing here is urgent
# enough to hammer it: a short pause between fetches keeps a 170-report run
# looking like a person reading rather than a scraper.
PAUSE = 0.3


def fold(text: str) -> str:
    """
    Casefold and strip diacritics, for the option search box.

    Typing 'nemecko' should find 'Německo': the alternative is a search box
    that silently fails for exactly the words this archive is full of.
    """
    stripped = unicodedata.normalize("NFKD", text or "")
    return "".join(c for c in stripped if not unicodedata.combining(c)).casefold()


class App:
    def __init__(self, insis: Insis, store: Store):
        self.insis = insis
        self.store = store
        self.filters: list[Filter] = []
        self.index_html = ""
        self.index_url = ""

        self.view = "filters"
        self.field: Filter | None = None      # the filter being edited
        self.query = ""                       # option search box
        self.cursor = {"filters": 0, "options": 0, "results": 0}

        self.stays: list[Stay] = []
        self.selected: set[int] = set()
        self.want_pdf = True

        self.message = ""
        self.error = ""

    # -- data --------------------------------------------------------------

    def load_archive(self) -> None:
        """Read the search form: what can be filtered, and with which ids."""
        self.index_html, self.index_url = self.insis.open_archive()
        self.filters = parse.parse_filters(self.index_html)
        self.restore_choices()

    def restore_choices(self) -> None:
        """
        Re-apply the previous run's filters, dropping any that no longer exist.

        Option ids are InSIS's and can be retired between semesters; a stale id
        would search for nothing and look like an empty archive.
        """
        saved = load_state().get("filters", {})
        for f in self.filters:
            valid = {o.value for o in f.options}
            f.chosen = [v for v in saved.get(f.name, []) if v in valid]

    def remember_choices(self) -> None:
        state = load_state()
        state["filters"] = {f.name: f.chosen for f in self.filters if f.chosen}
        save_state(state)

    # -- rows per view -----------------------------------------------------

    def chosen_map(self) -> dict[str, list[str]]:
        """What is picked everywhere, which is what narrows each list."""
        return {f.name: f.chosen for f in self.filters}

    def offered(self) -> list:
        """
        The current filter's options, narrowed by the *other* filters.

        The real form does this: choose Spain and the institution list becomes
        Spanish, choose a faculty and the degrees become that faculty's. The
        data for it ships inside the page, so this costs no request.
        """
        return self.field.available(self.chosen_map()) if self.field else []

    def options(self) -> list:
        """...and narrowed again by whatever has been typed into the box."""
        offered = self.offered()
        if not self.query:
            return offered
        needle = fold(self.query)
        return [o for o in offered if needle in fold(o.label)]

    def prune(self) -> None:
        """
        Drop picks the new parent no longer offers, and say so.

        Silence here would be the worst outcome: an invisible filter, still in
        every search, returning nothing for no reason the user can see.
        """
        dropped = []
        chosen = self.chosen_map()
        for f in self.filters:
            for value in f.prune(chosen):
                dropped.append(f"{f.label}: {f.label_for(value)}")
            chosen[f.name] = f.chosen
        if dropped:
            self.message = (f"{YELLOW}No longer offered, so dropped: "
                            f"{'; '.join(dropped[:3])}{RESET}")

    def rows(self) -> list:
        return {"filters": self.filters,
                "options": self.options(),
                "results": self.stays}.get(self.view, [])

    def move(self, delta: int) -> None:
        items = self.rows()
        if not items:
            return
        cur = self.cursor.get(self.view, 0)
        self.cursor[self.view] = max(0, min(len(items) - 1, cur + delta))

    def current(self):
        items = self.rows()
        i = self.cursor.get(self.view, 0)
        return items[i] if 0 <= i < len(items) else None

    # -- drawing -----------------------------------------------------------

    def draw(self) -> None:
        body, footer = getattr(self, f"draw_{self.view}")()
        status = ""
        if self.view == "results":
            status = f"{len(self.selected)}/{len(self.stays)} selected"
        message = self.error and f"{RED}{self.error}{RESET}" or self.message
        screen.render(TITLE, body, footer, status=status, message=message,
                      focus=self.cursor.get(self.view, 0))

    def draw_filters(self):
        body = ["  Pick what to search for. Several values per row are fine -",
                f"  {DIM}InSIS takes one at a time, so the app runs one search "
                f"per combination.{RESET}", ""]
        chosen = self.chosen_map()
        for i, f in enumerate(self.filters):
            mark = f"{CYAN}●{RESET}" if f.chosen else f"{DIM}○{RESET}"
            colour = "" if f.chosen else DIM
            # Say when a parent has narrowed this list, so '8 options' in a
            # field that had 118 reads as a consequence, not a glitch.
            offered = len(f.available(chosen))
            count = (f"{offered} of {len(f.options)}"
                     if offered < len(f.options) else f"{len(f.options)}")
            body.append(f"  {REVERSE if i == self.cursor['filters'] else ''}"
                        f" {mark} {fit(f.label, 17)} {colour}{fit(f.summary(), 38)}"
                        f"{RESET}{DIM}{count:>12}{RESET}")

        combos = search.plan(self.filters)
        body += ["", f"  {len(combos)} search{'es' if len(combos) != 1 else ''}"
                     f" to run"]
        # Say why picking twenty universities did not cost twenty searches.
        after = [f.label for f in self.filters
                 if f.name in search.LOCAL and f.chosen]
        if after:
            body.append(f"  {DIM}{', '.join(after)} filtered from the results "
                        f"afterwards - InSIS prints those columns, so they "
                        f"cost no extra search.{RESET}")
        if search.needs_period(self.filters):
            body.append(f"  {YELLOW}Pick at least one academic year: without "
                        f"one InSIS is asked for every exchange ever and "
                        f"times out.{RESET}")

        return body, ["↑↓ move   Enter open   c clear row",
                      "r run the search   l load last results   q quit"]

    def draw_options(self):
        options = self.options()
        offered = self.offered()
        chosen = set(self.field.chosen)

        narrowed = ""
        if len(offered) < len(self.field.options) and self.field.parents:
            parents = ", ".join(
                f.label for f in self.filters if f.name in self.field.parents)
            narrowed = f"  {DIM}narrowed by {parents}{RESET}"

        # The search box is always on screen, and always live: what is typed
        # filters as it is typed, so there is no moment where the list and the
        # box disagree.
        body = [f"  {self.field.label}{narrowed}",
                f"  {CYAN}› {self.query}▏{RESET}"
                f"{DIM}   {len(options)} of {len(offered)}{RESET}",
                ""]
        for i, opt in enumerate(options):
            on = opt.value in chosen
            mark = f"{GREEN}[x]{RESET}" if on else f"{DIM}[ ]{RESET}"
            body.append(f"  {REVERSE if i == self.cursor['options'] else ''}"
                        f" {mark} {fit(opt.label, 70)}{RESET}")
        if not options:
            body.append(f"  {DIM}nothing matches{RESET}")

        return body, ["type to filter   ↑↓ move   Enter toggle",
                      "Esc clear the box, or go back   ← → jump 10"]

    def draw_results(self):
        body = []
        for i, stay in enumerate(self.stays):
            on = i in self.selected
            if not stay.has_report:
                mark = f"{DIM} - {RESET}"
            elif on:
                mark = f"{GREEN}[x]{RESET}"
            else:
                mark = f"{DIM}[ ]{RESET}"
            saved = self.store.has(stay, self.want_pdf)
            colour = DIM if not stay.has_report else (CYAN if saved else "")
            # The state is shown only when it is not the ordinary 'finished',
            # because that is when it explains something: a stay still running
            # or yet to start has no report for the obvious reason.
            state = "" if stay.state.startswith("Ukončený") else stay.state
            body.append(
                f"  {REVERSE if i == self.cursor['results'] else ''}"
                f" {mark} {colour}{fit(stay.institution, 40)} "
                f"{fit(stay.country, 20)} {fit(stay.start, 11)} "
                f"{DIM}{fit(state, 18)}{RESET}"
                f"{CYAN + ' ✓' + RESET if saved else ''}")

        missing = sum(1 for s in self.stays if not s.has_report)
        if missing:
            body += ["", f"  {DIM}{missing} row(s) have no report filed - "
                         f"a stay that has not finished yet cannot have "
                         f"one.{RESET}"]

        pdf = f"{GREEN}on{RESET}" if self.want_pdf else f"{DIM}off{RESET}"
        return body, [f"↑↓ move   Space toggle   a all   n none   PDF: {pdf} (p)",
                      "d download selected   Esc back to filters   q quit"]

    def progress(self, title: str, lines: list[str]) -> None:
        """Redraw mid-operation. No input is read: this is not a view."""
        screen.render(TITLE, [f"  {title}", ""] + lines, [], message="")

    # -- actions -----------------------------------------------------------

    def run_search(self) -> None:
        """
        Run the plan, one combination at a time, merging as it goes.

        A failed search does not abort the rest: three good years and one
        timeout is a useful result, and starting over would cost another
        login. What failed is said plainly at the end rather than left as a
        short list that looks complete.
        """
        combos = search.plan(self.filters)
        self.remember_choices()
        batches, failures, done = [], [], []

        for n, combo in enumerate(combos, 1):
            label = search.describe(combo, self.filters)
            self.progress(
                f"Searching {n}/{len(combos)}: {label}",
                done + [f"  {YELLOW}waiting for InSIS "
                        f"(up to {self.insis.SEARCH_TIMEOUT}s)...{RESET}"])
            try:
                html, url = self.insis.search(
                    self.index_html, self.index_url, combo)
                found = parse.parse_results(html, url)
                batches.append(found)
                done.append(f"  {DIM}{len(found)} rows - {label}{RESET}")
            except Timeout:
                failures.append(f"{label}: timed out")
                done.append(f"  {RED}timed out - {label}{RESET}")
            except SessionExpired:
                raise
            except InsisError as e:
                failures.append(f"{label}: {e}")
                done.append(f"  {RED}failed - {label}{RESET}")

        merged = search.merge(batches)
        kept = search.apply_local(merged, self.filters)
        self.stays = sorted(kept,
                            key=lambda s: (s.country, s.institution, s.start))
        self.selected = {i for i, s in enumerate(self.stays) if s.has_report}
        self.store.save_index(self.stays)

        self.view = "results"
        self.cursor["results"] = 0
        narrowed = (f" (of {len(merged)} returned)"
                    if len(kept) != len(merged) else "")
        found = (f"{len(self.stays)} stays{narrowed}, "
                 f"{sum(s.has_report for s in self.stays)} with reports")
        self.message = (f"{found}. {RED}{len(failures)} search(es) failed: "
                        f"{'; '.join(failures)}{RESET}" if failures else found)

    def download(self) -> None:
        """
        Fetch every selected report, skipping what is already on disk.

        Each report is wrapped in its own try/except: one bad page must not
        end a run of a hundred, and a run that dies silently partway through
        is worse than one that says which three failed.
        """
        targets = [self.stays[i] for i in sorted(self.selected)
                   if self.stays[i].has_report]
        if not targets:
            self.error = "Nothing selected that has a report."
            return

        saved = skipped = failed = 0
        problems: list[str] = []

        for n, stay in enumerate(targets, 1):
            if self.store.has(stay, self.want_pdf):
                skipped += 1
                continue

            self.progress(
                f"Downloading {n}/{len(targets)}",
                [f"  {fit(stay.institution, 50)} {stay.country}", "",
                 f"  {GREEN}{saved} saved{RESET}   {DIM}{skipped} already "
                 f"had{RESET}   {RED if failed else DIM}{failed} failed{RESET}",
                 "", f"  {DIM}Ctrl-C stops after the current one.{RESET}"])

            try:
                html = self.insis.report(stay.report_id, stay.akce)
                report = parse.parse_report(html, stay.report_id, stay.akce)
                self.store.save_report(stay, report)
                if self.want_pdf:
                    self.store.save_pdf(
                        stay, self.insis.report_pdf(stay.report_id, stay.akce))
                saved += 1
            except SessionExpired:
                raise
            except InsisError as e:
                failed += 1
                problems.append(f"{stay.institution[:30]}: {e}")
            except KeyboardInterrupt:
                self.message = f"Stopped. {saved} saved, {len(targets) - n} left."
                return
            time.sleep(PAUSE)

        where = self.store.reports
        self.message = (f"{GREEN}{saved} saved{RESET} to {where}"
                        f"{f', {skipped} already had' if skipped else ''}"
                        f"{f', {RED}{failed} failed{RESET}' if failed else ''}")
        if problems:
            self.error = "; ".join(problems[:3])

    def load_cached(self) -> None:
        """Bring back the last search's rows without asking InSIS again."""
        stays = self.store.load_index()
        if not stays:
            self.error = "No saved results yet - run a search first."
            return
        self.stays = stays
        self.selected = {i for i, s in enumerate(stays) if s.has_report}
        self.view = "results"
        self.cursor["results"] = 0
        self.message = f"{len(stays)} stays from the last search."

    # -- input -------------------------------------------------------------

    def key(self, k: str) -> bool:
        """Handle one keypress. False means quit."""
        self.message = self.error = ""

        if k == "UP":
            self.move(-1)
        elif k == "DOWN":
            self.move(1)
        elif k in ("LEFT", "RIGHT"):
            self.move(-10 if k == "LEFT" else 10)
        elif self.view == "filters":
            return self.key_filters(k)
        elif self.view == "options":
            return self.key_options(k)
        elif self.view == "results":
            return self.key_results(k)
        return True

    def key_filters(self, k: str) -> bool:
        if k in ("q", "ESC"):
            return False
        if k == "ENTER":
            self.field = self.current()
            if self.field:
                self.view, self.query = "options", ""
                self.cursor["options"] = 0
        elif k == "c":
            field = self.current()
            if field:
                field.chosen = []
        elif k == "r":
            if search.needs_period(self.filters):
                self.error = ("Pick at least one academic year first - "
                              "an unrestricted search does not come back.")
            else:
                self.run_search()
        elif k == "l":
            self.load_cached()
        return True

    def key_options(self, k: str) -> bool:
        """
        Typing filters the list; Enter toggles what is highlighted.

        Space cannot be the toggle here, because it has to be typeable: half
        the institutions are 'Something University of Somewhere' and a search
        box that swallows the space bar is a search box for one word.
        """
        if k == "ESC":
            # One Esc clears a search in progress, the next leaves the field.
            # Leaving outright would throw away typing that took effort.
            if self.query:
                self.query = ""
                self.cursor["options"] = 0
            else:
                self.view = "filters"
        elif k == "ENTER":
            opt = self.current()
            if opt:
                if opt.value in self.field.chosen:
                    self.field.chosen.remove(opt.value)
                else:
                    self.field.chosen.append(opt.value)
                self.prune()
        elif k == "BACKSPACE":
            self.query = self.query[:-1]
            self.cursor["options"] = 0
        elif len(k) == 1 and k.isprintable():
            self.query += k
            self.cursor["options"] = 0
        return True

    def key_results(self, k: str) -> bool:
        if k == "q":
            return False
        if k == "ESC":
            self.view = "filters"
        elif k == " ":
            i = self.cursor["results"]
            if 0 <= i < len(self.stays) and self.stays[i].has_report:
                self.selected.symmetric_difference_update({i})
        elif k == "a":
            self.selected = {i for i, s in enumerate(self.stays) if s.has_report}
        elif k == "n":
            self.selected = set()
        elif k == "p":
            self.want_pdf = not self.want_pdf
        elif k == "d":
            self.download()
        return True

    # -- loop --------------------------------------------------------------

    def run(self) -> None:
        self.draw()
        while True:
            try:
                k = keys.read_key()
            except (KeyboardInterrupt, EOFError):
                return
            try:
                if not self.key(k):
                    return
            except SessionExpired:
                self.view = "filters"
                self.error = ("Session expired - InSIS logged you out. "
                              "Restart and log in again.")
            self.draw()
