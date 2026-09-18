"""Tests for the type-map gap prober (plugins/analitiq-pipeline-builder/scripts/type_map_gaps.py).

The script holds no matching logic — resolution dispatches to the pinned
`analitiq-validator`'s internal helpers (the exact semantics every runtime
reader uses); these tests exercise them against the in-repo source, which moves
in lockstep with the pin. They pin the *wiring*: probe/rule routing per
direction, map precedence (connection primary over connector fallback,
mirroring the engine's `TypeMapper.compose`), per-map model validation, gap
reporting, and the CLI envelope.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2] / "plugins" / "analitiq-pipeline-builder"
sys.path.insert(0, str(ROOT / "scripts"))
import type_map_gaps as G  # noqa: E402

pytest.importorskip("analitiq.validator",
                    reason="requires: pip install -r requirements-dev.txt")
from analitiq.contracts.type_map import TYPE_MAP_SCHEMA_URL  # noqa: E402
from analitiq.validator import (  # noqa: E402
    finding_costs_a_pass, type_map_findings,
)

CONNECTOR_READ = [
    {"match": "exact", "native_type": "CITEXT", "arrow_type": "Utf8"},
    {"match": "regex", "native_type": "^NUMERIC\\((?<precision>[1-9]|[12]\\d|3[0-8]),\\s*(?<scale>\\d|[12]\\d|3[0-8])\\)$",
     "arrow_type": "Decimal128(${precision}, ${scale})"},
]
CONNECTOR_WRITE = [
    {"match": "exact", "arrow_type": "Utf8", "native_type": "TEXT"},
    {"match": "regex", "arrow_type": "^Decimal(128|256)\\((?<p>\\d+),\\s*(?<s>\\d+)\\)$",
     "native_type": "NUMERIC(${p}, ${s})"},
]


def _tm_doc(rules: list, direction: str) -> dict:
    return {"$schema": TYPE_MAP_SCHEMA_URL, direction: rules}


def _map(tmp_path: Path, name: str, rules: list, direction: str = "read") -> Path:
    p = tmp_path / name
    p.write_text(json.dumps(_tm_doc(rules, direction)))
    return p


def test_read_exact_is_normalized(tmp_path):
    # read-side probes are normalized (trim/collapse/uppercase) before matching
    result = G.resolve("read", ["citext", "  Citext  "], [_map(tmp_path, "r.json", CONNECTOR_READ)])
    assert result["resolved"] == {"citext": "Utf8", "  Citext  ": "Utf8"}
    assert result["gaps"] == []


def test_read_regex_substitutes_captures(tmp_path):
    result = G.resolve("read", ["numeric(10,2)"], [_map(tmp_path, "r.json", CONNECTOR_READ)])
    assert result["resolved"]["numeric(10,2)"] == "Decimal128(10, 2)"


def test_read_gap_reported(tmp_path):
    result = G.resolve("read", ["vector(3)", "citext"], [_map(tmp_path, "r.json", CONNECTOR_READ)])
    assert result["resolved"]["vector(3)"] is None
    assert result["gaps"] == ["vector(3)"]


def test_read_connection_map_is_primary(tmp_path):
    # maps concatenate in argument order, first match wins — the engine's compose order
    connection = _map(tmp_path, "conn.json",
                      [{"match": "exact", "native_type": "CITEXT", "arrow_type": "LargeUtf8"}])
    connector = _map(tmp_path, "base.json", CONNECTOR_READ)
    result = G.resolve("read", ["citext", "numeric(10,2)"], [connection, connector])
    assert result["resolved"]["citext"] == "LargeUtf8"
    # a probe the connection map misses falls through to the connector map
    assert result["resolved"]["numeric(10,2)"] == "Decimal128(10, 2)"


def test_write_matches_canonical_case_preserving(tmp_path):
    m = _map(tmp_path, "w.json", CONNECTOR_WRITE, "write")
    result = G.resolve("write", ["Utf8", "utf8", "Decimal128(20, 4)"], [m])
    # write matchers compare the canonical as authored — no normalization
    assert result["resolved"]["Utf8"] == "TEXT"
    assert result["resolved"]["utf8"] is None
    assert result["resolved"]["Decimal128(20, 4)"] == "NUMERIC(20, 4)"
    assert result["gaps"] == ["utf8"]


def test_write_gap_reported(tmp_path):
    result = G.resolve("write", ["Duration(SECOND)"], [_map(tmp_path, "w.json", CONNECTOR_WRITE, "write")])
    assert result["gaps"] == ["Duration(SECOND)"]


def test_map_without_rules_key_rejected(tmp_path):
    # the prober keeps no shape gate of its own: the published grader names every
    # part of the envelope that is missing, so the message says what to author
    bad = tmp_path / "r.json"
    bad.write_text('{"match": "exact"}')
    with pytest.raises(ValueError, match=r"is not a valid type map") as exc:
        G.resolve("read", ["citext"], [bad])
    for field in ("/$schema", "/match"):
        assert field in str(exc.value), exc.value


def test_a_connection_write_map_is_not_held_to_a_connector_vocabulary(tmp_path, capsys):
    # A connection map fills the gaps its connector left, so holding it to the
    # whole Arrow vocabulary earns the coverage warning on every run. The
    # authoring agent is told to add rules for families the connector already
    # renders, and shadows them.
    G.resolve("write", ["Utf8"], [_map(tmp_path, "type-map.json", CONNECTOR_WRITE, "write")])
    assert capsys.readouterr().err == ""


def test_an_advisory_reaches_the_operator_over_a_map_that_resolves(tmp_path, capsys):
    # The gap and the rule that was meant to fill it are reported together: a
    # duplicate is unreachable, so the probe it was written for comes back
    # uncovered and reads as a vocabulary the connector simply lacks.
    p = tmp_path / "type-map.json"
    p.write_text(json.dumps(_tm_doc(CONNECTOR_READ + [CONNECTOR_READ[0]], "read")))
    result = G.resolve("read", ["citext", "vector(3)"], [p])
    assert result["gaps"] == ["vector(3)"]
    assert "duplicate rule" in capsys.readouterr().err


def test_an_advisory_reaches_the_operator_even_when_something_fatal_stops_the_probe(
        tmp_path, capsys):
    # An advisory is most often the explanation for a gap reported below it, so
    # withholding it until the fatal finding is fixed costs a round trip: the
    # author repairs the envelope, re-runs, and only then learns the rule they
    # wrote could never have matched.
    p = tmp_path / "type-map.json"
    p.write_text(json.dumps({
        "$schema": TYPE_MAP_SCHEMA_URL.replace("/type-map/", "/not-a-type-map/"),
        "read": CONNECTOR_READ + [CONNECTOR_READ[0]]}))
    with pytest.raises(ValueError, match=r"is not a valid type map"):
        G.resolve("read", ["citext"], [p])
    assert "duplicate rule" in capsys.readouterr().err


def test_a_crashed_check_is_not_reported_as_an_authoring_defect(tmp_path, monkeypatch):
    # a crash stops the probe for the same reason a defect does — nothing graded
    # the map — but telling the author to fix their map would send them after a
    # defect it does not have
    from analitiq.validator import connectors
    monkeypatch.setattr(connectors, "_type_map_rule_warnings",
                        lambda *a, **k: (_ for _ in ()).throw(TypeError("boom")))
    m = _map(tmp_path, "type-map.json", CONNECTOR_READ)
    with pytest.raises(ValueError, match=r"could not be graded") as exc:
        G.resolve("read", ["citext"], [m])
    assert "fix it" not in str(exc.value), exc.value
    assert "validator bug" in str(exc.value), exc.value


def test_cli_end_to_end(tmp_path, capsys):
    m = _map(tmp_path, "type-map.json", CONNECTOR_READ)
    probes = tmp_path / "probes.json"
    probes.write_text(json.dumps(["citext", "vector(3)"]))
    rc = G.main(["--direction", "read", "--map", str(m), "--probes-file", str(probes)])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out == {"direction": "read",
                   "resolved": {"citext": "Utf8", "vector(3)": None},
                   "gaps": ["vector(3)"]}


def test_cli_connection_map_beats_connector_map(tmp_path, capsys):
    # the documented invocation: both maps carry the probed section, so
    # precedence is argument order alone
    conn_dir, base_dir = tmp_path / "connection", tmp_path / "connector"
    conn_dir.mkdir()
    base_dir.mkdir()
    connection = _map(conn_dir, "type-map.json",
                      [{"match": "exact", "native_type": "CITEXT", "arrow_type": "LargeUtf8"}])
    connector = _map(base_dir, "type-map.json", CONNECTOR_READ)
    probes = tmp_path / "probes.json"
    probes.write_text('["citext"]')
    rc = G.main(["--direction", "read", "--map", str(connection), "--map", str(connector),
                 "--probes-file", str(probes)])
    assert rc == 0
    assert json.loads(capsys.readouterr().out)["resolved"] == {"citext": "LargeUtf8"}


@pytest.mark.parametrize("probes_payload", ['{"not": "a list"}', '["ok", 1]', "[ broken"])
def test_cli_rejects_bad_probes(tmp_path, capsys, probes_payload):
    m = _map(tmp_path, "type-map.json", CONNECTOR_READ)
    probes = tmp_path / "probes.json"
    probes.write_text(probes_payload)
    rc = G.main(["--direction", "read", "--map", str(m), "--probes-file", str(probes)])
    assert rc == 2
    assert json.loads(capsys.readouterr().out or "null") is None  # nothing on stdout


def test_cli_missing_map_names_the_file(tmp_path, capsys):
    # naming the file separates an unreadable map from a rejected one: both
    # exit 2 with nothing on stdout
    missing = tmp_path / "type-map.json"
    probes = tmp_path / "probes.json"
    probes.write_text('["citext"]')
    rc = G.main(["--direction", "read", "--map", str(missing), "--probes-file", str(probes)])
    assert rc == 2
    err = capsys.readouterr()
    assert not err.out
    assert str(missing) in err.err


# Text the parser refuses without raising a decode error: nesting deeper than
# it descends, and an integer longer than its digit limit.
_PARSER_REFUSALS = {
    "nested past the parser": "[" * 100_000 + "]" * 100_000,
    "integer past the digit limit": '{"n": 1' + "0" * 5_000 + "}",
}


@pytest.mark.parametrize("text", list(_PARSER_REFUSALS.values()), ids=list(_PARSER_REFUSALS))
@pytest.mark.parametrize("refused", ["map", "probes"])
def test_cli_rejects_input_the_parser_refuses(tmp_path, capsys, refused, text):
    # Unreadable input like any other, not a crash, and named like any other.
    m = _map(tmp_path, "type-map.json", CONNECTOR_READ)
    if refused == "map":
        m.write_text(text)
    probes = tmp_path / "probes.json"
    probes.write_text(text if refused == "probes" else '["citext"]')
    rc = G.main(["--direction", "read", "--map", str(m), "--probes-file", str(probes)])
    assert rc == 2
    err = capsys.readouterr()
    assert not err.out
    assert (str(m) if refused == "map" else "cannot read probes") in err.err


def test_duplicate_probes_deduped(tmp_path):
    result = G.resolve("read", ["vector(3)", "vector(3)"], [_map(tmp_path, "r.json", CONNECTOR_READ)])
    assert result["gaps"] == ["vector(3)"]
    assert list(result["resolved"]) == ["vector(3)"]


def test_malformed_rule_fails_loud(tmp_path):
    # the resolver mirrors runtime semantics and SKIPS a malformed rule — which
    # would surface as a false "gap" and drive the agent to shadow the rule the
    # map intended. The prober must therefore refuse the map outright, naming it.
    bad = _map(tmp_path, "r.json", [{"match": "exact", "native_type": "CITEXT"}])  # no arrow_type
    with pytest.raises(ValueError, match="r.json"):
        G.resolve("read", ["citext"], [bad])


def test_advisory_finding_does_not_block_probing(tmp_path):
    # a read pattern whose lowercase literal can never meet an uppercased
    # native is dead but well-formed: it costs no pass, so the map still
    # resolves and its uncovered probe is a gap, not a refusal.
    dead = {"match": "regex", "native_type": "^vector\\(\\d+\\)$", "arrow_type": "Utf8"}
    m = _map(tmp_path, "r.json", [*CONNECTOR_READ, dead])
    findings = type_map_findings(json.loads(m.read_text()))
    assert [f.get("rule") for f in findings] == ["RULE-TMAP-014"]
    assert not any(finding_costs_a_pass(f) for f in findings)

    result = G.resolve("read", ["citext", "vector(3)"], [m])
    assert result["resolved"] == {"citext": "Utf8", "vector(3)": None}
    assert result["gaps"] == ["vector(3)"]


def test_a_non_fatal_finding_reaches_stderr_beside_the_gap_it_explains(tmp_path, capsys):
    # A dead rule resolves nothing while costing no pass, so the probe it was
    # written to cover comes back a gap. Discarding the advisory leaves that gap
    # looking uncaused, and the authoring agent's next move is to write the rule
    # that is already there. stdout stays pure JSON: it is machine-read.
    dead = {"match": "regex", "native_type": "^vector\\(\\d+\\)$", "arrow_type": "Utf8"}
    m = _map(tmp_path, "type-map.json", [*CONNECTOR_READ, dead])
    probes = tmp_path / "probes.json"
    probes.write_text('["vector(3)"]')

    rc = G.main(["--direction", "read", "--map", str(m), "--probes-file", str(probes)])

    captured = capsys.readouterr()
    assert rc == 0
    assert json.loads(captured.out)["gaps"] == ["vector(3)"]
    assert "RULE-TMAP-014" not in captured.out
    assert str(m) in captured.err and "/read/" in captured.err


def test_connection_scoped_write_map_probes_without_the_full_vocabulary(tmp_path, monkeypatch):
    # a connection-scoped map fills the gaps its connector map leaves, so the
    # connector scope's write vocabulary is coverage it can never reach.
    gap_only = [{"match": "exact", "arrow_type": "Duration(SECOND)", "native_type": "INTERVAL"}]
    m = _map(tmp_path, "w.json", gap_only, "write")
    assert [f.get("rule") for f in
            type_map_findings(json.loads(m.read_text()), scope="connector")] \
        == ["RULE-TMAP-017"]

    # the scope the prober grades at is what keeps such a map probeable, not the
    # coverage finding's severity: hold every finding fatal and a map graded at
    # the connector scope is refused, while this one still resolves.
    monkeypatch.setattr("analitiq.validator.finding_costs_a_pass", lambda f: True)
    result = G.resolve("write", ["Duration(SECOND)", "Utf8"], [m])
    assert result["resolved"] == {"Duration(SECOND)": "INTERVAL", "Utf8": None}
    assert result["gaps"] == ["Utf8"]


def test_a_rule_is_graded_as_the_section_it_sits_under(tmp_path):
    # a write regex rule's arrow_type is a matcher pattern — invalid as a read
    # rule's rendered Arrow type — so write-shaped rules under `read` refuse the
    # map rather than being probed as read rules.
    with pytest.raises(ValueError, match="not a valid type map"):
        G.resolve("read", ["citext"], [_map(tmp_path, "m.json", CONNECTOR_WRITE, "read")])


def test_cli_probes_a_map_under_any_filename(tmp_path, capsys):
    # The prober reads the path it is handed; which files a directory's map is
    # read from is the loader's concern, not this one's.
    m = _map(tmp_path, "my-types.json", CONNECTOR_WRITE, "write")
    probes = tmp_path / "probes.json"
    probes.write_text('["Utf8"]')
    rc = G.main(["--direction", "write", "--map", str(m), "--probes-file", str(probes)])
    assert rc == 0
    assert json.loads(capsys.readouterr().out)["resolved"] == {"Utf8": "TEXT"}


def test_a_map_without_the_probed_section_contributes_no_rules(tmp_path, capsys):
    # A connection map is gap-only and may carry either direction alone. One
    # carrying only read rules must neither refuse a write probe nor lend it
    # rules keyed on native types: the connector map behind it answers alone.
    conn_dir, base_dir = tmp_path / "connection", tmp_path / "connector"
    conn_dir.mkdir()
    base_dir.mkdir()
    connection = _map(conn_dir, "type-map.json", CONNECTOR_READ)
    connector = _map(base_dir, "type-map.json", CONNECTOR_WRITE, "write")
    probes = tmp_path / "probes.json"
    probes.write_text('["Utf8", "Int64"]')
    rc = G.main(["--direction", "write", "--map", str(connection), "--map", str(connector),
                 "--probes-file", str(probes)])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["resolved"] == {"Utf8": "TEXT", "Int64": None}
    assert out["gaps"] == ["Int64"]


def test_cli_rejects_a_map_carrying_no_section(tmp_path, capsys):
    m = tmp_path / "type-map.json"
    m.write_text(json.dumps({"$schema": TYPE_MAP_SCHEMA_URL}))
    probes = tmp_path / "probes.json"
    probes.write_text('["citext"]')
    rc = G.main(["--direction", "read", "--map", str(m), "--probes-file", str(probes)])
    assert rc == 2
    err = capsys.readouterr()
    assert not err.out
    assert str(m) in err.err and "at least one of `read` or `write`" in err.err, err.err


def test_cli_refuses_a_chain_with_no_section_for_the_direction(tmp_path, capsys):
    # One map lacking the probed section is expected (a connection map is
    # gap-only); every map lacking it leaves nothing that could cover any
    # probe. Reporting every probe as a gap would send the author to cover the
    # whole vocabulary at connection scope for what is a connector defect or a
    # wrong --direction.
    m = _map(tmp_path, "type-map.json", CONNECTOR_READ)
    (tmp_path / "c").mkdir()
    c = _map(tmp_path / "c", "type-map.json", CONNECTOR_READ)
    probes = tmp_path / "probes.json"
    probes.write_text('["Utf8"]')
    rc = G.main(["--direction", "write", "--map", str(c), "--map", str(m),
                 "--probes-file", str(probes)])
    assert rc == 2
    err = capsys.readouterr()
    assert not err.out
    assert str(c) in err.err and str(m) in err.err and "'write'" in err.err, err.err


def test_cli_refuses_a_fallback_map_that_does_not_grade(tmp_path, capsys):
    # A fallback map is concatenated into the same rule list as the primary, so
    # one the grader refuses stops the probe as surely as a lone one.
    r = _map(tmp_path, "type-map.json", CONNECTOR_READ)
    (tmp_path / "w").mkdir()
    w = _map(tmp_path / "w", "type-map.json", CONNECTOR_WRITE, "read")
    probes = tmp_path / "probes.json"
    probes.write_text('["citext"]')
    rc = G.main(["--direction", "read", "--map", str(r), "--map", str(w),
                 "--probes-file", str(probes)])
    assert rc == 2
    err = capsys.readouterr()
    assert not err.out
    assert str(w) in err.err, err.err


@pytest.mark.parametrize("payload", ["null", "[]", '"read"'])
def test_cli_refuses_a_map_that_is_no_object(tmp_path, capsys, payload):
    m = tmp_path / "type-map.json"
    m.write_text(payload)
    probes = tmp_path / "probes.json"
    probes.write_text('["citext"]')
    rc = G.main(["--direction", "read", "--map", str(m), "--probes-file", str(probes)])
    assert rc == 2
    assert str(m) in capsys.readouterr().err


def test_cli_parse_error_names_the_file(tmp_path, capsys):
    bad = tmp_path / "type-map.json"
    bad.write_text("[ not json")
    probes = tmp_path / "probes.json"
    probes.write_text('["citext"]')
    rc = G.main(["--direction", "read", "--map", str(bad), "--probes-file", str(probes)])
    assert rc == 2
    assert str(bad) in capsys.readouterr().err


def test_cli_reads_probes_from_stdin(tmp_path, capsys, monkeypatch):
    # stdin is the documented primary invocation (spec-type-map-gaps.md)
    import io
    m = _map(tmp_path, "type-map.json", CONNECTOR_READ)
    monkeypatch.setattr("sys.stdin", io.StringIO('["citext"]'))
    rc = G.main(["--direction", "read", "--map", str(m)])
    assert rc == 0
    assert json.loads(capsys.readouterr().out)["resolved"] == {"citext": "Utf8"}
