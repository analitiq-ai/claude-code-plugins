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
from analitiq.contracts.type_map import (  # noqa: E402
    TYPE_MAP_READ_SCHEMA_URL, TYPE_MAP_WRITE_SCHEMA_URL,
)
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
    schema_url = TYPE_MAP_READ_SCHEMA_URL if direction == "read" else TYPE_MAP_WRITE_SCHEMA_URL
    return {"$schema": schema_url, "direction": direction, "rules": rules}


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
    result = G.resolve("read", ["citext"], [connection, connector])
    assert result["resolved"]["citext"] == "LargeUtf8"


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
    with pytest.raises(ValueError, match=r"is not a valid read type map") as exc:
        G.resolve("read", ["citext"], [bad])
    for field in ("/$schema", "/direction", "/rules"):
        assert field in str(exc.value), exc.value


def test_a_connection_write_map_is_not_held_to_a_connector_vocabulary(tmp_path, capsys):
    # A connection map fills the gaps its connector left, so holding it to the
    # whole Arrow vocabulary earns the coverage warning on every run. The
    # authoring agent is told to add rules for families the connector already
    # renders, and shadows them.
    G.resolve("write", ["Utf8"], [_map(tmp_path, "type-map-write.json", CONNECTOR_WRITE, "write")])
    assert capsys.readouterr().err == ""


def test_an_advisory_reaches_the_operator_over_a_map_that_resolves(tmp_path, capsys):
    # The gap and the rule that was meant to fill it are reported together: a
    # duplicate is unreachable, so the probe it was written for comes back
    # uncovered and reads as a vocabulary the connector simply lacks.
    p = tmp_path / "type-map-read.json"
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
    p = tmp_path / "type-map-read.json"
    p.write_text(json.dumps({
        "$schema": TYPE_MAP_WRITE_SCHEMA_URL,   # the write schema under a read declaration
        "direction": "read",
        "rules": CONNECTOR_READ + [CONNECTOR_READ[0]]}))
    with pytest.raises(ValueError, match=r"is not a valid read type map"):
        G.resolve("read", ["citext"], [p])
    assert "duplicate rule" in capsys.readouterr().err


def test_a_crashed_check_is_not_reported_as_an_authoring_defect(tmp_path, monkeypatch):
    # a crash stops the probe for the same reason a defect does — nothing graded
    # the map — but telling the author to fix their map would send them after a
    # defect it does not have
    from analitiq.validator import connectors
    monkeypatch.setattr(connectors, "_type_map_rule_warnings",
                        lambda *a, **k: (_ for _ in ()).throw(TypeError("boom")))
    m = _map(tmp_path, "type-map-read.json", CONNECTOR_READ)
    with pytest.raises(ValueError, match=r"could not be graded") as exc:
        G.resolve("read", ["citext"], [m])
    assert "fix it" not in str(exc.value), exc.value
    assert "validator bug" in str(exc.value), exc.value


def test_cli_end_to_end(tmp_path, capsys):
    m = _map(tmp_path, "type-map-read.json", CONNECTOR_READ)
    probes = tmp_path / "probes.json"
    probes.write_text(json.dumps(["citext", "vector(3)"]))
    rc = G.main(["--map", str(m), "--probes-file", str(probes)])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out == {"direction": "read",
                   "resolved": {"citext": "Utf8", "vector(3)": None},
                   "gaps": ["vector(3)"]}


def test_cli_connection_map_beats_connector_map(tmp_path, capsys):
    # the documented invocation: both maps declare the same direction, so
    # precedence is argument order alone
    conn_dir, base_dir = tmp_path / "connection", tmp_path / "connector"
    conn_dir.mkdir()
    base_dir.mkdir()
    connection = _map(conn_dir, "type-map-read.json",
                      [{"match": "exact", "native_type": "CITEXT", "arrow_type": "LargeUtf8"}])
    connector = _map(base_dir, "type-map-read.json", CONNECTOR_READ)
    probes = tmp_path / "probes.json"
    probes.write_text('["citext"]')
    rc = G.main(["--map", str(connection), "--map", str(connector),
                 "--probes-file", str(probes)])
    assert rc == 0
    assert json.loads(capsys.readouterr().out)["resolved"] == {"citext": "LargeUtf8"}


@pytest.mark.parametrize("probes_payload", ['{"not": "a list"}', '["ok", 1]', "[ broken"])
def test_cli_rejects_bad_probes(tmp_path, capsys, probes_payload):
    m = _map(tmp_path, "type-map-read.json", CONNECTOR_READ)
    probes = tmp_path / "probes.json"
    probes.write_text(probes_payload)
    rc = G.main(["--map", str(m), "--probes-file", str(probes)])
    assert rc == 2
    assert json.loads(capsys.readouterr().out or "null") is None  # nothing on stdout


def test_cli_missing_map_names_the_file(tmp_path, capsys):
    # naming the file separates an unreadable map from a rejected one: both
    # exit 2 with nothing on stdout
    missing = tmp_path / "type-map-read.json"
    probes = tmp_path / "probes.json"
    probes.write_text('["citext"]')
    rc = G.main(["--map", str(missing), "--probes-file", str(probes)])
    assert rc == 2
    err = capsys.readouterr()
    assert not err.out
    assert str(missing) in err.err


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
    findings = type_map_findings(json.loads(m.read_text()), "read")
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
    m = _map(tmp_path, "type-map-read.json", [*CONNECTOR_READ, dead])
    probes = tmp_path / "probes.json"
    probes.write_text('["vector(3)"]')

    rc = G.main(["--map", str(m), "--probes-file", str(probes)])

    captured = capsys.readouterr()
    assert rc == 0
    assert json.loads(captured.out)["gaps"] == ["vector(3)"]
    assert "RULE-TMAP-014" not in captured.out
    assert str(m) in captured.err and "/rules/" in captured.err


def test_connection_scoped_write_map_probes_without_the_full_vocabulary(tmp_path, monkeypatch):
    # a connection-scoped map fills the gaps its connector map leaves, so the
    # connector scope's write vocabulary is coverage it can never reach.
    gap_only = [{"match": "exact", "arrow_type": "Duration(SECOND)", "native_type": "INTERVAL"}]
    m = _map(tmp_path, "w.json", gap_only, "write")
    assert [f.get("rule") for f in
            type_map_findings(json.loads(m.read_text()), "write", scope="connector")] \
        == ["RULE-TMAP-017"]

    # the scope the prober grades at is what keeps such a map probeable, not the
    # coverage finding's severity: hold every finding fatal and a map graded at
    # the connector scope is refused, while this one still resolves.
    monkeypatch.setattr("analitiq.validator.finding_costs_a_pass", lambda f: True)
    result = G.resolve("write", ["Duration(SECOND)", "Utf8"], [m])
    assert result["resolved"] == {"Duration(SECOND)": "INTERVAL", "Utf8": None}
    assert result["gaps"] == ["Utf8"]


def test_map_direction_must_match_model(tmp_path):
    # a write regex rule's arrow_type is a matcher pattern — invalid as a read
    # rule's rendered Arrow type — so grading a map as the direction asked for
    # rejects write-shaped rules under a read envelope.
    with pytest.raises(ValueError, match="not a valid read type map"):
        G.resolve("read", ["citext"], [_map(tmp_path, "m.json", CONNECTOR_WRITE, "read")])


def test_cli_probes_a_map_under_any_filename(tmp_path, capsys):
    # The name says nothing about direction anywhere a map is consumed, so it
    # says nothing here: the document's own declaration is what is probed.
    m = _map(tmp_path, "my-types.json", CONNECTOR_READ)
    probes = tmp_path / "probes.json"
    probes.write_text('["citext"]')
    rc = G.main(["--map", str(m), "--probes-file", str(probes)])
    assert rc == 0
    assert json.loads(capsys.readouterr().out)["direction"] == "read"


def test_cli_probes_the_direction_the_envelope_declares(tmp_path, capsys):
    # A write map under the read direction's conventional name is probed as the
    # write map it declares. Probing it as read would not simply fail: each
    # direction keys on the member the other renders, so it would match the
    # wrong things and every verdict would be computed from rules that mean
    # something else.
    m = _map(tmp_path, "type-map-read.json", CONNECTOR_WRITE, "write")
    probes = tmp_path / "probes.json"
    probes.write_text('["Utf8"]')
    rc = G.main(["--map", str(m), "--probes-file", str(probes)])
    assert rc == 0
    assert json.loads(capsys.readouterr().out)["direction"] == "write"


def test_cli_rejects_a_map_declaring_no_usable_direction(tmp_path, capsys):
    # Nothing else says which vocabulary to probe, so probing cannot begin —
    # picking one would report gaps in a vocabulary the document never claimed.
    m = tmp_path / "type-map-read.json"
    m.write_text(json.dumps({**_tm_doc(CONNECTOR_READ, "read"), "direction": "sideways"}))
    probes = tmp_path / "probes.json"
    probes.write_text('["citext"]')
    rc = G.main(["--map", str(m), "--probes-file", str(probes)])
    assert rc == 2
    err = capsys.readouterr()
    assert not err.out
    assert "declares direction 'sideways'" in err.err


def test_cli_rejects_maps_holding_different_directions(tmp_path, capsys):
    r = _map(tmp_path, "type-map-read.json", CONNECTOR_READ)
    (tmp_path / "w").mkdir()
    w = _map(tmp_path / "w", "type-map-write.json", CONNECTOR_WRITE, "write")
    probes = tmp_path / "probes.json"
    probes.write_text('["citext"]')
    rc = G.main(["--map", str(r), "--map", str(w), "--probes-file", str(probes)])
    assert rc == 2
    err = capsys.readouterr()
    assert not err.out
    assert "same direction" in err.err
    # The message names one map per direction, and which one is the first to
    # declare it: a later map naming the same direction changes nothing about
    # which document the author is pointed at.
    assert str(r) in err.err and str(w) in err.err, err.err


def test_cli_parse_error_names_the_file(tmp_path, capsys):
    bad = tmp_path / "type-map-read.json"
    bad.write_text("[ not json")
    probes = tmp_path / "probes.json"
    probes.write_text('["citext"]')
    rc = G.main(["--map", str(bad), "--probes-file", str(probes)])
    assert rc == 2
    assert str(bad) in capsys.readouterr().err


def test_cli_reads_probes_from_stdin(tmp_path, capsys, monkeypatch):
    # stdin is the documented primary invocation (spec-type-map-gaps.md)
    import io
    m = _map(tmp_path, "type-map-read.json", CONNECTOR_READ)
    monkeypatch.setattr("sys.stdin", io.StringIO('["citext"]'))
    rc = G.main(["--map", str(m)])
    assert rc == 0
    assert json.loads(capsys.readouterr().out)["resolved"] == {"citext": "Utf8"}
