"""
Where downloaded reports live, and what they turn into on disk.

Three forms of the same report, because they answer different questions:

    reports/<id>.md     what you actually read - sections, questions, answers
    reports/<id>.json   the same thing structured, for grepping and filtering
    pdf/<id>.pdf        InSIS's own printable copy, untouched

The markdown is the point. A directory of PDFs is a directory you have to open
one at a time, which is the problem this tool exists to solve; a directory of
markdown is one `grep -ril "ubytování"` away from an answer.

Downloads are resumable. Each report is a request against a university server,
so re-fetching a hundred already-saved ones to add the hundred-and-first is
rude as well as slow.
"""

import json
import re
from dataclasses import asdict
from pathlib import Path

from .models import Report, Stay

ROOT = Path(__file__).resolve().parent.parent / "data"


def _safe(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "_", name) or "unnamed"


class Store:
    def __init__(self, root: Path = ROOT):
        self.root = root
        self.reports = root / "reports"
        self.pdfs = root / "pdf"

    def prepare(self) -> None:
        self.reports.mkdir(parents=True, exist_ok=True)
        self.pdfs.mkdir(parents=True, exist_ok=True)

    # -- naming ------------------------------------------------------------

    def stem(self, stay: Stay) -> str:
        """
        A filename that says what the report is about.

        The id alone is unreadable; the institution alone is not unique. Both,
        so that `ls` is already a useful listing and two stays at the same
        school in the same year cannot overwrite each other.
        """
        year = (stay.start or "")[-4:]
        return _safe(f"{stay.institution[:60]}-{year}-{stay.report_id}").strip("_")

    def json_path(self, stay: Stay) -> Path:
        return self.reports / f"{self.stem(stay)}.json"

    def md_path(self, stay: Stay) -> Path:
        return self.reports / f"{self.stem(stay)}.md"

    def pdf_path(self, stay: Stay) -> Path:
        return self.pdfs / f"{self.stem(stay)}.pdf"

    def has(self, stay: Stay, want_pdf: bool) -> bool:
        """Already downloaded? Used to skip, so a run can be resumed."""
        done = self.json_path(stay).exists()
        return done and (self.pdf_path(stay).exists() if want_pdf else True)

    # -- writing -----------------------------------------------------------

    def save_report(self, stay: Stay, report: Report) -> Path:
        self.prepare()
        data = asdict(report)
        data["stay"] = asdict(stay)
        self.json_path(stay).write_text(
            json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        path = self.md_path(stay)
        path.write_text(to_markdown(stay, report), encoding="utf-8")
        return path

    def save_pdf(self, stay: Stay, data: bytes) -> Path:
        self.prepare()
        path = self.pdf_path(stay)
        path.write_bytes(data)
        return path

    def save_index(self, stays: list[Stay]) -> Path:
        """
        The result rows themselves, so a search need not be repeated to see
        what it found.
        """
        self.prepare()
        path = self.root / "stays.json"
        path.write_text(
            json.dumps([asdict(s) for s in stays], indent=2, ensure_ascii=False),
            encoding="utf-8")
        return path

    def load_index(self) -> list[Stay]:
        path = self.root / "stays.json"
        if not path.exists():
            return []
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return []
        known = set(Stay.__dataclass_fields__)
        return [Stay(**{k: v for k, v in row.items() if k in known})
                for row in raw if isinstance(row, dict)]


def to_markdown(stay: Stay, report: Report) -> str:
    """
    A report as a document, questions kept verbatim.

    Unanswered questions are dropped *here* and only here - they are noise in
    something meant to be read, but they stay in the JSON, where "asked and
    not answered" is a fact worth being able to count.
    """
    out = [f"# {report.institution or stay.institution}", ""]

    facts = [
        ("Country", report.country or stay.country),
        ("Agreement", stay.agreement),
        ("Period", report.period or f"{stay.start} – {stay.end}"),
        ("Duration", report.duration),
        ("VŠE faculty", report.faculty),
        ("Coordinator", report.coordinator),
        ("InSIS id", report.report_id or stay.report_id),
    ]
    out += [f"- **{k}:** {v}" for k, v in facts if v]

    for section in report.sections:
        answered = section.answered()
        if not answered:
            continue
        out += ["", f"## {section.title}", ""]
        for qa in answered:
            out += [f"**{qa.question}**", "", qa.answer, ""]

    return "\n".join(out).rstrip() + "\n"
