"""A document on disk is graded in the layout its path spells, and the disk
tree answers a refused or missing lookup as the kernel does.
"""
from __future__ import annotations

import errno
import json
import os
from pathlib import Path

import pytest

from analitiq.validator._core import _passed
from analitiq.validator._location import DISK, Location, located

CORPUS = Path(__file__).resolve().parent / "corpus"
_H = "https://schemas.analitiq.ai"


def _corpus(name: str) -> dict:
    return json.loads((CORPUS / name).read_text())


def _endpoint(endpoint_id: str, native: str = "STRING", arrow: str = "Utf8",
              transport_ref: str | None = None) -> dict:
    doc = _corpus("valid_read.json")
    doc["endpoint_id"] = endpoint_id
    # The path the id derives from, so the id itself is never the defect.
    doc["operations"]["read"]["request"]["path"] = "/" + endpoint_id.replace("__", "/")
    if transport_ref is not None:
        doc["operations"]["read"]["request"]["transport_ref"] = transport_ref
    doc["operations"]["read"]["response"]["schema"]["items"]["properties"] = {
        "a": {"type": "string", "native_type": native, "arrow_type": arrow}}
    return doc


_RULES = [{"match": "exact", "native_type": "STRING", "arrow_type": "Utf8"}]


def _map(*directions: str) -> dict:
    return {"$schema": f"{_H}/type-map/latest.json", **{d: _RULES for d in directions}}


def _text(doc) -> str:
    return doc if isinstance(doc, str) else json.dumps(doc)


_API = _corpus("valid_connector.json")
_API_PACKAGE = {
    "connector.json": _API,
    "type-map.json": _map("read"),
    "endpoints/v1__records.json": _endpoint("v1__records"),
}


def _write(root: Path, package: dict) -> None:
    for key, doc in package.items():
        path = root / key
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(_text(doc))


@pytest.mark.parametrize("pattern", ["a/*.json", "**/*.json", "**"])
@pytest.mark.parametrize("walk", ["glob", "rglob"])
def test_a_pattern_crossing_names_is_refused(tmp_path, pattern, walk):
    """A walk matches one name at a time, so such a pattern would quietly
    match nothing, or as `*`."""
    with pytest.raises(ValueError, match="single name"):
        list(getattr(Location(tmp_path, DISK), walk)(pattern))


@pytest.mark.parametrize("key", ["missing", "file.json/below", "loop"],
                         ids=["no entry", "under a file", "a link loop"])
def test_a_key_carrying_nothing_answers_false(tmp_path, key):
    (tmp_path / "file.json").write_text("")
    (tmp_path / "loop").symlink_to(tmp_path / "loop")
    assert (DISK.is_file(tmp_path / key), DISK.is_dir(tmp_path / key)) == (False, False)


@pytest.mark.parametrize("name", ["missing", "file.json"], ids=["no entry", "a file"])
@pytest.mark.parametrize("walk", ["glob", "rglob"])
def test_a_key_that_is_no_directory_lists_nothing(tmp_path, name, walk):
    (tmp_path / "file.json").write_text("")
    assert list(getattr(Location(tmp_path / name, DISK), walk)("*")) == []


@pytest.mark.parametrize("ask", ["is_file", "is_dir", "glob", "rglob"])
def test_a_refused_lookup_raises_rather_than_answering_absent(tmp_path, monkeypatch, refuse, ask):
    """A caller reads an answer of absent as the entry not being there. pathlib
    is made to swallow the refusal, as some interpreters' does, so the answer
    cannot come from it on any interpreter."""
    monkeypatch.setattr(Path, "is_file", lambda self, **_: False)
    monkeypatch.setattr(Path, "is_dir", lambda self, **_: False)
    (tmp_path / "shut/inside").mkdir(parents=True)
    refuse(tmp_path / "shut", 0o000)
    with pytest.raises(PermissionError):
        answer = getattr(DISK, ask)(*((tmp_path / "shut/inside",) if ask.startswith("is_")
                                     else (tmp_path / "shut/inside", "*")))
        list(answer)


@pytest.mark.parametrize("walk", ["glob", "rglob"])
@pytest.mark.parametrize("shut", [".", "below"], ids=["the directory", "a directory below it"])
def test_a_directory_that_cannot_be_listed_raises_rather_than_listing_empty(tmp_path, refuse, walk, shut):
    """`glob` lists only the directory itself, so a refusal below it is not its to raise."""
    (tmp_path / "below").mkdir()
    refuse(tmp_path / shut, 0o300)
    walked = getattr(Location(tmp_path, DISK), walk)
    if walk == "glob" and shut == "below":
        assert sorted(entry.name for entry in walked("*")) == ["below"]
    else:
        with pytest.raises(PermissionError):
            list(walked("*"))


class _UnclassifiedScan:
    """`os.scandir` on a filesystem that reports no entry types, where telling
    whether `refused` is a directory is a lookup the kernel refuses."""

    def __init__(self, entries, refused: str) -> None:
        self._entries, self._refused = entries, refused

    def __enter__(self):
        return self

    def __exit__(self, *exc) -> None:
        self._entries.close()

    def __iter__(self):
        return self

    def __next__(self):
        entry = next(self._entries)
        return _Unclassified(entry) if entry.name == self._refused else entry

    def close(self) -> None:
        self._entries.close()


class _Unclassified:
    def __init__(self, entry) -> None:
        self._entry = entry

    def __getattr__(self, name):
        return getattr(self._entry, name)

    def is_dir(self, *, follow_symlinks: bool = True) -> bool:
        raise PermissionError(errno.EACCES, "Permission denied", self._entry.path)


def test_an_entry_the_walk_cannot_classify_raises_rather_than_being_skipped(tmp_path, monkeypatch):
    (tmp_path / "below").mkdir()
    (tmp_path / "below/x.json").write_text("{}")
    scandir = os.scandir
    monkeypatch.setattr(os, "scandir", lambda path: _UnclassifiedScan(scandir(path), "below"))
    with pytest.raises(PermissionError):
        list(Location(tmp_path, DISK).rglob("*"))


# ---------------------------------------------------------------------------
# A `Path` becomes a location in the layout it spells. A link stands where the
# link is, and a `..` is collapsed against the names written before it, unless
# a read of the path steps up somewhere else. Links are relative, as a checkout
# holds them.
# ---------------------------------------------------------------------------

_UNDECLARED = {**_API, "transports": {"other": _API["transports"]["api"]}}


def _endpoint_047(findings: list[dict]) -> list[tuple[str, str]]:
    return [(f["kind"], f["message_id"]) for f in findings if f.get("rule") == "RULE-ENDP-047"]


def _about_the_connector(findings: list[dict]) -> list[dict]:
    """What an endpoint's findings say about reading its connector: whether it
    was read, and what `transport_ref` could be checked against."""
    return [f for f in findings if f["message_id"].startswith(("sibling-connector", "transport-ref"))]


def _assert_cli_agrees(cli, expected: Path, got: Path) -> None:
    """The CLI on `got` answers exactly as on `expected`, a package that passes."""
    want, have = cli.run("--document", str(expected)), cli.run("--document", str(got))
    assert json.loads(want.stdout)["passed"], want.stdout
    assert (have.returncode, json.loads(have.stdout)) == (want.returncode, json.loads(want.stdout))


def test_a_linked_endpoint_is_graded_against_the_connector_beside_the_link(tmp_path, validator):
    doc = _endpoint("thing", transport_ref="api")
    _write(tmp_path, {"pkg/connector.json": _UNDECLARED, "shared/endpoints/thing.json": doc,
                      "shared/connector.json": _API})
    (tmp_path / "pkg/endpoints").mkdir()
    (tmp_path / "pkg/endpoints/thing.json").symlink_to("../../shared/endpoints/thing.json")

    findings = validator.validate_document(doc, doc_path=tmp_path / "pkg/endpoints/thing.json")

    assert ("fail", "transport-ref-undeclared") in _endpoint_047(findings), findings


def test_the_cli_grades_a_linked_connector_with_the_package_beside_the_link(tmp_path, validator_cli):
    _write(tmp_path / "regular", _API_PACKAGE)
    _write(tmp_path / "linked", {k: v for k, v in _API_PACKAGE.items() if k != "connector.json"})
    _write(tmp_path / "shared", {"connector.json": _API})
    (tmp_path / "linked/connector.json").symlink_to("../shared/connector.json")

    _assert_cli_agrees(validator_cli, tmp_path / "regular/connector.json",
                       tmp_path / "linked/connector.json")


def _split(cli, given: Path) -> list[str]:
    """What the CLI reports on `given`, which `located` refuses."""
    result = cli.run("--document", str(given))
    with pytest.raises(ValueError):
        located(given)
    return [f["message_id"] for f in json.loads(result.stdout)["findings"]]


@pytest.mark.parametrize("links, given", [
    ({"spelled/link": "../real/endpoints"}, "spelled/link/../connector.json"),
    ({"spelled/link": "hop", "spelled/hop": "../real/endpoints"}, "spelled/link/../connector.json"),
    ({"spelled/endpoints": "../real/endpoints", "real/endpoints/inner": "../b"},
     "spelled/endpoints/inner/../connector.json"),
    ({"real/b/up": ".."}, "real/b/up/../real/connector.json"),
    ({"spelled/link": "../real/endpoints"}, "spelled/link/../b/x/../connector.json"),
], ids=["one link", "a link to a link", "a link inside a linked directory", "a link to its parent",
        "a name only the link's side holds"])
def test_a_dotdot_out_of_a_link_to_elsewhere_is_refused(tmp_path, validator_cli, links, given):
    """A read of `link/..` opens the parent of where the link leads. Collapsed
    against the names written instead, one package's document would be graded
    against another package's siblings."""
    _write(tmp_path / "real", _API_PACKAGE)
    (tmp_path / "real/b/x").mkdir(parents=True)
    _write(tmp_path / "spelled", {"connector.json": _UNDECLARED})
    for name, target in links.items():
        (tmp_path / name).symlink_to(target)

    assert _split(validator_cli, tmp_path / given) == ["unreadable-document"]


_ENTRIES = {
    "validate_document": lambda v, where: v.validate_document(_API, doc_path=where / "connector.json"),
    "check_coverage": lambda v, where: v.check_coverage(_API, where / "connector.json"),
    "load_type_map": lambda v, where: v.load_type_map(where, rule=None),
}


@pytest.mark.parametrize("given, refusal", [
    ("spelled/link/..", ValueError), ("spelled/typo/..", FileNotFoundError),
], ids=["out of a link to elsewhere", "out of a missing name"])
@pytest.mark.parametrize("entry", _ENTRIES.values(), ids=_ENTRIES.keys())
def test_a_path_located_refuses_is_raised_by_every_entry_taking_one(
        tmp_path, validator, entry, given, refusal):
    """A refused path has no location to grade, so no finding can stand for
    it; and a finding would be the crash guard's, which reports a validator
    bug."""
    _write(tmp_path / "real", _API_PACKAGE)
    _write(tmp_path / "spelled", _API_PACKAGE)
    (tmp_path / "spelled/link").symlink_to("../real/endpoints")

    with pytest.raises(refusal):
        entry(validator, tmp_path / given)


def test_a_dotdot_is_stepped_up_before_the_layout_is_read(tmp_path, validator):
    """Left in, `sub/..` makes the parent's name `..`, and the endpoint is no
    longer seen at its `endpoints/` home. `sub` is no link, so stepping up out
    of it leaves the linked `endpoints/` above it standing where it is."""
    doc = _endpoint("thing", transport_ref="api")
    _write(tmp_path, {"pkg/connector.json": _UNDECLARED, "shared/connector.json": _API,
                      "shared/endpoints/thing.json": doc})
    (tmp_path / "shared/endpoints/sub").mkdir()
    (tmp_path / "pkg/endpoints").symlink_to("../shared/endpoints")

    findings = validator.validate_document(doc, doc_path=tmp_path / "pkg/endpoints/sub/../thing.json")

    assert ("fail", "transport-ref-undeclared") in _endpoint_047(findings), findings


@pytest.mark.parametrize("given, remedy", [
    (None, "no path was given, so there is no directory to read the connector from. "
           "Validate it at its path, `endpoints/{endpoint_id}.json` beside its connector"),
    ("pkg/endpoints/thing.json", "there is no connector.json file beside the `endpoints/` "
                                 "directory holding this document. Place the connector there and re-run"),
    ("pkg/thing.json", "the document is not directly inside an `endpoints/` directory, so no "
                       "connector is read for it. Place it at `endpoints/{endpoint_id}.json` "
                       "beside its connector"),
], ids=["no path", "no connector beside endpoints", "outside an endpoints directory"])
def test_an_endpoint_with_no_connector_read_is_told_why(tmp_path, validator, given, remedy):
    """The connector lookup is for `endpoints/{id}.json` one level below its
    connector, so a connector two levels up from anywhere else is not this
    endpoint's. Each reason no connector is read has its own remedy, and a
    swapped one sends the author to the wrong fix."""
    doc = _endpoint("thing", transport_ref="api")
    _write(tmp_path, {"connector.json": _UNDECLARED})

    findings = validator.validate_document(doc, doc_path=given and tmp_path / given)

    assert [(f["kind"], f["message_id"], f["message"]) for f in _about_the_connector(findings)] == [
        ("notApplicable", "transport-ref-check-skipped-no-sibling",
         f"transport_ref ['api'] not checked: {remedy}.")], findings


@pytest.mark.parametrize("shut,mode,remedy", [
    ("pkg", 0o000, "Make every directory on the path to it searchable"),
    ("pkg", 0o400, "Make every directory on the path to it searchable"),
    ("pkg/connector.json", 0o000, "Make it readable"),
], ids=["no access", "listable only", "connector unreadable"])
def test_an_endpoint_whose_connector_is_refused_is_told_why(tmp_path, validator, refuse, shut, mode, remedy):
    """A refused lookup or read of the connector is not its absence, nor a
    parse error: either remedy would send the author to the wrong fix."""
    doc = _endpoint("thing", transport_ref="api")
    _write(tmp_path, {"pkg/connector.json": _API})
    refuse(tmp_path / shut, mode)
    findings = validator.validate_document(doc, doc_path=tmp_path / "pkg/endpoints/thing.json")
    sibling = _about_the_connector(findings)
    assert [(f["kind"], f.get("rule"), f["message_id"]) for f in sibling] == [
        ("fail", None, "sibling-connector-unreadable"),
        ("notApplicable", "RULE-ENDP-047", "transport-ref-check-skipped-sibling-unreadable")], findings
    assert str(tmp_path / "pkg/connector.json") in sibling[1]["message"]
    assert f"{remedy}, then re-run." in sibling[0]["message"]


def test_a_dotdot_out_of_a_link_landing_where_its_names_spell_is_collapsed(tmp_path, validator):
    """`inner/..` lands back in the linked `endpoints/`, so the path is graded
    as spelled. Resolving it instead would carry that `endpoints/` to its
    target, out of the package holding it."""
    doc = _endpoint("thing", transport_ref="api")
    _write(tmp_path, {"pkg/connector.json": _UNDECLARED, "shared/connector.json": _API,
                      "shared/endpoints/thing.json": doc, "shared/endpoints/real/README.md": ""})
    (tmp_path / "pkg/endpoints").symlink_to("../shared/endpoints")
    (tmp_path / "shared/endpoints/inner").symlink_to("real")

    findings = validator.validate_document(doc, doc_path=tmp_path / "pkg/endpoints/inner/../thing.json")

    assert ("fail", "transport-ref-undeclared") in _endpoint_047(findings), findings


def _refusals(cli, given: Path) -> tuple[list[str], int]:
    """What the CLI reports on `given`, and the errno `located` raises on it."""
    result = cli.run("--document", str(given))
    with pytest.raises(OSError) as refused:
        located(given)
    return [f["message_id"] for f in json.loads(result.stdout)["findings"]], refused.value.errno


def _chain(where: Path, stem: str, hops: int, target: str) -> None:
    """`stem0` -> `stem1` -> ... -> `target`, `hops` links in all."""
    for i in range(hops):
        (where / f"{stem}{i}").symlink_to(f"{stem}{i + 1}" if i + 1 < hops else target)


@pytest.mark.parametrize("links, given", [
    ({"loop": "loop", "via": "loop/.."}, "via/../connector.json"),
    ({"a": "b/..", "b": "a/.."}, "a/../connector.json"),
], ids=["a loop behind a link", "links stepping out of each other"])
def test_a_link_loop_before_a_dotdot_is_unreadable(tmp_path, validator_cli, links, given):
    """Followed without a bound, a loop would never be stepped out of."""
    _write(tmp_path / "pkg", _API_PACKAGE)
    for name, target in links.items():
        (tmp_path / "pkg" / name).symlink_to(target)

    assert _refusals(validator_cli, tmp_path / "pkg" / given) == (
        ["unreadable-document"], errno.ELOOP)


@pytest.mark.parametrize("left, code", [
    ("typo", errno.ENOENT), ("connector.json", errno.ENOTDIR), ("dangling", errno.ENOENT),
    ("filelink", errno.ENOTDIR), ("connector.json/x", errno.ENOTDIR), ("loop/x", errno.ELOOP),
    ("endpoints/../typo", errno.ENOENT),
])
def test_a_dotdot_the_kernel_cannot_take_is_refused_as_the_kernel_refuses_it(
        tmp_path, validator_cli, left, code):
    """Stepping up out of a name needs every name up to it to lead to a
    directory, so there is no file there to grade, and why is the kernel's to
    say."""
    _write(tmp_path / "pkg", _API_PACKAGE)
    (tmp_path / "pkg/dangling").symlink_to("gone")
    (tmp_path / "pkg/filelink").symlink_to("type-map.json")
    (tmp_path / "pkg/loop").symlink_to("loop")

    assert _refusals(validator_cli, tmp_path / f"pkg/{left}/../connector.json") == (
        ["unreadable-document"], code)


@pytest.mark.skipif(os.geteuid() == 0, reason="root searches any directory")
def test_a_dotdot_out_of_a_directory_that_cannot_be_searched_is_refused(tmp_path, validator_cli):
    _write(tmp_path / "pkg", _API_PACKAGE)
    (tmp_path / "pkg/sealed").mkdir(mode=0o600)

    assert _refusals(validator_cli, tmp_path / "pkg/sealed/../connector.json") == (
        ["unreadable-document"], errno.EACCES)


@pytest.mark.parametrize("chains, given", [
    ({"c": (30, "pkg"), "pkg/a": (15, "sub")}, "c0/a0/../connector.json"),
    ({"h": (25, "pkg"), "c": (25, "pkg")}, "h0/../c0/connector.json"),
], ids=["all before the dotdot", "some after the dotdot"])
def test_more_links_than_one_lookup_follows_are_unreadable(tmp_path, validator_cli, chains, given):
    """The kernel bounds every link one lookup follows, whether a `..` steps
    out of it or not."""
    _write(tmp_path / "pkg", _API_PACKAGE)
    (tmp_path / "pkg/sub").mkdir()
    for where, (hops, target) in chains.items():
        stem = Path(where)
        _chain(tmp_path / stem.parent, stem.name, hops, target)

    result = validator_cli.run("--document", str(tmp_path / given))

    assert [f["message_id"] for f in json.loads(result.stdout)["findings"]] == [
        "unreadable-document"], result.stdout


def test_a_linked_directory_under_endpoints_is_not_walked(tmp_path, validator):
    """Through a loop of links, every file below it would be reported again at
    each turn."""
    _write(tmp_path / "pkg", _API_PACKAGE)
    (tmp_path / "pkg/endpoints/sub").symlink_to(".")

    findings = validator.validate_document(_API, doc_path=tmp_path / "pkg/connector.json")

    assert _passed(findings), findings


# ---------------------------------------------------------------------------
# A file is read as the exact UTF-8 text it holds. Neither the host's locale
# encoding nor its newline convention reaches the parser, so a document on disk
# is parsed as the same text an in-memory caller hands over.
# ---------------------------------------------------------------------------

def _write_utf8(root: Path, package: dict) -> None:
    for key, doc in package.items():
        path = root / key
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(json.dumps(doc, ensure_ascii=False).encode("utf-8"))


def test_a_package_is_read_as_utf8_whatever_the_host_locale(tmp_path, validator_cli, ascii_locale_env):
    """The document and the sibling type map each carry text outside ASCII,
    so a read decoding either with the locale refuses the package."""
    native = "CARACTÈRE"
    _write_utf8(tmp_path, {
        "connector.json": {**_API, "description": "Café records"},
        "type-map.json": {"$schema": f"{_H}/type-map/latest.json",
                          "read": [{"match": "exact", "native_type": native, "arrow_type": "Utf8"}]},
        "endpoints/v1__records.json": _endpoint("v1__records", native=native),
    })
    document = str(tmp_path / "connector.json")

    want = validator_cli.run("--document", document)
    have = validator_cli.run("--document", document, env_overrides=ascii_locale_env)

    assert json.loads(want.stdout)["passed"], want.stdout
    assert (have.returncode, json.loads(have.stdout)) == (want.returncode, json.loads(want.stdout))


_UNPARSEABLE_BY_NEWLINE = {
    "CRLF": '{\r\n  "$schema": "x",\r\n  "read": [,]\r\n}',
    "lone CR": '{\r  "$schema": "x",\r  "read": [,]\r}',
}


@pytest.mark.parametrize("text", _UNPARSEABLE_BY_NEWLINE.values(), ids=_UNPARSEABLE_BY_NEWLINE.keys())
def test_a_parse_error_quotes_offsets_into_the_text_as_written(tmp_path, validator, text):
    """Newlines translated before parsing would move every offset the error
    quotes off the file the author holds."""
    (tmp_path / validator.TYPE_MAP_FILENAME).write_bytes(text.encode("utf-8"))
    with pytest.raises(json.JSONDecodeError) as as_written:
        json.loads(text)

    [(_, found)] = validator.load_type_map(tmp_path, rule=None).findings

    assert found["message_id"] == "type-map-unparseable", found
    assert f"({as_written.value})" in found["message"], found


def test_a_byte_order_mark_is_content_not_encoding(tmp_path, validator):
    """An in-memory caller's document keeps its byte-order mark and is refused
    for it, so a disk read that stripped one would pass the same bytes."""
    (tmp_path / validator.TYPE_MAP_FILENAME).write_bytes(
        b"\xef\xbb\xbf" + json.dumps(_map("read")).encode("utf-8"))

    [(_, found)] = validator.load_type_map(tmp_path, rule=None).findings

    assert found["message_id"] == "type-map-unparseable", found
