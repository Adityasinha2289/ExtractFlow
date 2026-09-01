"""Tests for the ExtractFlow framework components."""

import json
import os

import pytest

from extractflow.core.parser import RecordParser
from extractflow.core.pipeline import Pipeline
from extractflow.exporters.csv_exporter import export_csv
from extractflow.exporters.json_exporter import export_json
from extractflow.extractors.api import extract_api
from extractflow.extractors.embedded_json import (
    extract_assignment,
    extract_embedded,
    extract_script_json,
)
from extractflow.extractors.html import HTMLExtractor
from extractflow.extractors.jsonld import extract_jsonld
from extractflow.extractors.tables import extract_tables
from extractflow.utils.helpers import coalesce, deep_get, slugify, stable_hash
from extractflow.utils.retry import with_retry
from extractflow.validators.duplicate_detector import deduplicate, detect_duplicates
from extractflow.validators.quality_checker import check_quality, completeness
from extractflow.validators.schema_validator import validate, validate_all


# -------------------------------------------------------------- extractors
def test_extract_assignment_is_string_aware():
    # A brace inside a quoted value must not unbalance the scan.
    html = '<script>var data={"a":"}{","b":[1,2]};</script>'
    assert extract_assignment(html, "data") == {"a": "}{", "b": [1, 2]}


def test_extract_assignment_missing_variable():
    assert extract_assignment("<script>var other={};</script>", "data") is None


def test_extract_script_json():
    html = (
        '<script id="__NEXT_DATA__" type="application/json">{"props":{"x":1}}</script>'
    )
    assert extract_script_json(html)["props"]["x"] == 1


def test_extract_embedded_with_path():
    html = '<script>var d={"a":{"b":[{"c":7}]}};</script>'
    out = extract_embedded(html, {"value": {"variable": "d", "path": "a.b[0].c"}})
    assert out["value"] == 7


def test_html_extractor_types():
    html = """
    <div class="card" data-id="7">
      <h3>Title A</h3><span class="p">10%</span>
      <a href="/x.pdf">doc</a><li>one</li><li>two</li>
    </div>"""
    mapping = {
        "title": {"selector": "h3", "type": "text"},
        "pct": {"selector": "span.p", "type": "text", "regex": r"(\d+)%"},
        "items": {"selector": "li", "type": "list"},
        "href": {"selector": "a", "type": "attribute", "attribute": "href"},
        "missing": {"selector": ".nope", "type": "text", "default": None},
        "has_link": {"selector": "a", "type": "exists"},
    }
    out = HTMLExtractor(mapping).extract(html)
    assert out["title"] == "Title A"
    assert out["pct"] == "10"
    assert out["items"] == ["one", "two"]
    assert out["href"] == "/x.pdf"
    assert out["missing"] is None
    assert out["has_link"] is True


def test_html_extract_all_captures_root_classes():
    html = """
    <div class="tile grocery east"><h3>A</h3></div>
    <div class="tile fashion west"><h3>B</h3></div>"""
    records = HTMLExtractor({"name": {"selector": "h3", "type": "text"}}).extract_all(
        html, "div.tile"
    )
    assert [r["name"] for r in records] == ["A", "B"]
    assert "grocery" in records[0]["_root_classes"]


def test_extract_tables_zips_headers():
    html = """<table><tr><th>Brand</th><th>Offer</th></tr>
              <tr><td>AbhiBus</td><td>10% off</td></tr></table>"""
    tables = extract_tables(html)
    assert tables[0]["records"] == [{"Brand": "AbhiBus", "Offer": "10% off"}]


def test_extract_tables_skips_single_row_tables():
    assert extract_tables("<table><tr><td>only</td></tr></table>") == []


def test_extract_jsonld_flattens_graph_and_filters_type():
    html = """<script type="application/ld+json">
      {"@graph":[{"@type":"Offer","name":"x"},
                 {"@type":"WebPage","name":"y"}]}</script>"""
    assert [b["name"] for b in extract_jsonld(html, ["Offer"])] == ["x"]


def test_extract_api_accepts_string_and_path():
    assert extract_api('{"data":{"items":[1,2]}}', "data.items") == [1, 2]
    assert extract_api("not json") is None


# ------------------------------------------------------------------ utils
def test_deep_get_handles_lists_and_missing():
    obj = {"a": {"b": [{"c": 1}]}}
    assert deep_get(obj, "a.b[0].c") == 1
    assert deep_get(obj, "a.b[5].c", "fallback") == "fallback"
    assert deep_get(obj, "nope.x") is None


def test_coalesce_skips_blank_strings_but_keeps_zero():
    assert coalesce(None, "  ", "value") == "value"
    assert coalesce(None, 0) == 0


def test_stable_hash_is_deterministic_and_order_sensitive():
    assert stable_hash("a", "b") == stable_hash("a", "b")
    assert stable_hash("a", "b") != stable_hash("b", "a")


def test_slugify():
    assert slugify("Big C Mobiles!") == "big_c_mobiles"


def test_with_retry_retries_then_succeeds():
    calls = {"n": 0}

    @with_retry(attempts=3, delay=0.01)
    def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise ValueError("boom")
        return "ok"

    assert flaky() == "ok"
    assert calls["n"] == 3


def test_with_retry_reraises_after_final_attempt():
    @with_retry(attempts=2, delay=0.01)
    def always_fails():
        raise ValueError("nope")

    with pytest.raises(ValueError):
        always_fails()


# ----------------------------------------------------------------- parser
def test_record_parser_isolates_transform_failures():
    parser = RecordParser({"n": int})
    out = parser.parse({"n": "not-a-number", "s": "  keep  "})
    assert out["n"] is None  # failure does not kill the record
    assert out["s"] == "keep"


# --------------------------------------------------------------- pipeline
def test_pipeline_runs_stages_in_order():
    result = (
        Pipeline()
        .add("seed", lambda _: [1, 2, 3])
        .add("double", lambda xs: [x * 2 for x in xs])
        .run()
    )
    assert result.ok
    assert result.records == [2, 4, 6]
    assert [s.name for s in result.stages] == ["seed", "double"]


def test_pipeline_passes_through_none_returning_stage():
    result = Pipeline().add("seed", lambda _: [1]).add("noop", lambda xs: None).run()
    assert result.records == [1]


def test_pipeline_records_stage_failure_and_aborts():
    def boom(_):
        raise RuntimeError("stage failed")

    result = (
        Pipeline().add("a", lambda _: [1]).add("b", boom).add("c", lambda _: [9]).run()
    )
    assert not result.ok
    assert len(result.stages) == 2  # c never ran


# -------------------------------------------------------------- validators
def test_validate_reports_rather_than_raises():
    schema = {"required": ["a.b"], "types": {"n": "number"}, "enums": {"t": ["S", "A"]}}
    out = validate({"n": "x", "t": "Z"}, schema)
    assert out["valid"] is False
    assert len(out["errors"]) == 3


def test_validate_types_ignore_nulls():
    assert validate({"n": None}, {"types": {"n": "number"}})["valid"] is True


def test_validate_all_rolls_up():
    out = validate_all([{"a": 1}, {}], {"required": ["a"]})
    assert out["valid_count"] == 1 and out["invalid_indexes"] == [1]


def test_detect_duplicates_ignores_null_keys():
    data = [{"k": "x"}, {"k": "x"}, {"k": None}, {"k": None}]
    out = detect_duplicates(data, "k")
    assert out["duplicate_count"] == 1
    assert out["unkeyed_indexes"] == [2, 3]


def test_deduplicate_with_merge():
    data = [{"k": "a", "n": 1}, {"k": "a", "n": 2}]
    out = deduplicate(data, "k", merge=lambda x, y: {**x, "n": x["n"] + y["n"]})
    assert out == [{"k": "a", "n": 3}]


def test_completeness_counts_zero_as_populated():
    # A rate of zero is an extracted value, not a missing field.
    assert completeness({"a": 0, "b": None}, ["a", "b"]) == 0.5


def test_check_quality_coverage_format():
    out = check_quality([{"a": 1}, {"a": None}], ["a"])
    assert out["coverage_by_field"]["a"] == "1/2"


# -------------------------------------------------------------- exporters
def test_export_json_and_lines(tmp_path):
    path = str(tmp_path / "out.json")
    export_json([{"a": 1}], path)
    assert json.load(open(path, encoding="utf-8")) == [{"a": 1}]

    lines_path = str(tmp_path / "out.jsonl")
    export_json([{"a": 1}, {"a": 2}], lines_path, lines=True)
    assert len(open(lines_path, encoding="utf-8").read().strip().splitlines()) == 2


def test_export_csv_flattens_nested(tmp_path):
    path = str(tmp_path / "out.csv")
    export_csv([{"a": {"b": 1}, "c": [1, 2]}], path)
    header = open(path, encoding="utf-8-sig").readline()
    assert "a.b" in header and "c" in header


def test_atomic_write_leaves_no_temp_files(tmp_path):
    path = str(tmp_path / "x.json")
    export_json({"a": 1}, path)
    assert os.listdir(tmp_path) == ["x.json"]
