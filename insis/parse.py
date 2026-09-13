"""
Turning InSIS HTML into data.

InSIS is server-rendered Perl with plain <table> markup and no useful ids or
classes on rows, so everything here works the same way: find a table by its
header cells, derive column indexes from that header, then read rows by text.
Never by position - column order is not contractual and has no reason to stay
stable between semesters.

Report-specific parsing (zavzpr.pl) lives further down, and was written
against a captured page rather than a memory of one.
"""

import json
import re
from urllib.parse import parse_qs, urljoin, urlsplit

from bs4 import BeautifulSoup

from .models import QA, Filter, Option, Report, Section, Stay

BASE = "https://insis.vse.cz/"

# The result table, identified by its header cells. 'Zpráva' is the column
# holding the link to the report itself, so a table without it is some other
# table that happens to mention institutions.
RESULTS_HEAD = {"Instituce", "Stát", "Zpráva"}

_STAY_COLUMNS = {
    "institution": "Instituce", "country": "Stát", "agreement": "Dohoda",
    "start": "Odkdy", "end": "Dokdy", "state": "Stav",
}

# The filters worth exposing, in the order they are useful to a reader, with
# names that say what they filter. InSIS's own labels sit in a separate table
# from the selects, so pairing them up by position would be fragile; these are
# short enough to be worth writing out. Everything else on that form narrows
# by the *writer's* study, which is rarely what you want when reading reviews.
FILTER_LABELS = {
    "filtr_obdobi": "Academic year",
    "filtr_stat": "Country",
    "filtr_instituce": "Host institution",
    "filtr_dohoda": "Agreement type",
    "filtr_stav": "Stay state",
    "fakulta": "VŠE faculty",
    "stupen": "Degree",
    "forma": "Study form",
    "program": "Programme",
    "podprogram1": "Sub-programme",
    "specializace1": "Specialisation",
    "specializace2": "Specialisation 2",
    "specializace3": "Specialisation 3",
}

# How the real form narrows one list by another. Both systems ship inside the
# page - there is no request to make, and no need to imitate the JS:
#
#   study side   each <select> declares data-dependency-aliases, e.g.
#                {"a":"fakulta","b":"stupen"}, and each <option> a
#                data-depends-on of '[[[a:40,b:1],[a:30,b:1]]]' - alternative
#                combinations of its parents' values.
#   institutions a script assigns instituce_pole_zavislosti['<country id>']
#                = {'<institution id>' : 1, ...}.
_DEPENDS_RE = re.compile(r"\[([a-z]:\d+(?:\s*,\s*[a-z]:\d+)*)\]")
_ALIAS_PAIR_RE = re.compile(r"([a-z]):(\d+)")
_INSTITUTIONS_BY_COUNTRY_RE = re.compile(
    r"instituce_pole_zavislosti\['(\d+)'\]\s*=\s*\{([^}]*)\}")
_ID_RE = re.compile(r"'(\d+)'")

COUNTRY = "filtr_stat"
INSTITUTION = "filtr_instituce"

# An empty result set is not an empty table: InSIS puts a sentence *inside*
# the table, in the first column, where an institution name would go. Parsed
# as a row it becomes a stay called 'Nenalezena žádná vyhovující data.' with
# no report - indistinguishable, on screen, from a real stay nobody wrote up.
NO_DATA = "Nenalezena žádná vyhovující data"

# A section heading in a report: a single-cell row reading '3. Ubytování'.
# The number is InSIS's, and the space after it is often non-breaking.
_SECTION_RE = re.compile(r"^\d+\.\s")


# -- text and table helpers --------------------------------------------------

def _text(node) -> str:
    return " ".join(node.get_text(" ", strip=True).split()) if node else ""


def _lines(node) -> list[str]:
    """
    Cell text split on <br>.

    InSIS routinely puts several entries in one cell separated by <br>.
    Flattening them into one string loses the boundary between them.
    """
    if node is None:
        return []
    out, cur = [], []
    for child in node.descendants:
        name = getattr(child, "name", None)
        if name == "br":
            out.append(" ".join("".join(cur).split()))
            cur = []
        elif name is None:
            cur.append(str(child))
    out.append(" ".join("".join(cur).split()))
    return [x for x in out if x]


def headers(table) -> list[str]:
    """The header cells of a table, read from its first row."""
    rows = table.find_all("tr")
    if not rows:
        return []
    return [_text(c) for c in rows[0].find_all(["th", "td"])]


def find_table(soup, needed: set[str]):
    """First table whose header row contains all of `needed`."""
    for table in soup.find_all("table"):
        if needed <= set(headers(table)):
            return table
    return None


def index_map(heads: list[str], wanted: dict[str, str]) -> dict[str, int]:
    """{'country': 'Země'} -> {'country': 2}. Missing columns are absent."""
    out = {}
    for key, label in wanted.items():
        if label in heads:
            out[key] = heads.index(label)
    return out


def cell(cells, idx: int | None) -> str:
    if idx is None or idx >= len(cells):
        return ""
    return _text(cells[idx])


def query_params(url: str) -> dict[str, str]:
    """
    Query parameters of an InSIS URL.

    InSIS separates them with ';' rather than '&', which parse_qs does not
    split on, so normalise first.
    """
    q = urlsplit(url).query.replace(";", "&")
    return {k: v[0] for k, v in parse_qs(q).items() if v}


# -- forms and links ---------------------------------------------------------

def parse_form(html: str, needle: str = "") -> tuple[str, str, dict[str, str]]:
    """
    Extract a form as (method, action, fields).

    Every hidden field is carried forward verbatim: InSIS threads a lot of
    state through them and dropping any of it silently changes what the
    request means.

    `needle` picks the form whose markup contains that string, for pages with
    more than one.
    """
    soup = BeautifulSoup(html, "html.parser")
    forms = soup.find_all("form")
    if needle:
        forms = [f for f in forms if needle in str(f)] or forms
    if not forms:
        raise ValueError("no form on page")

    form = forms[0]
    fields: dict[str, str] = {}
    for inp in form.find_all("input"):
        name = inp.get("name")
        if not name:
            continue
        itype = (inp.get("type") or "text").lower()
        if itype in ("submit", "button", "image"):
            continue
        if itype in ("checkbox", "radio"):
            # Only pre-checked boxes are part of the submission.
            if inp.has_attr("checked"):
                fields[name] = inp.get("value", "on")
            continue
        fields[name] = inp.get("value", "")

    for sel in form.find_all("select"):
        name = sel.get("name")
        if not name:
            continue
        opt = sel.find("option", selected=True) or sel.find("option")
        if opt is not None:
            fields[name] = opt.get("value", "")

    for area in form.find_all("textarea"):
        name = area.get("name")
        if name:
            fields[name] = area.get_text()

    return (
        (form.get("method") or "get").lower(),
        form.get("action") or "",
        fields,
    )


def submit_button(html: str, needle: str = "") -> dict[str, str]:
    """
    The clicked-button field a browser would add to the POST.

    `parse_form` drops submit inputs, because most of the time they are noise.
    On zavzpr.pl they are not: the filter form does nothing without
    `omezit=Zobrazit`, and InSIS answers with the empty search page again -
    which reads as "no reports match" rather than "you never pressed the
    button".
    """
    soup = BeautifulSoup(html, "html.parser")
    forms = soup.find_all("form")
    if needle:
        forms = [f for f in forms if needle in str(f)] or forms
    for form in forms:
        for inp in form.find_all("input", attrs={"type": "submit"}):
            if inp.get("name"):
                return {inp["name"]: inp.get("value", "")}
    return {}


# Fields that carry login state and must never be mistaken for the code box.
# 'auth_2fa_type' is the trap: its name contains '2fa' but it is a hidden
# selector, and writing the code into it breaks the POST.
_NOT_CODE = {"auth_2fa_type", "auth_id_hidden", "login_hidden", "destination",
             "lang", "credential_0", "credential_1", "credential_cookie"}


def find_code_field(html: str) -> str | None:
    """
    Name of the input on the second login page that wants the 6-digit code.

    Restricted to inputs that are *visibly fillable and currently empty* - a
    hidden field can never be the box the user types into. Among those, an
    obviously code-ish name wins, then InSIS's own 'credential_k'.
    """
    soup = BeautifulSoup(html, "html.parser")
    candidates: list[str] = []
    for form in soup.find_all("form"):
        for inp in form.find_all("input"):
            name = inp.get("name")
            itype = (inp.get("type") or "text").lower()
            if not name or name in _NOT_CODE:
                continue
            if itype not in ("text", "password", "tel", "number"):
                continue
            if inp.get("value"):
                continue
            candidates.append(name)

    if not candidates:
        return None
    for pattern in (r"2fa", r"otp", r"totp", r"code", r"kod", r"token", r"ověř"):
        for name in candidates:
            if re.search(pattern, name, re.I):
                return name
    if "credential_k" in candidates:
        return "credential_k"
    # Exactly one fillable empty box on a page asking for a code is unambiguous.
    return candidates[0] if len(candidates) == 1 else None


def page_title(html: str) -> str:
    m = re.search(r"<title>(.*?)</title>", html, re.S | re.I)
    return " ".join(m.group(1).split()) if m else ""


# -- the report archive ------------------------------------------------------

def _aliases(sel) -> dict[str, str]:
    """{'a': 'fakulta', 'b': 'stupen'} - which parent each alias letter means."""
    raw = sel.get("data-dependency-aliases")
    if not raw:
        return {}
    try:
        return {k: v for k, v in json.loads(raw).items()}
    except (ValueError, AttributeError):
        return {}


def _depends_on(opt, aliases: dict[str, str]) -> list[dict[str, str]]:
    """
    '[[[a:40,b:1,c:1],[a:30,b:1,c:1]]]' -> two alternative parent combinations.

    Alternatives are an OR and the pairs inside one are an AND, which is why
    this is a list of dicts rather than one dict: a programme taught at two
    faculties is offered if *either* is chosen.
    """
    raw = opt.get("data-depends-on")
    if not raw or not aliases:
        return []
    combos = []
    for group in _DEPENDS_RE.findall(raw):
        combo = {aliases[a]: v for a, v in _ALIAS_PAIR_RE.findall(group)
                 if a in aliases}
        if combo:
            combos.append(combo)
    return combos


def institutions_by_country(html: str) -> dict[str, set[str]]:
    """
    Which institutions belong to which country, from the page's own script.

    The map is assigned one country at a time, after an initial empty version
    of itself - so a parser that reads only the first assignment sees 69
    countries with no institutions each, and concludes every list is empty.
    Read the per-country assignments instead.
    """
    out: dict[str, set[str]] = {}
    for country, body in _INSTITUTIONS_BY_COUNTRY_RE.findall(html):
        if country == "0":      # the 'no country' entry: every institution
            continue
        ids = {i for i in _ID_RE.findall(body) if i != "0"}
        if ids:
            out.setdefault(country, set()).update(ids)
    return out


def parse_filters(html: str) -> list[Filter]:
    """
    The search form's dropdowns: what you can filter by, and the ids to do it
    with.

    Read off the page every run rather than shipped as a table. There are 632
    institutions and the ids are opaque (381 = 2025/2026, 40 = FIS); a
    hardcoded copy would rot silently, and the failure would be a search for
    the wrong year that looks entirely plausible.

    Only the fields in FILTER_LABELS are returned, in that order.
    """
    soup = BeautifulSoup(html, "html.parser")
    found: dict[str, Filter] = {}
    by_country = institutions_by_country(html)

    for sel in soup.find_all("select"):
        name = sel.get("name")
        if name not in FILTER_LABELS or name in found:
            continue
        aliases = _aliases(sel)
        options = []
        for opt in sel.find_all("option"):
            value = opt.get("value", "")
            label = _text(opt)
            # '-- nezadáno --' is InSIS's way of spelling 'no filter'. It is
            # the default, not a choice, and listing it invites picking it
            # alongside real values.
            if value == "0" or label.startswith("--"):
                continue
            depends = _depends_on(opt, aliases)
            if name == INSTITUTION:
                # The institution list is narrowed by a separate mechanism,
                # so its options carry no attributes; invert the country map
                # to give them the same shape as everything else.
                depends = [{COUNTRY: country}
                           for country, ids in by_country.items()
                           if value in ids]
            options.append(Option(value=value, label=label, depends=depends))
        if options:
            found[name] = Filter(name=name, label=FILTER_LABELS[name],
                                 options=options,
                                 parents=sorted(set(aliases.values())))

    if INSTITUTION in found:
        found[INSTITUTION].parents = [COUNTRY]

    return [found[name] for name in FILTER_LABELS if name in found]


def parse_results(html: str, base: str = BASE, akce: int = 1) -> list[Stay]:
    """
    The rows a search returned.

    The 'Zpráva' and 'Tisk' cells hold **icons, not text**: the anchors are
    empty and reading the cell gives ''. The report id lives only in the
    hrefs, as `zobrazit=<id>` and `tisk=<id>` - the same id for the page and
    the PDF - so this reads links, not text, for those two columns.
    """
    soup = BeautifulSoup(html, "html.parser")
    table = find_table(soup, RESULTS_HEAD)
    if not table:
        return []

    heads = headers(table)
    ix = index_map(heads, _STAY_COLUMNS)

    stays: list[Stay] = []
    for row in table.find_all("tr")[1:]:
        cells = row.find_all("td")
        if not cells:
            continue
        institution = cell(cells, ix.get("institution"))
        if not institution or NO_DATA in institution:
            continue

        # A real row says where, which country and when. InSIS's own notices
        # fill one cell and leave the rest blank, so requiring corroboration
        # drops them without having to know how each one is worded.
        if sum(1 for key in _STAY_COLUMNS if cell(cells, ix.get(key))) < 3:
            continue

        report_id = ""
        for a in row.find_all("a", href=True):
            params = query_params(urljoin(base, a["href"]))
            report_id = params.get("zobrazit") or params.get("tisk") or ""
            if report_id:
                break

        stays.append(Stay(
            institution=institution,
            country=cell(cells, ix.get("country")),
            agreement=cell(cells, ix.get("agreement")),
            start=cell(cells, ix.get("start")),
            end=cell(cells, ix.get("end")),
            state=cell(cells, ix.get("state")),
            report_id=report_id,
            akce=akce,
        ))
    return stays


def _report_table(soup):
    """
    The questionnaire, which is the table with the most section headings.

    A report page carries the whole search form as well, and that form's
    layout table has plenty of rows too. Counting '3. Ubytování'-shaped
    heading rows tells them apart without depending on any one heading's
    wording.
    """
    best, best_score = None, 0
    for table in soup.find_all("table"):
        score = sum(
            1 for row in table.find_all("tr")
            if len(row.find_all(["td", "th"])) == 1
            and _SECTION_RE.match(_text(row.find(["td", "th"])))
        )
        if score > best_score:
            best, best_score = table, score
    return best


def _report_header(soup) -> dict[str, str]:
    """
    Where and when: the small table above the questionnaire.

    Its rows are a mix - 'Fakulta:' / value pairs, bare subheadings
    ('Hostitelská instituce'), and two sentences carrying the dates. Read by
    label, never by position.
    """
    out = {"period": "", "duration": ""}
    for table in soup.find_all("table"):
        heads = headers(table)
        if not heads or "Domácí univerzita" not in heads[0]:
            continue
        for row in table.find_all("tr"):
            cells = row.find_all(["td", "th"])
            if len(cells) == 2:
                label = _text(cells[0]).rstrip(":")
                value = _text(cells[1])
                if label and value:
                    out[label] = value
            elif len(cells) == 1:
                text = _text(cells[0])
                if text.startswith("Pobyt od"):
                    out["period"] = text
                elif text.startswith("Doba pobytu"):
                    out["duration"] = text
        break
    return out


def parse_report(html: str, report_id: str = "", akce: int = 1) -> Report:
    """
    One report: the header, then the questionnaire as sections of Q&A.

    The questionnaire is a flat table pretending to be nested. Three kinds of
    row, told apart by cell count:

      * one cell, '\\d+. Title'  -> a new section starts
      * one cell, empty          -> a spacer between questions, ignored
      * two cells                -> a question and its answer

    Unanswered questions are kept with an empty answer rather than dropped:
    'this student did not say' is information, and dropping them would make
    two reports with different content look identically complete.
    """
    soup = BeautifulSoup(html, "html.parser")
    head = _report_header(soup)

    report = Report(
        report_id=report_id,
        akce=akce,
        faculty=head.get("Fakulta", ""),
        coordinator=head.get("Koordinátor", ""),
        institution=head.get("Název instituce", ""),
        country=head.get("Stát", ""),
        period=head.get("period", ""),
        duration=head.get("duration", ""),
    )

    table = _report_table(soup)
    if table is None:
        return report

    section = None
    for row in table.find_all("tr"):
        cells = row.find_all(["td", "th"])
        if len(cells) == 1:
            title = _text(cells[0])
            if _SECTION_RE.match(title):
                section = Section(title=title)
                report.sections.append(section)
            continue
        if len(cells) < 2:
            continue
        question = _text(cells[0])
        if not question:
            continue
        if section is None:
            # A questionnaire that starts with questions before any heading.
            # Not observed, but a report is worth having without one.
            section = Section(title="")
            report.sections.append(section)
        section.items.append(QA(question=question, answer=_text(cells[1])))

    return report
