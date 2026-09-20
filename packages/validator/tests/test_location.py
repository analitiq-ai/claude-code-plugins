"""A document read from memory is graded exactly as the same files on disk, and
a document on disk is graded in the layout its path spells.

Every cross-file check reads a document's siblings through a `Location`, so one
implementation serves both trees. The equivalence cases hold the two trees to
the same answer: each layout is written to disk and validated by path, then
handed over as text keyed by package-relative path and validated from memory,
and the two finding lists must agree entry for entry, order included.

Only layouts a key→text map can hold appear in those — regular files, and the
directories their keys imply. A FIFO, a dangling link or an empty directory has
no in-memory spelling, so the disk tree's answers for those stay graded where
they are, in `test_validation.py`. Links are disk-only too, and are graded
below, where a `Path` becomes a location.
"""
from __future__ import annotations

import errno
import json
import os
from pathlib import Path, PurePosixPath

import pytest

from analitiq.contracts.endpoint_identity import build_database_object, derive_db_endpoint_id
from analitiq.validator._core import _passed
from analitiq.validator._location import DISK, Location, MemoryTree, located

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


def _db_endpoint() -> dict:
    return {
        "$schema": f"{_H}/database-endpoint/latest.json",
        "endpoint_id": derive_db_endpoint_id(None, "public", "orders"),
        "display_name": "public.orders",
        "database_object": build_database_object(None, "public", "orders"),
        "columns": [{"name": "id", "native_type": "bigint", "arrow_type": "Int64",
                     "nullable": False, "ordinal_position": 1}],
        "primary_keys": ["id"],
    }


def _text(doc) -> str:
    return doc if isinstance(doc, str) else json.dumps(doc)


_API = _corpus("valid_connector.json")
_API_PACKAGE = {
    "connector.json": _API,
    "type-map.json": _map("read"),
    "endpoints/v1__records.json": _endpoint("v1__records"),
}
_API_PACKAGE_WITHOUT_MAP = {k: v for k, v in _API_PACKAGE.items() if k != "type-map.json"}

#: `(entry key, package)` — the document validated, and every file beside it.
def _kind_of(entry: str) -> str:
    """The kind a package's entry document is submitted as, from the name it
    sits under — the same routing the package entry point does, so a layout
    table states its entry once instead of once per kind."""
    return "connector" if entry.endswith("connector.json") else "api-endpoint"


LAYOUTS = {
    "api package, covered": ("connector.json", _API_PACKAGE),
    # Path order compares parts, string order compares characters: `a-b.json`
    # sorts after the nested `a/z.json` as a path and before it as a string, so
    # these two findings come out in disk order only while memory keys sort as
    # paths do. `test_locations_sort_as_paths` holds that order to disk's.
    "api package, nested endpoint beside a hyphenated one": ("connector.json", {
        **_API_PACKAGE,
        "endpoints/a-b.json": _endpoint("a-b", native="BOOLEAN", arrow="Boolean"),
        "endpoints/a/z.json": _endpoint("z"),
    }),
    "api package, type map missing": ("connector.json", _API_PACKAGE_WITHOUT_MAP),
    "api package, type map unparseable": ("connector.json", {
        **_API_PACKAGE, "type-map.json": "{not json"}),
    # Text-mode reads turn `\r\n` and a lone `\r` into `\n`, and a parse error
    # quotes offsets into the text it was handed.
    "api package, type map unparseable with CRLF endings": ("connector.json", {
        **_API_PACKAGE, "type-map.json": '{\r\n  "read": [,]\r\n}'}),
    "api package, type map unparseable with lone CR endings": ("connector.json", {
        **_API_PACKAGE, "type-map.json": '{\r  "read": [,]\r}'}),
    "api package, write section in the map": ("connector.json", {
        **_API_PACKAGE, "type-map.json": _map("read", "write")}),
    "api package, stray type-map name": ("connector.json", {
        **_API_PACKAGE, "type-map-natives.json": _map("read")}),
    "api package, no endpoints": ("connector.json", {
        "connector.json": _API, "type-map.json": _map("read")}),
    "api package, endpoints holding no json": ("connector.json", {
        "connector.json": _API, "type-map.json": _map("read"),
        "endpoints/README.md": "notes"}),
    "api package, a file named endpoints": ("connector.json", {
        "connector.json": _API, "type-map.json": _map("read"), "endpoints": "notes"}),
    "api package, map name on a directory": ("connector.json", {
        **_API_PACKAGE_WITHOUT_MAP, "type-map.json/x.json": _map("read")}),
    "api package, stray type-map name on a directory": ("connector.json", {
        **_API_PACKAGE, "type-map-extra.json/x.json": _map("read")}),
    # Every other layout sits at the root, where a key's name and the key are
    # the same string; below it they are not.
    "api package below the root": ("connectors/c/definition/connector.json", {
        f"connectors/c/definition/{key}": doc for key, doc in _API_PACKAGE.items()}),
    "api package, endpoint unparseable": ("connector.json", {
        **_API_PACKAGE, "endpoints/v2__broken.json": "{not json"}),
    "api package, directory under an endpoint name": ("connector.json", {
        **_API_PACKAGE, "endpoints/x.json/y.json": _endpoint("y")}),
    "database package, both sections": ("connector.json", {
        "connector.json": _corpus("valid_connector_sync_driver.json"),
        "type-map.json": _map("read", "write"),
    }),
    "api endpoint, transport undeclared by its connector": ("endpoints/thing.json", {
        "connector.json": {**_API, "transports": {"other": {}}},
        "endpoints/thing.json": _endpoint("thing", transport_ref="api"),
    }),
    "api endpoint, connector unparseable": ("endpoints/thing.json", {
        "connector.json": "{",
        "endpoints/thing.json": _endpoint("thing", transport_ref="api"),
    }),
    "api endpoint, no connector": ("endpoints/thing.json", {
        "endpoints/thing.json": _endpoint("thing", transport_ref="api"),
    }),
    # Not under `endpoints/`, so the connector lookup does not run, though a
    # connector sits beside it.
    "api endpoint at the package root": ("thing.json", {
        "connector.json": {**_API, "transports": {"api": {}}},
        "thing.json": _endpoint("thing", transport_ref="api"),
    }),
    "database endpoint at its stem-addressed home, misnamed": (
        "connections/c/definition/endpoints/wrong.json", {
            "connections/c/definition/endpoints/wrong.json": _db_endpoint(),
        }),
}


def _write(root: Path, package: dict) -> None:
    for key, doc in package.items():
        path = root / key
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(_text(doc))


@pytest.mark.parametrize("entry, package", LAYOUTS.values(), ids=LAYOUTS.keys())
def test_memory_tree_grades_as_the_same_files_on_disk(validator, tmp_path, entry, package):
    texts = {key: _text(doc) for key, doc in package.items()}
    # One level down, so that a lookup climbing above the package root would
    # find a directory holding nothing but the package.
    root = tmp_path / "pkg"
    _write(root, package)
    document = package[entry]

    on_disk = validator.validate_document(document, _kind_of(entry), doc_path=root / entry)
    in_memory = validator.validate_document(
        document, _kind_of(entry),
        doc_path=Location(PurePosixPath(entry), MemoryTree(texts)))

    # A disk finding naming a sibling by its full path names it under the
    # package root; the same sibling in memory is named by its key.
    anchored = json.dumps(on_disk).replace(f"{root}/", "")
    assert json.loads(anchored) == in_memory


def test_a_covered_package_passes_from_memory(validator):
    entry, package = LAYOUTS["api package, covered"]
    tree = MemoryTree({key: _text(doc) for key, doc in package.items()})
    findings = validator.validate_document(
        package[entry], _kind_of(entry), doc_path=Location(PurePosixPath(entry), tree))
    assert _passed(findings), findings


def test_locations_sort_as_paths():
    """Both trees sort through `Location`, so comparing them cannot see this
    order change; comparing it with the order of the paths it wraps can."""
    keys = [PurePosixPath(k) for k in ("endpoints/a.json", "endpoints/a-b.json", "endpoints/a/z.json")]
    tree = MemoryTree({str(k): "" for k in keys})
    assert [loc.key for loc in sorted(Location(k, tree) for k in keys)] == sorted(keys)


@pytest.mark.parametrize("pattern", ["a/*.json", "**/*.json", "**"])
@pytest.mark.parametrize("walk", ["glob", "rglob"])
def test_a_pattern_crossing_names_is_refused(tmp_path, pattern, walk):
    """Both trees match one name at a time, so such a pattern would quietly
    match nothing, or as `*`."""
    for root in (Location(tmp_path, DISK), Location(PurePosixPath("."), MemoryTree({"a/z.json": ""}))):
        with pytest.raises(ValueError, match="single name"):
            list(getattr(root, walk)(pattern))


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
    for root in (Location(tmp_path / name, DISK), Location(PurePosixPath(name), MemoryTree({"file.json": ""}))):
        assert list(getattr(root, walk)("*")) == []


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
    def graded(path):
        return cli.run("--document", str(path), "--kind", _kind_of(path.name))

    want, have = graded(expected), graded(got)
    assert json.loads(want.stdout)["passed"], want.stdout
    assert (have.returncode, json.loads(have.stdout)) == (want.returncode, json.loads(want.stdout))


def test_a_linked_endpoint_is_graded_against_the_connector_beside_the_link(tmp_path, validator):
    doc = _endpoint("thing", transport_ref="api")
    _write(tmp_path, {"pkg/connector.json": _UNDECLARED, "shared/endpoints/thing.json": doc,
                      "shared/connector.json": _API})
    (tmp_path / "pkg/endpoints").mkdir()
    (tmp_path / "pkg/endpoints/thing.json").symlink_to("../../shared/endpoints/thing.json")

    findings = validator.validate_document(doc, "api-endpoint", doc_path=tmp_path / "pkg/endpoints/thing.json")

    assert ("fail", "transport-ref-undeclared") in _endpoint_047(findings), findings


def test_the_cli_grades_a_linked_connector_with_the_package_beside_the_link(tmp_path, validator_cli):
    _write(tmp_path / "regular", _API_PACKAGE)
    _write(tmp_path / "linked", {k: v for k, v in _API_PACKAGE.items() if k != "connector.json"})
    _write(tmp_path / "shared", {"connector.json": _API})
    (tmp_path / "linked/connector.json").symlink_to("../shared/connector.json")

    _assert_cli_agrees(validator_cli, tmp_path / "regular/connector.json",
                       tmp_path / "linked/connector.json")


def _split(cli, given: Path) -> list[str]:
    """What the CLI reports on `given`, which `located` refuses.

    The kind is required and immaterial: `given` is refused while being read,
    before anything grades it."""
    result = cli.run("--document", str(given), "--kind", _kind_of(given.name))
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
    "validate_document": lambda v, where: v.validate_document(_API, "connector", doc_path=where / "connector.json"),
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

    findings = validator.validate_document(
        doc, "api-endpoint", doc_path=tmp_path / "pkg/endpoints/sub/../thing.json")

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

    findings = validator.validate_document(doc, "api-endpoint", doc_path=given and tmp_path / given)

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
    findings = validator.validate_document(doc, "api-endpoint", doc_path=tmp_path / "pkg/endpoints/thing.json")
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

    findings = validator.validate_document(
        doc, "api-endpoint", doc_path=tmp_path / "pkg/endpoints/inner/../thing.json")

    assert ("fail", "transport-ref-undeclared") in _endpoint_047(findings), findings


def _refusals(cli, given: Path) -> tuple[list[str], int]:
    """What the CLI reports on `given`, and the errno `located` raises on it.

    The kind is required and immaterial: `given` is refused while being read,
    before anything grades it."""
    result = cli.run("--document", str(given), "--kind", _kind_of(given.name))
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

    result = validator_cli.run("--document", str(tmp_path / given),
                                 "--kind", _kind_of(Path(given).name))

    assert [f["message_id"] for f in json.loads(result.stdout)["findings"]] == [
        "unreadable-document"], result.stdout


def test_a_linked_directory_under_endpoints_is_not_walked(tmp_path, validator):
    """Through a loop of links, every file below it would be reported again at
    each turn."""
    _write(tmp_path / "pkg", _API_PACKAGE)
    (tmp_path / "pkg/endpoints/sub").symlink_to(".")

    findings = validator.validate_document(_API, "connector", doc_path=tmp_path / "pkg/connector.json")

    assert _passed(findings), findings
