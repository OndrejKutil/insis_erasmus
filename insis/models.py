"""
What the archive is made of.

Three things, in the order you meet them: the filters you search by, the rows
a search returns, and the report behind a row.

Everything InSIS wrote is kept as InSIS wrote it - Czech country names, Czech
question wording, dates as dd.mm.yyyy. Translating or reformatting here would
make what is on screen impossible to compare with the real page, and the
questions are the report: they are what a reader skims for.
"""

from dataclasses import dataclass, field

UNRESTRICTED = "0"      # every filter's "-- nezadáno --" / "-- neomezeno --"


@dataclass
class Option:
    """
    One entry in a filter dropdown. The value is an opaque InSIS id.

    `depends` is what makes the lists narrow each other, exactly as the real
    form does: a list of alternative conditions, each a {field: value} mapping
    that would make this option appear. An institution in Spain carries
    {'filtr_stat': '724'}; a programme carries its faculty, degree and form.
    Empty means the option is always offered.
    """

    value: str
    label: str
    depends: list[dict[str, str]] = field(default_factory=list)

    def offered(self, chosen: dict[str, list[str]]) -> bool:
        """
        Would InSIS show this option, given what is picked elsewhere?

        A parent with nothing picked constrains nothing - no country chosen
        means every institution, which is what the real form does too.
        """
        if not self.depends:
            return True
        for combo in self.depends:
            if all(not chosen.get(field_name)
                   or value in chosen[field_name]
                   for field_name, value in combo.items()):
                return True
        return False


@dataclass
class Filter:
    """
    One of the search form's dropdowns, and what the user picked in it.

    InSIS takes a single value per field, so `chosen` holding more than one is
    a request for several searches - see `plan()` in search.py. Empty means
    unrestricted.
    """

    name: str
    label: str
    options: list[Option] = field(default_factory=list)
    chosen: list[str] = field(default_factory=list)
    parents: list[str] = field(default_factory=list)

    def label_for(self, value: str) -> str:
        return next((o.label for o in self.options if o.value == value), value)

    def available(self, chosen: dict[str, list[str]]) -> list[Option]:
        """The options still on offer, given the other filters."""
        return [o for o in self.options if o.offered(chosen)]

    def prune(self, chosen: dict[str, list[str]]) -> list[str]:
        """
        Drop picks that the current parents no longer offer, and say which.

        Picking Spain after picking a German university would otherwise leave
        an invisible filter behind: the search would run for a combination the
        user can no longer see, and return nothing for no visible reason.
        """
        allowed = {o.value for o in self.available(chosen)}
        dropped = [v for v in self.chosen if v not in allowed]
        if dropped:
            self.chosen = [v for v in self.chosen if v in allowed]
        return dropped

    def summary(self) -> str:
        if not self.chosen:
            return "vše"
        if len(self.chosen) <= 2:
            return ", ".join(self.label_for(v) for v in self.chosen)
        return f"{len(self.chosen)} selected"


@dataclass
class Stay:
    """
    One row of a result table: a stay, and the report written about it.

    `report_id` is the id in both the 'zobrazit' and 'tisk' links - the same
    number for the page and the PDF. A row without one is a stay whose student
    never filed a report, which is the one thing that makes a row useless here.
    """

    institution: str = ""
    country: str = ""
    agreement: str = ""
    start: str = ""
    end: str = ""
    state: str = ""
    report_id: str = ""
    akce: int = 1

    @property
    def has_report(self) -> bool:
        return bool(self.report_id)

    def describe(self) -> str:
        when = f"{self.start}-{self.end}".strip("-")
        return f"{self.institution} ({self.country}) {when}".strip()


@dataclass
class QA:
    """One question from the form, and what the student answered."""

    question: str
    answer: str


@dataclass
class Section:
    """A numbered part of the questionnaire, e.g. '3. Ubytování'."""

    title: str
    items: list[QA] = field(default_factory=list)

    def answered(self) -> list[QA]:
        return [qa for qa in self.items if qa.answer]


@dataclass
class Report:
    """
    A full report: who and where, then the questionnaire.

    The header holds no student name - InSIS names only the home coordinator -
    so a report identifies a place and a semester, not a person.
    """

    report_id: str = ""
    akce: int = 1
    faculty: str = ""
    coordinator: str = ""
    institution: str = ""
    country: str = ""
    period: str = ""
    duration: str = ""
    sections: list[Section] = field(default_factory=list)

    @property
    def answers(self) -> int:
        return sum(len(s.answered()) for s in self.sections)

    def is_empty(self) -> bool:
        """
        A report can exist and say nothing.

        InSIS files a report for every stay whose student opened the form,
        including those who saved it blank. Such a page parses perfectly into
        a report with no answers, which is worth knowing before downloading a
        hundred of them.
        """
        return self.answers == 0
