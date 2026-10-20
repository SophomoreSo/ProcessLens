"""Unit tests for row validation, timestamp normalization and deterministic ordering."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from app.config import Settings
from app.schemas.imports import ColumnMapping, TimestampOptions
from app.services.ingestion import IngestionError, read_raw_table, suggest_mapping
from app.services.validation import validate_table

MAP = ColumnMapping(case_id="case", activity="activity", timestamp="ts")


def table_from(tmp_path: Path, text: str, **settings):
    p = tmp_path / "t.csv"
    p.write_text(text, encoding="utf-8")
    return read_raw_table(p, "utf-8", ",", Settings(data_dir=tmp_path, **settings))


def validate(tmp_path, text, mapping=MAP, **ts):
    return validate_table(table_from(tmp_path, text), mapping, TimestampOptions(**ts))


def codes(result) -> dict[int, list[str]]:
    return {r: sorted(c) for r, c in result.invalid_rows.items()}


def test_identifiers_preserved_as_strings(tmp_path):
    r = validate(tmp_path, "case,activity,ts\n000123,A,2024-01-01T00:00:00Z\n0042,B,2024-01-01T01:00:00Z\n1e3,C,2024-01-01T02:00:00Z\n")
    assert set(r.normalized["case_id"]) == {"000123", "0042", "1e3"}
    assert not r.invalid_rows


def test_invalid_rows_are_flagged_not_dropped(tmp_path):
    text = ("case,activity,ts\n"
            "1,A,2024-01-01T00:00:00Z\n"   # row 1 ok
            ",A,2024-01-01T00:00:00Z\n"    # row 2 missing case id
            "1,   ,2024-01-01T00:00:00Z\n"  # row 3 empty activity
            "1,B,not-a-date\n"             # row 4 malformed
            "1,B,\n"                       # row 5 missing timestamp
            "1,B\n")                       # row 6 field count
    r = validate(tmp_path, text)
    assert codes(r) == {2: ["missing_case_id"], 3: ["empty_activity"], 4: ["malformed_timestamp"],
                        5: ["missing_timestamp"], 6: ["field_count_mismatch", "missing_timestamp"]}
    assert r.valid_rows == 1
    assert r.total_rows == 6


def test_duplicate_event_ids_flag_all_rows(tmp_path):
    m = ColumnMapping(case_id="case", activity="activity", timestamp="ts", event_id="eid")
    r = validate(tmp_path, "case,activity,ts,eid\n1,A,2024-01-01T00:00:00Z,e1\n1,B,2024-01-01T01:00:00Z,e1\n"
                           "1,C,2024-01-01T02:00:00Z,e2\n1,D,2024-01-01T03:00:00Z,\n", mapping=m)
    assert codes(r) == {1: ["duplicate_event_id"], 2: ["duplicate_event_id"]}
    assert r.counts_by_code["missing_event_id"] == 1


def test_cost_and_start_validation(tmp_path):
    m = ColumnMapping(case_id="case", activity="activity", timestamp="ts", cost="cost", start_timestamp="start")
    text = ("case,activity,ts,start,cost\n"
            "1,A,2024-01-01T10:00:00Z,2024-01-01T09:00:00Z,12.5\n"
            "1,B,2024-01-01T11:00:00Z,2024-01-01T12:00:00Z,1\n"   # start after end
            "1,C,2024-01-01T12:00:00Z,,12,50 EUR\n"              # extra field -> field count + cost
            "1,D,2024-01-01T13:00:00Z,garbage,inf\n"             # malformed start + inf cost
            "1,E,2024-01-01T14:00:00Z,,-3\n")                    # negative cost is a warning only
    r = validate(tmp_path, text, mapping=m)
    assert codes(r)[2] == ["start_after_end"]
    assert "field_count_mismatch" in codes(r)[3]
    assert sorted(codes(r)[4]) == ["invalid_cost", "malformed_start_timestamp"]
    assert 5 not in r.invalid_rows and r.counts_by_code["negative_cost"] == 1
    ok = r.normalized.set_index("activity")
    assert ok.loc["A", "cost"] == 12.5
    assert ok.loc["E", "cost"] == -3


def test_missing_optional_fields_are_null(tmp_path):
    m = ColumnMapping(case_id="case", activity="activity", timestamp="ts", resource="res", cost="cost")
    r = validate(tmp_path, "case,activity,ts,res,cost\n1,A,2024-01-01T00:00:00Z,,\n", mapping=m)
    row = r.normalized.iloc[0]
    assert pd.isna(row["resource"])  # pandas string dtype stores missing as NaN; persisted as NULL
    assert pd.isna(row["cost"])
    assert not r.invalid_rows


def test_offsets_and_source_timezone(tmp_path):
    text = ("case,activity,ts\n"
            "1,A,2024-06-01T12:00:00+02:00\n"   # explicit offset wins
            "1,B,2024-06-01 12:00:00\n"         # naive -> Europe/Berlin (CEST, +2)
            "1,C,2024-01-15 12:00:00\n")        # naive winter -> CET (+1)
    r = validate(tmp_path, text, source_timezone="Europe/Berlin")
    ts = r.normalized.set_index("activity")["ts"]
    assert ts["A"] == pd.Timestamp("2024-06-01T10:00:00Z")
    assert ts["B"] == pd.Timestamp("2024-06-01T10:00:00Z")
    assert ts["C"] == pd.Timestamp("2024-01-15T11:00:00Z")


@pytest.mark.parametrize("policy,expected", [("earliest", "2024-10-27T00:30:00Z"), ("latest", "2024-10-27T01:30:00Z")])
def test_ambiguous_local_time_policy(tmp_path, policy, expected):
    text = "case,activity,ts\n1,A,2024-10-27 02:30:00\n"  # occurs twice in Europe/Berlin
    rejected = validate(tmp_path, text, source_timezone="Europe/Berlin")
    assert codes(rejected) == {1: ["ambiguous_local_time"]}
    resolved = validate(tmp_path, text, source_timezone="Europe/Berlin", ambiguous_policy=policy)
    assert not resolved.invalid_rows
    assert resolved.normalized["ts"].iloc[0] == pd.Timestamp(expected)
    assert resolved.counts_by_code["ambiguous_time_resolved"] == 1


def test_nonexistent_local_time_policy(tmp_path):
    text = "case,activity,ts\n1,A,2024-03-31 02:30:00\n"  # skipped in Europe/Berlin
    assert codes(validate(tmp_path, text, source_timezone="Europe/Berlin")) == {1: ["nonexistent_local_time"]}
    fwd = validate(tmp_path, text, source_timezone="Europe/Berlin", nonexistent_policy="shift_forward")
    assert fwd.normalized["ts"].iloc[0] == pd.Timestamp("2024-03-31T01:00:00Z")  # 03:00 CEST


def test_explicit_format_and_unix(tmp_path):
    r = validate(tmp_path, "case,activity,ts\n1,A,31/12/2024 23:59\n", format="%d/%m/%Y %H:%M")
    assert r.normalized["ts"].iloc[0] == pd.Timestamp("2024-12-31T23:59:00Z")
    r2 = validate(tmp_path, "case,activity,ts\n1,A,1704067200\n", format="unix_s")
    assert r2.normalized["ts"].iloc[0] == pd.Timestamp("2024-01-01T00:00:00Z")
    r3 = validate(tmp_path, "case,activity,ts\n1,A,31/12/2024 23:59\n")  # wrong format -> error, not a guess
    assert codes(r3) == {1: ["malformed_timestamp"]}


def test_deterministic_order_ties_and_rework(tmp_path):
    text = ("case,activity,ts\n"
            "1,C,2024-01-01T02:00:00Z\n"
            "1,B,2024-01-01T01:00:00Z\n"   # row 2 tied with row 3
            "1,A,2024-01-01T01:00:00Z\n"
            "1,B,2024-01-01T03:00:00Z\n")  # repeated activity (rework) kept
    r = validate(tmp_path, text)
    norm = r.normalized
    assert list(norm["activity"]) == ["B", "A", "C", "B"]  # tie broken by source row
    assert list(norm["seq"]) == [0, 1, 2, 3]
    assert r.tie_cases == 1
    assert r.counts_by_code["timestamp_ties"] == 1
    assert r.valid_rows == 4


def test_whitespace_trim_reported(tmp_path):
    r = validate(tmp_path, "case,activity,ts\n 01 , A ,2024-01-01T00:00:00Z\n")
    assert r.normalized["case_id"].iloc[0] == "01"
    assert r.counts_by_code["whitespace_trimmed"] == 1


def test_file_level_limits(tmp_path):
    with pytest.raises(IngestionError) as exc:
        table_from(tmp_path, "case,activity,ts\n" + "1,A,2024-01-01T00:00:00Z\n" * 5, max_rows=3)
    assert exc.value.code == "too_many_rows"
    t = table_from(tmp_path, ",case,case\n1,2,3\n")
    assert t.columns == ["column_1", "case", "case (2)"]
    assert len(t.column_warnings) == 2


def test_mapping_suggestions():
    mapping, attrs = suggest_mapping(["Case ID", "Activity", "Timestamp", "org:resource", "Amount", "vendor"])
    assert mapping["case_id"] == "Case ID"
    assert mapping["activity"] == "Activity"
    assert mapping["timestamp"] == "Timestamp"
    assert mapping["resource"] == "org:resource"
    assert mapping["cost"] == "Amount"
    assert attrs == ["vendor"]


def test_mapping_rejects_duplicate_columns():
    with pytest.raises(ValueError):
        ColumnMapping(case_id="a", activity="a", timestamp="t")
