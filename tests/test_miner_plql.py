"""PLQL parser and engine, checked against the hand-checkable fixture (tests/fixtures/README.md).

Cases: C1 A B C D (4 h), C2 A B B C D (6 h, rework), C3 A B C D (2 h), C4 A C (1 h, open), 007 A (0 h).
"""

from __future__ import annotations

import pytest

from app.database import SessionLocal
from app.models import Dataset
from app.services.eventlog import get_event_log
from miner.plql import Frame, KnowledgeModel, PlqlError, QueryItem, evaluate_measure, parse, quote, run_query
from miner.plql.engine import count_matching

KM = KnowledgeModel(
    kpis={"cases": "COUNT_CASES()", "avg_h": "AVG(case.duration_h)", "rework_rate": "SHARE(case.rework)",
          "loop_a": 'KPI("loop_b")', "loop_b": 'KPI("loop_a")'},
    filters={"done": 'case.status = "completed"', "has_b": 'CONTAINS("B")'},
)


@pytest.fixture
def frame(hand_dataset) -> Frame:
    with SessionLocal() as db:
        ds = db.get(Dataset, hand_dataset)
        return Frame.of(get_event_log(db, ds))


def value(frame, formula, filters=None):
    return evaluate_measure(frame, formula, KM, filters)[0]


def q(frame, dims, measures, filters=None, **kw):
    r = run_query(frame, [QueryItem(d) for d in dims], [QueryItem(m) for m in measures], KM, filters, **kw)
    return r, {tuple(row[:len(dims)]): row[len(dims):] for row in r.rows}


# --------------------------------------------------------------------------- parser
def test_parser_precedence_and_literals():
    node = parse('1 + 2 * 3 = 7 AND NOT case.rework OR event.activity IN ("A", \'B\')')
    assert node.op == "OR"
    assert parse("-.5e1").operand.value == 5.0
    assert parse('"a\\"b\\u00e9"').value == 'a"bé'
    assert parse("x.y -- comment").name == "y"


@pytest.mark.parametrize("text, fragment, pos", [
    ("", "empty", 0),
    ("AVG(case.duration_d", "Expected ')'", 19),
    ('"open', "not closed", 0),
    ("1 +", "ends too early", 3),
    ("foo", "Unknown name", 0),
    ("case.x y", "operator or comma missing", 7),
    ("1 # 2", "Unexpected character", 2),
])
def test_parser_errors_have_positions(text, fragment, pos):
    with pytest.raises(PlqlError) as e:
        parse(text)
    assert fragment in e.value.message
    assert e.value.position == pos


def test_quote_round_trips():
    for s in ['plain', 'with "quotes"', 'back\\slash', 'new\nline']:
        assert parse(quote(s)).value == s


# --------------------------------------------------------------------------- measures
def test_basic_kpis(frame):
    assert value(frame, "COUNT_CASES()") == 5
    assert value(frame, "COUNT_EVENTS()") == 16
    assert value(frame, "AVG(case.duration_h)") == pytest.approx(2.6)
    assert value(frame, "MEDIAN(case.duration_h)") == pytest.approx(2.0)
    assert value(frame, "PERCENTILE(case.duration_s, 0.9)") == pytest.approx(18720)
    assert value(frame, "SHARE(case.rework)") == pytest.approx(0.2)
    assert value(frame, 'SHARE(case.status = "completed")') == pytest.approx(0.6)
    assert value(frame, "SUM(event.cost)") == pytest.approx(73)
    assert value(frame, "COUNT_DISTINCT(event.activity)") == 4
    assert value(frame, "COUNT_DISTINCT(case.variant)") == 4
    assert value(frame, 'COUNT_CASES(event.activity = "D")') == 3
    assert value(frame, 'COUNT_EVENTS(event.activity = "B")') == 4
    assert value(frame, "MAX(case.age_d)") == pytest.approx(40 / 24)


def test_measure_arithmetic_and_references(frame):
    assert value(frame, 'COUNT_CASES(case.status = "completed") / COUNT_CASES() * 100') == pytest.approx(60)
    assert value(frame, 'KPI("avg_h") * 2') == pytest.approx(5.2)
    assert value(frame, 'ROUND(KPI("rework_rate"), 1)') == pytest.approx(0.2)
    assert value(frame, "COUNT_CASES()", ['FILTER("done")']) == 3
    assert value(frame, "AVG(IF(case.rework, case.duration_h, NULL))") == pytest.approx(6)
    assert value(frame, "COUNT_CASES() / 0") is None  # division by zero is missing, not infinite


def test_case_functions(frame):
    # A→D: C1 08:00→12:00 = 4 h, C2 08:00→14:00 = 6 h, C3 08:00→10:00 = 2 h; C4 and 007 have no D
    assert value(frame, 'AVG(THROUGHPUT("A", "D", "h"))') == pytest.approx(4)
    assert value(frame, 'COUNT(THROUGHPUT("A", "D", "h"))') == 3
    assert value(frame, 'SUM(COUNT_ACT("B"))') == 4
    assert value(frame, 'SHARE(CONTAINS("C", "D"))') == pytest.approx(0.8)
    assert value(frame, "SUM(CASE_COUNT(event.resource = \"r2\"))") == 7
    assert value(frame, "MAX(CASE_SUM(event.cost))") == pytest.approx(21)  # C2: 10 + 5 + 5 + (no cost) + 1
    assert value(frame, 'COUNT_CASES(CASE_FIRST(event.resource) = "r1")') == 5


# --------------------------------------------------------------------------- grouping
def test_group_by_case_dimension(frame):
    r, rows = q(frame, ["case.status"], ["COUNT_CASES()", "COUNT_EVENTS()", "AVG(case.duration_h)"])
    assert rows[("completed",)] == [3, 13, pytest.approx(4)]
    assert rows[("incomplete",)] == [1, 2, pytest.approx(1)]
    assert rows[("unknown",)] == [1, 1, pytest.approx(0)]
    assert r.columns[0]["type"] == "text" and r.columns[1]["type"] == "number"
    assert r.rows[0][0] == "completed"  # default order: first measure descending


def test_group_by_event_dimension_counts_distinct_cases(frame):
    _, rows = q(frame, ["event.activity"], ["COUNT_CASES()", "COUNT_EVENTS()", "AVG(case.duration_h)"])
    assert rows[("A",)][:2] == [5, 5]
    assert rows[("B",)][:2] == [3, 4]  # C2 has B twice but is one case
    assert rows[("B",)][2] == pytest.approx(4)  # cases C1, C2, C3 each once
    assert rows[("D",)][:2] == [3, 3]


def test_event_condition_inside_event_grouping(frame):
    _, rows = q(frame, ["event.department"], ['COUNT_CASES(event.resource = "r2")'])
    assert rows[("D1",)] == [2]  # B by r2 in C1, C2
    assert rows[("D2",)] == [4]  # C by r2 in C1..C4


def test_time_dimension_sorts_ascending(frame):
    r, rows = q(frame, ["DAY(case.start)"], ["COUNT_CASES()"])
    assert [row[0] for row in r.rows] == ["2024-01-01", "2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05"]
    r, _ = q(frame, ["MONTH(event.ts)", "WEEKDAY(event.ts)"], ["COUNT_EVENTS()"])
    assert r.rows[0][:2] == ["2024-01", "1 Mon"]


def test_two_dimensions_and_limit(frame):
    r, rows = q(frame, ["case.first_activity", "case.last_activity"], ["COUNT_CASES()"], limit=2)
    assert r.total_groups == 3 and len(r.rows) == 2
    assert r.rows[0] == ["A", "D", 3]


def test_filters_keep_whole_cases(frame):
    r, rows = q(frame, ["event.activity"], ["COUNT_EVENTS()"], filters=['event.activity = "D"'])
    assert r.case_count == 3 and r.total_case_count == 5
    assert rows[("A",)] == [3]  # whole histories of the matching cases
    assert count_matching(frame, 'case.start >= "2024-01-03"', KM) == (3, 5)
    assert count_matching(frame, 'case.id IN ("007", "C1")', KM) == (2, 5)
    assert count_matching(frame, "case.age_d = NULL", KM) == (3, 5)


def test_empty_population(frame):
    r, _ = q(frame, [], ["COUNT_CASES()", "AVG(case.duration_h)"], filters=["case.events > 100"])
    assert r.rows == [[0, None]]
    r, _ = q(frame, ["case.status"], ["COUNT_CASES()"], filters=["FALSE"])
    assert r.rows == [] and r.total_groups == 0


# --------------------------------------------------------------------------- errors
@pytest.mark.parametrize("dims, measures, fragment", [
    ([], ["case.duration_d"], "must be inside an aggregation"),
    ([], ["AVG(SUM(event.cost))"], "cannot be nested"),
    ([], ['KPI("nope")'], "does not exist"),
    ([], ['KPI("loop_a")'], "refers to itself"),
    ([], ["PERCENTILE(case.duration_d, 2)"], "between 0 and 1"),
    ([], ["AVG(case.activity)"], "Unknown case column"),
    ([], ["AVG(case.first_activity)"], "A number is needed"),
    ([], ["1 + 1"], "needs an aggregation"),
    (["COUNT_CASES()"], ["COUNT_CASES()"], "aggregation and cannot be used here"),
    (["case.duration_d > \"x\""], ["COUNT_CASES()"], "cannot be compared"),
    ([], ['AVG(THROUGHPUT("A", "B", "years"))'], "Unknown time unit"),
])
def test_errors_are_explained(frame, dims, measures, fragment):
    with pytest.raises(PlqlError) as e:
        q(frame, dims, measures)
    assert fragment in e.value.message
