"""
Talking to InSIS over plain HTTP.

The login is lifted from the enrolment watchdog (insis_picker): it is the
expensive part to rediscover and it has not changed. InSIS is server-rendered
Perl with ordinary <form> markup and no JavaScript worth running, so requests
plus an HTML parser is enough.

What InSIS does that costs time to relearn:

  * A **404 arrives as HTTP 200** with a page titled 'Stránka nenalezena',
    which otherwise parses to zero rows and reads as "nothing here" rather
    than "wrong address".
  * **Not being logged in arrives as HTTP 403 with a login body**, with no
    redirect. Detect it by the title or by `credential_0`. The session cookie
    is session-scoped: it does not survive the process that owns it.
  * **Login is one form, posted twice.** The 2FA step is not a separate form:
    it is the same form with `auth_2fa_type` flipped to `totp` and a visible
    `credential_k` box added. The username comes back empty and the password
    is not echoed, so both must be resent alongside the code.
  * Query parameters are separated by **';' not '&'**.
  * Relative links must be resolved against **the page's own URL**, never the
    site root: '../int/foo.pl' joined onto 'https://insis.vse.cz/' climbs one
    level too far and silently drops '/auth/', so every link 404s.
"""

import re
from urllib.parse import urljoin

import requests

from . import parse

BASE = "https://insis.vse.cz/"
LOGIN_ACTION = "/system/login.pl"

# 'Zprávy ze zahraničních pobytů'. One script, two tabs, both as a query
# parameter on it; a report is 'zobrazit=<id>' and its PDF 'tisk=<id>'.
ZAVZPR = "/auth/int/zavzpr.pl"
STUDY = 1       # Studijní pobyty
PLACEMENT = 2   # Pracovní stáže

LOGIN_TITLE = "Přihlášení do systému"
NOT_FOUND_TITLE = "Stránka nenalezena"

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux aarch64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0 Safari/537.36"
)


class InsisError(Exception):
    """Anything that went wrong talking to InSIS."""


class LoginFailed(InsisError):
    """Credentials or the 2FA code were rejected."""


class SessionExpired(InsisError):
    """We were logged in and no longer are."""


class Timeout(InsisError):
    """
    InSIS took too long.

    Worth its own type because it means something specific and recoverable:
    the *query* was too big, not the connection broken. A search with every
    filter set to 'unrestricted' - all years, all programmes - does not come
    back. The session is still good, so the right response is to narrow the
    search and try again, not to log in afresh.
    """


def _is_html(r: requests.Response) -> bool:
    """
    Does this response carry a page, or a file?

    InSIS does not always say: some handlers answer with no Content-Type at
    all. A PDF is unmistakable from its first bytes, so fall back to those
    rather than assuming HTML and decoding a binary.
    """
    ctype = (r.headers.get("Content-Type") or "").lower()
    if ctype:
        return "html" in ctype or ctype.startswith("text/")
    return not r.content[:5].startswith(b"%PDF")


def looks_like_login(html: str) -> bool:
    if LOGIN_TITLE in parse.page_title(html):
        return True
    return 'name="credential_0"' in html and 'action="/system/login.pl"' in html


class Insis:
    # A page fetch is quick. A search is InSIS running a query across every
    # exchange the university has ever recorded, and takes as long as it takes.
    TIMEOUT = 30
    SEARCH_TIMEOUT = 180

    def __init__(self, base: str = BASE, timeout: int = TIMEOUT):
        self.base = base
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": USER_AGENT,
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
        })
        self.logged_in = False

    # -- low level ---------------------------------------------------------

    def _request(self, method: str, url: str, timeout: int | None = None,
                 **kwargs) -> requests.Response:
        """
        One HTTP call, with network failures turned into InsisError.

        A raw ReadTimeout traceback says 'ssl.py line 1166' and buries the one
        fact that matters: the session is still valid and the search was too
        broad. Callers can act on that; they cannot act on a stack trace.
        """
        try:
            return self.session.request(
                method, urljoin(self.base, url),
                timeout=timeout or self.timeout, **kwargs)
        except requests.exceptions.Timeout:
            raise Timeout(
                f"InSIS did not answer within {timeout or self.timeout}s. "
                "The session is still good - narrow the search and retry.")
        except requests.exceptions.RequestException as e:
            raise InsisError(f"could not reach InSIS: {e}")

    def _get(self, url: str, params: dict | None = None,
             timeout: int | None = None) -> requests.Response:
        return self._request("GET", url, timeout, params=params)

    def _post(self, action: str, data: dict,
              timeout: int | None = None) -> requests.Response:
        return self._request("POST", action, timeout, data=data)

    def fetch(self, url: str, params: dict | None = None,
              allow_login_page: bool = False) -> tuple[str, str]:
        """
        Fetch a page as (html, final_url).

        The final URL matters: it is what relative links on that page must be
        resolved against, and InSIS redirects often enough that the URL asked
        for is not always the URL landed on.
        """
        r = self._get(url, params)
        # Not every InSIS URL serves HTML: 'tisk=<id>' answers with a PDF.
        # Decoding those bytes as text yields 200 KB of mojibake that looks
        # like a page and parses to nothing, so refuse rather than guess.
        if not _is_html(r):
            raise InsisError(
                f"{r.url} returned {r.headers.get('Content-Type', 'unknown')}, "
                "not a page - use fetch_bytes() for documents")
        if looks_like_login(r.text) and not allow_login_page:
            self.logged_in = False
            raise SessionExpired(
                "InSIS returned the login page - the session is gone")
        # InSIS answers a bad URL with a normal-looking 200 page. Say which
        # it is, rather than handing back something that parses to nothing.
        if NOT_FOUND_TITLE in parse.page_title(r.text):
            raise InsisError(f"InSIS says 'Stránka nenalezena' for {r.url}")
        return r.text, r.url

    def fetch_bytes(self, url: str, timeout: int | None = None
                    ) -> tuple[bytes, str]:
        """
        Fetch a document as (bytes, content-type). The PDF behind 'tisk='.

        An expired session does not fail here - it succeeds, with an HTML
        login page in place of the document. Check for that explicitly, or a
        run of 'downloads' ends up as a directory of login screens named .pdf.
        """
        r = self._request("GET", url, timeout)
        ctype = r.headers.get("Content-Type", "")
        if _is_html(r):
            if looks_like_login(r.text):
                self.logged_in = False
                raise SessionExpired(
                    "InSIS returned the login page - the session is gone")
            raise InsisError(f"expected a document at {r.url}, got a page")
        return r.content, ctype

    def get_html(self, url: str, params: dict | None = None) -> str:
        return self.fetch(url, params)[0]

    def submit(self, html: str, page_url: str, data: dict,
               needle: str = "", timeout: int | None = None) -> tuple[str, str]:
        """
        Re-submit a form found on `html`, with `data` merged over its fields.

        Every hidden field is carried forward verbatim: InSIS threads a lot of
        state through them and dropping any of it silently changes what the
        request means. A GET form is sent as a query string, because that is
        what a browser would do.

        Defaults to the long timeout: a form submit here is a search.
        """
        timeout = timeout or self.SEARCH_TIMEOUT
        method, action, fields = parse.parse_form(html, needle=needle)
        fields.update(data)
        target = urljoin(page_url, action) if action else page_url
        r = (self._post(target, fields, timeout) if method == "post"
             else self._get(target, fields, timeout))
        if looks_like_login(r.text):
            self.logged_in = False
            raise SessionExpired("InSIS returned the login page")
        if NOT_FOUND_TITLE in parse.page_title(r.text):
            raise InsisError(f"InSIS says 'Stránka nenalezena' for {r.url}")
        return r.text, r.url

    # -- the report archive ------------------------------------------------

    def open_archive(self, akce: int = STUDY) -> tuple[str, str]:
        """
        The search page for one tab: akce=1 study stays, akce=2 work placements.

        Returns (html, url). Hold on to both: every search is this page's form
        posted back, and the URL is what its relative links resolve against.
        """
        return self.fetch(f"{ZAVZPR}?akce={akce}")

    def search(self, index_html: str, index_url: str, filters: dict[str, str],
               timeout: int | None = None) -> tuple[str, str]:
        """
        Run one search. `filters` is {select name: option value}.

        **Submit the pristine index page, not a result page.** A result page
        carries the same form with the previous search still selected in it,
        so re-submitting that inherits filters nobody asked for - and because
        every field has a plausible-looking value, the wrong result set looks
        entirely correct.

        Fields left out keep whatever InSIS pre-selected, which is *not*
        neutral: it pre-fills the logged-in student's own faculty, programme
        and the current year. Pass '0' explicitly to mean "all".
        """
        fields = dict(filters)
        for name, value in parse.submit_button(index_html).items():
            # InSIS's filter form does nothing without its own button: it
            # answers with the empty search page, which reads as "no matches".
            fields.setdefault(name, value)
        return self.submit(index_html, index_url, fields, timeout=timeout)

    def report(self, report_id: str, akce: int = STUDY) -> str:
        """The on-screen report behind a row's first icon."""
        return self.get_html(f"{ZAVZPR}?akce={akce};zobrazit={report_id}")

    def report_pdf(self, report_id: str, akce: int = STUDY) -> bytes:
        """The PDF behind a row's second icon."""
        data, ctype = self.fetch_bytes(f"{ZAVZPR}?akce={akce};tisk={report_id}")
        if not data.startswith(b"%PDF"):
            raise InsisError(
                f"report {report_id}: expected a PDF, got {ctype or 'nothing'}")
        return data

    # -- login -------------------------------------------------------------

    def login(self, username: str, password: str, get_code=None,
              destination: str | None = None) -> None:
        """
        Log in, handling the second-step 6-digit code.

        `get_code` is called only if InSIS asks for one, so accounts without
        2FA never see a prompt. It should return the code as a string.

        The whole login form is round-tripped rather than posted field by
        field: InSIS threads state through hidden inputs (destination,
        auth_id_hidden, auth_2fa_type) and dropping any of them changes what
        the POST means.
        """
        target = destination or "/auth/student/moje_studium.pl"
        r = self._get(target)
        html = r.text
        if not looks_like_login(html):
            self.logged_in = True
            return

        method, action, fields = parse.parse_form(html, needle="credential_0")
        fields["credential_0"] = username
        fields["credential_1"] = password
        # The submit button's own name/value is part of a normal browser POST
        # and some InSIS handlers branch on it.
        fields.setdefault("login", "Přihlásit se")

        r = self._post(action or LOGIN_ACTION, fields)
        html = r.text

        # Still on a login form? Either the password was wrong, or this is the
        # 2FA step. Presence of an empty code-ish field distinguishes them.
        if looks_like_login(html) or self._wants_code(html):
            html = self._handle_second_step(html, get_code, username, password)

        if looks_like_login(html):
            raise LoginFailed("InSIS still shows the login page - "
                              "wrong username or password?")

        # Prove it rather than trust the redirect we happened to land on.
        check = self._get(target)
        if looks_like_login(check.text):
            raise LoginFailed("login appeared to work but the session is not valid")

        self.logged_in = True

    def _wants_code(self, html: str) -> bool:
        low = html.lower()
        if "ověřovací kód" in low or "verification code" in low:
            return True
        return bool(re.search(r'name="[^"]*(2fa|otp|totp)[^"]*"', low))

    def _handle_second_step(self, html: str, get_code,
                            username: str, password: str) -> str:
        """
        Fill in and submit the 6-digit code page.

        Not a separate form: the same login form re-posted with the code
        added. Posting the parsed page back unchanged would submit an empty
        username, because InSIS does not echo it.
        """
        try:
            method, action, fields = parse.parse_form(html)
        except ValueError:
            raise LoginFailed("expected a second login step but found no form")

        fields["credential_0"] = username
        fields["credential_1"] = password

        field = parse.find_code_field(html)
        if field is None:
            # Fail loudly with the field names, rather than guessing and
            # posting the code into some arbitrary input.
            raise LoginFailed(
                "could not find the 2FA code field. Fields on the page were: "
                + ", ".join(sorted(fields))
                + " -- report these so the client can be taught this form."
            )

        if get_code is None:
            raise LoginFailed("InSIS wants a 2FA code but no prompt was given")

        code = str(get_code()).strip()
        if not code:
            raise LoginFailed("no 2FA code entered")

        fields[field] = code
        fields.setdefault("login", "Přihlásit se")
        return self._post(action or LOGIN_ACTION, fields).text
