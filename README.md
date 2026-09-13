# insis-erasmus

Read the Erasmus **závěrečné zprávy** — the reports VŠE students write after an
exchange — in bulk, instead of opening them one at a time in InSIS.

InSIS keeps them at `/auth/int/zavzpr.pl` behind a filter form that takes *one
value per dropdown* and prints results as a table of PDF icons. This is a
terminal app that multi-selects across that form, fans the search out into the
several requests InSIS actually accepts, merges the results, and saves each
report as readable markdown, as structured JSON, and as InSIS's own PDF.

```bash
uv sync && uv run main.py
```

---

## What it does

1. **Logs you in** — username, password, 6-digit authenticator code. One run,
   one login; nothing is written to disk but your username.
2. **Reads the archive's filters off the page** — 26 academic years, 69
   countries, 632 host institutions, 38 agreement types, plus VŠE's own
   faculty / degree / programme tree. No hardcoded lists: InSIS ships the
   option values *and* the rules for how they narrow each other inside the
   HTML.
3. **Lets you pick many values per filter**, which the real form cannot do.
4. **Plans the search and shows you the plan** before sending anything. Three
   years × two institutions is six POSTs, each as slow as the last.
5. **Runs it, merges and de-duplicates** the result rows.
6. **Downloads the reports you tick** — markdown + JSON + PDF, resumably.

## What it allows you to do

- **Search several years, countries or universities at once.** The form is
  single-value. Six combinations is six searches; the app does them and merges
  the rows, de-duplicated on InSIS's own report id.
- **Search the *whole* archive, not just your own programme.** InSIS
  pre-selects the logged-in student's faculty, degree, form and programme.
  Left alone, "all of 2024/2025" quietly means "my programme in 2024/2025" and
  returns a short, plausible, **wrong** list. Every search here zeroes those
  fields unless you chose otherwise.
- **`grep` a hundred reports.** Each one becomes markdown with its sections and
  questions kept verbatim, so `grep -ril ubytování data/reports` answers "what
  did people say about housing" in a line.
- **Count what was asked and not answered.** Unanswered questions are dropped
  from the markdown — noise, in something meant to be read — but kept in the
  JSON. A report can also be filed completely blank; those stay visible rather
  than silently vanishing.
- **Keep InSIS's own PDFs**, toggled with `p`. The same document the print icon
  gives you, byte for byte.
- **Stop and resume.** A 170-report run is 170 requests to a university server.
  Already-downloaded reports are ticked on screen and skipped by `d`.
- **Re-read a search without logging in**, with `--offline`.
- **Filter without paying for extra requests.** Institution, country, agreement
  and state are columns in the result table, so they are applied to the merged
  rows rather than searched for. Twenty-six universities across four years is
  4 requests this way and 104 the other, for identical results.

### What it deliberately does not do

- **Run unattended.** Your password is prompted for every run and lives in
  memory only. There is nowhere to cache the 2FA code, and that is the point.
- **Drive a browser.** InSIS needs no JavaScript for any of this, so it is
  `requests` + `beautifulsoup4` (~30 MB) rather than Playwright (~400 MB).
- **Translate anything.** Country names, universities and period labels appear
  exactly as InSIS writes them, so what is on screen can be compared with the
  real page.
- **Hammer the server.** Reports are fetched one at a time with a pause between
  them, and what has been read is cached rather than re-fetched.

---

## Requirements

- **Python 3.11 or newer**
- A **VŠE InSIS account** with two-factor authentication set up — the archive is
  behind the login, and this tool has no way in that you do not
- A terminal that handles ANSI escapes. Windows Terminal, PowerShell 7, macOS
  Terminal and any Linux terminal are fine; the old `cmd.exe` console is not.

## Install and run

Both routes do the same thing. Pick one.

### With uv (recommended)

[uv](https://docs.astral.sh/uv/) creates the virtual environment and installs
the pinned dependencies from `uv.lock` for you.

```bash
git clone <this-repo> insis-erasmus
cd insis-erasmus

uv sync          # creates .venv and installs dependencies
uv run main.py   # log in, pick filters, download reports
```

`uv run` activates nothing — it runs the command inside the project's
environment. There is no `activate` step.

### With pip and a virtual environment

**Windows (PowerShell):**

```powershell
git clone <this-repo> insis-erasmus
cd insis-erasmus

python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt

python main.py
```

**macOS / Linux:**

```bash
git clone <this-repo> insis-erasmus
cd insis-erasmus

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

python main.py
```

Leave the environment afterwards with `deactivate`.

### Everything you can run

| With uv | In an activated venv | What it does |
|---|---|---|
| `uv run main.py` | `python main.py` | The app: log in, search, download |
| `uv run main.py --offline` | `python main.py --offline` | Browse the last search's rows, no login |
| `uv run pytest` | `pytest` | Parser tests, on synthetic fixtures |

On the pip route the tests need the dev dependencies:
`pip install -r requirements-dev.txt`.

---

## Using it

Four screens, in the order you move through them.

```
filters    ↑↓ move   Enter open a row   c clear it
           r run the search   l load last results   q quit

options    type to filter   ↑↓ move   Enter toggle
           Esc clear the box, or go back   ← → jump 10

results    ↑↓ move   Space toggle   a all   n none   p PDFs on/off
           d download selected   Esc back to filters   q quit

progress   what is happening, while it happens
```

**The option lists filter as you type** — no search key, no Enter to apply —
and ignore diacritics, so `nemecko` finds `Německo`. Typing is why *Enter*
toggles on that screen while *Space* toggles on the results screen: half the
institutions are "Something University of Somewhere", and a search box that
swallows the space bar is a search box for one word.

**The lists narrow each other, the way the real form does.** Pick Spain and the
institution list becomes the 30 Spanish ones; pick a faculty and the degrees
become that faculty's; faculty + degree + form unlock the programme list. InSIS
ships that dependency data inside the page, so it costs no extra request. If a
pick stops being offered — you swap Spain for Germany with a Spanish university
still selected — it is dropped and said out loud, rather than left as an
invisible filter that returns nothing for no visible reason.

**You must pick at least one academic year.** With no year, InSIS is asked for
every exchange VŠE has ever recorded and never answers — observed to read-time
out at three minutes. The app refuses that search rather than attempting it.

**Rows with no report filed are dimmed** and cannot be selected; a stay that has
not finished yet cannot have one. Already-downloaded rows are ticked, and `d`
skips them.

## What you get

```
data/
  stays.json                          every row the last search returned
  reports/<school>-<year>-<id>.md     what you read: sections, questions, answers
  reports/<school>-<year>-<id>.json   the same, structured, for grep and filters
  pdf/<school>-<year>-<id>.pdf        InSIS's printable copy, untouched
```

A report is a 14-section questionnaire of roughly 120 questions — accommodation,
courses, costs, the city, the coordinator — of which a thorough student answers
about 96. The markdown is the point: a directory of PDFs is still a directory
you open one at a time, which is the problem this exists to solve.

```bash
grep -ril "ubytování" data/reports          # who wrote about housing
grep -ril "kolej" data/reports/*Aalto*      # ...at one school
```

## Privacy, and what is not stored

**Your password, ever.** It is prompted for on each run and lives in memory for
that run only. Remembered between runs: your username and your last filter
selection, in `insis_erasmus_state.json`.

**`data/`, `reports/` and `captures/` hold other students' words**, and captures
additionally carry your name and study id. All are gitignored. Keep it that
way, and read any file before sharing it.

This reads a university system with your own credentials, at a human-ish rate,
for pages you are already entitled to open. Treat it accordingly.


### Layout

```
main.py         entry point
insis/
  app.py        the full-screen UI and its four screens
  client.py     HTTP session, login (incl. 2FA), search, report, PDF
  parse.py      HTML -> filters, result rows, reports
  search.py     multi-select -> one search per combination, merged
  store.py      markdown / JSON / PDF on disk, and resumability
  models.py     Filter, Stay, Report
  screen.py     full-screen rendering
  keys.py       single-keypress input
tests/          parser tests
```

## License

[MIT](LICENSE). Not affiliated with, or endorsed by, VŠE or InSIS.
