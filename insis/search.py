"""
Turning a multi-select into the several searches InSIS actually accepts.

The form takes one value per field. Asking for three years and two countries
is therefore six POSTs, each as slow as the last, and their results merged. So
the plan is computed up front and shown before anything is sent: six searches
is a minute of waiting, and the user should see that coming rather than watch
a progress bar they did not agree to.
"""

from itertools import product

from .models import UNRESTRICTED, Filter, Stay

# Fields InSIS pre-fills with the logged-in student's own study. They are not
# neutral and they are not all on screen, so every search zeroes them unless
# the user picked something: otherwise a search for 'all of 2024/2025' quietly
# means 'my programme in 2024/2025' and returns a plausible, wrong, short list.
NEUTRALISE = ("fakulta", "stupen", "forma", "program", "podprogram1",
              "specializace1", "specializace2", "specializace3")

# Without a period, InSIS is asked for every exchange VŠE has ever recorded
# and does not answer. One year at a time is the unit that comes back.
PERIOD = "filtr_obdobi"

# Filters whose value is *also a column in the result table*, and can
# therefore be applied to the rows afterwards instead of being searched for.
# Verified against a capture: all 170 rows' institution, country, agreement
# and state matched a filter option label exactly.
#
# This is what keeps a multi-select usable. Twenty-six universities across
# four years is 104 POSTs if the institution is part of the query, and four if
# it is not - for identical results, because the year is the only one of those
# axes InSIS does not print in the table.
LOCAL = {
    "filtr_instituce": "institution",
    "filtr_stat": "country",
    "filtr_dohoda": "agreement",
    "filtr_stav": "state",
}


def plan(filters: list[Filter]) -> list[dict[str, str]]:
    """
    Every combination of the chosen values, as one dict of form fields each.

    A field with nothing chosen contributes '0' - unrestricted - rather than
    being left out, because leaving it out means "keep InSIS's default" and
    InSIS's default is the current student's own study.
    """
    axes: list[list[tuple[str, str]]] = []
    base = {name: UNRESTRICTED for name in NEUTRALISE}

    for f in filters:
        if f.name in LOCAL:
            # Searched for by reading the results, not by asking. Still sent
            # as '0', because leaving a field out means "keep InSIS's default"
            # and its default for the stay state is not "all".
            base[f.name] = UNRESTRICTED
            continue
        values = f.chosen or [UNRESTRICTED]
        axes.append([(f.name, v) for v in values])

    return [dict(base, **dict(combo)) for combo in product(*axes)] if axes \
        else [dict(base)]


def apply_local(stays: list[Stay], filters: list[Filter]) -> list[Stay]:
    """
    Narrow merged results by the filters that were not part of the query.

    Matched on the label InSIS itself printed, which is the same string in the
    dropdown and in the table - checked, not assumed. A filter with nothing
    chosen narrows nothing.
    """
    for f in filters:
        attribute = LOCAL.get(f.name)
        if not attribute or not f.chosen:
            continue
        wanted = {f.label_for(v) for v in f.chosen}
        stays = [s for s in stays if getattr(s, attribute, "") in wanted]
    return stays


def describe(combo: dict[str, str], filters: list[Filter]) -> str:
    """One search, in the words the user picked it with."""
    by_name = {f.name: f for f in filters}
    parts = []
    for name, value in combo.items():
        if value == UNRESTRICTED or name not in by_name:
            continue
        parts.append(by_name[name].label_for(value))
    return " · ".join(parts) or "everything"


def needs_period(filters: list[Filter]) -> bool:
    """
    True if this plan would ask InSIS for every year at once.

    Worth refusing rather than attempting: it read-times out after three
    minutes, and the failure arrives long after the user has stopped watching.
    """
    period = next((f for f in filters if f.name == PERIOD), None)
    return period is None or not period.chosen


def merge(batches: list[list[Stay]]) -> list[Stay]:
    """
    Results from several searches, de-duplicated.

    Overlap is normal, not exceptional: 'Aalto' in 2024/2025 and 'Finland' in
    2024/2025 return the same stays. De-duplicate on the report id, which is
    InSIS's own identity for the thing being read.

    Rows without a report keep their institution and dates as their identity,
    so two genuinely different stays are not collapsed into one.
    """
    seen: dict[tuple, Stay] = {}
    for batch in batches:
        for stay in batch:
            key = ((stay.akce, stay.report_id) if stay.report_id
                   else (stay.institution, stay.start, stay.end))
            seen.setdefault(key, stay)
    return list(seen.values())
