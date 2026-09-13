"""
Parser tests, on fixtures small enough to read.

The fixtures are hand-written rather than captured: a real page carries other
students' reports and a study id, and none of that belongs in a repository.
They keep the shapes that actually broke something - icon-only links, ';' as
the parameter separator, spacer rows, a pre-selected filter that is not
neutral - so a rewrite that "looks equivalent" still has to pass them.
"""

from insis import parse, search
from insis.models import UNRESTRICTED, Filter, Option
from insis.store import to_markdown

RESULTS = """
<table>
<tr class="zahlavi">
  <th>Instituce</th><th>Stát</th><th>Dohoda</th><th>Odkdy</th>
  <th>Dokdy</th><th>Stav</th><th>Zpráva</th><th>Tisk</th>
</tr>
<tr>
  <td>Aalto University</td><td>Finská republika</td><td>Erasmus+</td>
  <td>05.08.2024</td><td>19.12.2024</td><td>Ukončený výjezd</td>
  <td><a href="/auth/int/zavzpr.pl?akce=1;zobrazit=111"><span title="x"></span></a></td>
  <td><a href="/auth/int/zavzpr.pl?akce=1;tisk=111"><span title="y"></span></a></td>
</tr>
<tr>
  <td>ESADE</td><td>Španělské království</td><td>CEMS</td>
  <td>01.02.2025</td><td>30.06.2025</td><td>Ukončený výjezd</td>
  <td></td><td></td>
</tr>
</table>
"""

REPORT = """
<table>
<tr><th>Domácí univerzita</th></tr>
<tr><td>Fakulta:</td><td>Fakulta informatiky a statistiky</td></tr>
<tr><td>Koordinátor:</td><td>Ing. Někdo Někdo</td></tr>
<tr><td>Hostitelská instituce</td></tr>
<tr><td>Název instituce:</td><td>Aalto University</td></tr>
<tr><td>Stát:</td><td>Finská republika</td></tr>
<tr><td>Pobyt od 5.&nbsp;8.&nbsp;2024 do 19.&nbsp;12.&nbsp;2024.</td></tr>
<tr><td>Doba pobytu je 4,5 měsíce.</td></tr>
</table>
<table>
<tr><td>1.&nbsp;Základní údaje</td></tr>
<tr><td>Studijní jazyk</td><td>Angličtina</td></tr>
<tr><td></td></tr>
<tr><td>Pokud jiný upřesněte</td><td></td></tr>
<tr><td>3.&nbsp;Ubytování</td></tr>
<tr><td>Kdy jste si zajišťoval/a ubytování?</td><td>Měsíc předem.</td></tr>
</table>
"""

FILTER_FORM = """
<form method="post" action="/auth/int/zavzpr.pl">
<input type="hidden" name="akce" value="1" />
<select name="filtr_obdobi">
  <option value="0">-- nezadáno --</option>
  <option value="381" selected>2025/2026</option>
  <option value="361">2024/2025</option>
</select>
<select name="fakulta">
  <option value="0">-- neomezeno --</option>
  <option value="40" selected>FIS Fakulta informatiky a statistiky</option>
</select>
<input type="submit" name="omezit" value="Zobrazit"/>
</form>
"""


# -- results -----------------------------------------------------------------

def test_report_id_comes_from_the_href_not_the_cell():
    """The Zpráva cell holds an icon; its text is empty and always was."""
    stays = parse.parse_results(RESULTS, "https://insis.vse.cz/auth/int/zavzpr.pl")
    assert [s.report_id for s in stays] == ["111", ""]
    assert stays[0].institution == "Aalto University"
    assert stays[0].start == "05.08.2024"


def test_a_row_without_a_report_is_kept_but_marked():
    """A stay nobody wrote up is still a row; it just cannot be downloaded."""
    stays = parse.parse_results(RESULTS)
    assert stays[0].has_report and not stays[1].has_report


def test_insis_no_data_notice_is_not_a_stay():
    """An empty result set is a sentence *inside* the table, in column one."""
    empty = RESULTS.replace("<td>Aalto University</td>",
                            "<td>Nenalezena žádná vyhovující data.</td>")
    institutions = [s.institution for s in parse.parse_results(empty)]
    assert "Nenalezena žádná vyhovující data." not in institutions


def test_a_row_needs_more_than_one_filled_cell():
    """Notices fill one cell; a real row says where, which country and when."""
    lonely = """
    <table>
    <tr><th>Instituce</th><th>Stát</th><th>Dohoda</th><th>Odkdy</th>
        <th>Dokdy</th><th>Stav</th><th>Zpráva</th><th>Tisk</th></tr>
    <tr><td>Nějaké oznámení</td><td></td><td></td><td></td>
        <td></td><td></td><td></td><td></td></tr>
    </table>
    """
    assert parse.parse_results(lonely) == []


def test_columns_are_read_by_header_not_position():
    swapped = RESULTS.replace(
        "<th>Instituce</th><th>Stát</th>", "<th>Stát</th><th>Instituce</th>")
    stays = parse.parse_results(swapped)
    assert stays[0].institution == "Finská republika"   # follows the header


# -- one report --------------------------------------------------------------

def test_report_header_is_read_by_label():
    report = parse.parse_report(REPORT, "111")
    assert report.institution == "Aalto University"
    assert report.faculty == "Fakulta informatiky a statistiky"
    assert report.period.startswith("Pobyt od")
    assert "4,5" in report.duration


def test_sections_split_on_numbered_heading_rows():
    report = parse.parse_report(REPORT, "111")
    assert [s.title for s in report.sections] == \
        ["1. Základní údaje", "3. Ubytování"]
    assert len(report.sections[0].items) == 2      # spacer row is not a question


def test_unanswered_questions_are_kept_in_the_data():
    """'Asked and not answered' is a fact; only the markdown drops it."""
    report = parse.parse_report(REPORT, "111")
    first = report.sections[0]
    assert [qa.answer for qa in first.items] == ["Angličtina", ""]
    assert len(first.answered()) == 1
    assert report.answers == 2 and not report.is_empty()


def test_markdown_keeps_questions_but_drops_the_blanks():
    report = parse.parse_report(REPORT, "111")
    stays = parse.parse_results(RESULTS)
    md = to_markdown(stays[0], report)
    assert "## 1. Základní údaje" in md
    assert "**Studijní jazyk**" in md and "Angličtina" in md
    assert "Pokud jiný upřesněte" not in md


# -- filters and the plan ----------------------------------------------------

DEPENDENT_FORM = """
<script>
  var instituce_pole_zavislosti = {};
  instituce_pole_zavislosti = {'724' : {},'276' : {},'0' : {}};
  instituce_pole_zavislosti['724'] = {'68' : 1,'0' : 1,'70' : 1};
  instituce_pole_zavislosti['276'] = {'99' : 1,'0' : 1};
  instituce_pole_zavislosti['0'] = {'68' : 1,'70' : 1,'99' : 1};
</script>
<form method="post" action="/auth/int/zavzpr.pl">
<select name="filtr_stat" onchange="js_instituce_change(1, 1);">
  <option value="0">-- nezadáno --</option>
  <option value="724">Španělské království</option>
  <option value="276">Spolková republika Německo</option>
</select>
<select name="filtr_instituce">
  <option value="0">-- nezadáno --</option>
  <option value="68">ESADE</option>
  <option value="70">ESCI-UPF</option>
  <option value="99">Universität Mannheim</option>
</select>
<select name="fakulta"><option value="0">-- neomezeno --</option>
  <option value="40">FIS</option><option value="30">FPH</option></select>
<select name="stupen" data-dependency-aliases='{"a":"fakulta"}'>
  <option value="0">-- neomezeno --</option>
  <option value="1" data-depends-on="[[[a:40],[a:30]]]">Bakalářský</option>
  <option value="2" data-depends-on="[[[a:30]]]">Magisterský</option>
</select>
<input type="submit" name="omezit" value="Zobrazit"/>
</form>
"""


def test_country_to_institution_map_is_read_past_the_empty_one():
    """The map is declared empty first, then filled one country at a time."""
    by_country = parse.institutions_by_country(DEPENDENT_FORM)
    assert by_country == {"724": {"68", "70"}, "276": {"99"}}


def test_institutions_narrow_to_the_chosen_country():
    by = {f.name: f for f in parse.parse_filters(DEPENDENT_FORM)}
    institutions = by["filtr_instituce"]
    assert len(institutions.available({})) == 3            # no country: all
    spanish = institutions.available({"filtr_stat": ["724"]})
    assert [o.label for o in spanish] == ["ESADE", "ESCI-UPF"]


def test_several_countries_union_their_institutions():
    by = {f.name: f for f in parse.parse_filters(DEPENDENT_FORM)}
    both = by["filtr_instituce"].available({"filtr_stat": ["724", "276"]})
    assert len(both) == 3


def test_study_chain_narrows_by_data_depends_on():
    by = {f.name: f for f in parse.parse_filters(DEPENDENT_FORM)}
    degrees = by["stupen"]
    assert degrees.parents == ["fakulta"]
    assert [o.label for o in degrees.available({"fakulta": ["40"]})] \
        == ["Bakalářský"]
    assert len(degrees.available({"fakulta": ["30"]})) == 2


def test_pruning_drops_picks_the_new_parent_no_longer_offers():
    by = {f.name: f for f in parse.parse_filters(DEPENDENT_FORM)}
    institutions = by["filtr_instituce"]
    institutions.chosen = ["68", "99"]
    dropped = institutions.prune({"filtr_stat": ["724"]})
    assert dropped == ["99"] and institutions.chosen == ["68"]


def test_filters_drop_the_no_filter_option():
    filters = parse.parse_filters(FILTER_FORM)
    by = {f.name: f for f in filters}
    assert [o.value for o in by["filtr_obdobi"].options] == ["381", "361"]
    assert by["fakulta"].label == "VŠE faculty"


def test_submit_button_is_found():
    """Without omezit=Zobrazit InSIS re-serves the empty search page."""
    assert parse.submit_button(FILTER_FORM) == {"omezit": "Zobrazit"}


def test_plan_is_the_product_of_the_choices():
    year = Filter("filtr_obdobi", "Year",
                  [Option("361", "2024/2025"), Option("342", "2023/2024")],
                  chosen=["361", "342"])
    country = Filter("filtr_stat", "Country", [Option("724", "ES")],
                     chosen=["724"])
    combos = search.plan([year, country])
    assert len(combos) == 2
    assert {c["filtr_obdobi"] for c in combos} == {"361", "342"}


def test_plan_zeroes_the_fields_insis_prefills_with_your_own_study():
    """The bug this prevents: 'all of 2024/2025' silently meaning 'my programme'."""
    year = Filter("filtr_obdobi", "Year", [Option("361", "2024/2025")],
                  chosen=["361"])
    combo = search.plan([year])[0]
    assert all(combo[name] == UNRESTRICTED for name in search.NEUTRALISE)


def test_columns_insis_prints_are_filtered_after_the_search():
    """26 universities × 4 years must be 4 searches, not 104."""
    year = Filter("filtr_obdobi", "Year",
                  [Option("361", "2024/2025"), Option("342", "2023/2024")],
                  chosen=["361", "342"])
    schools = Filter("filtr_instituce", "Host institution",
                     [Option("68", "ESADE"), Option("99", "Mannheim")],
                     chosen=["68", "99"])
    combos = search.plan([year, schools])
    assert len(combos) == 2
    assert combos[0]["filtr_instituce"] == UNRESTRICTED

    stays = parse.parse_results(RESULTS)
    esade = Filter("filtr_instituce", "Host institution",
                   [Option("1", "ESADE")], chosen=["1"])
    kept = search.apply_local(stays, [esade])
    assert [s.institution for s in kept] == ["ESADE"]


def test_a_plan_without_a_year_is_refused():
    assert search.needs_period([Filter("filtr_stat", "Country", [])])
    assert not search.needs_period(
        [Filter("filtr_obdobi", "Year", [Option("361", "x")], chosen=["361"])])


def test_merge_deduplicates_on_report_id():
    stays = parse.parse_results(RESULTS)
    merged = search.merge([stays, stays])
    assert len(merged) == len(stays)
