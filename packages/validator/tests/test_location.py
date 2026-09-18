"""A document read from memory is graded exactly as the same files on disk.

Every cross-file check reads a document's siblings through a `Location`, so one
implementation serves both trees. These cases hold the two trees to the same
answer: each layout is written to disk and validated by path, then handed over
as text keyed by package-relative path and validated from memory, and the two
finding lists must agree entry for entry, order included.

Only layouts a key→text map can hold appear here — regular files, and the
directories their keys imply. A FIFO, a dangling link or an empty directory has
no in-memory spelling, so the disk tree's answers for those stay graded where
they are, in `test_validation.py`.
"""
from __future__ import annotations

import json
from pathlib import Path, PurePosixPath

import pytest

from analitiq.contracts.endpoint_identity import build_database_object, derive_db_endpoint_id
from analitiq.validator._core import _passed
from analitiq.validator._location import DISK, Location, MemoryTree

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


def _map(direction: str) -> dict:
    return {"$schema": f"{_H}/type-map-{direction}/latest.json", "direction": direction,
            "rules": [{"match": "exact", "native_type": "STRING", "arrow_type": "Utf8"}]}


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
    "type-map-read.json": _map("read"),
    "endpoints/v1__records.json": _endpoint("v1__records"),
}

#: `(entry key, package)` — the document validated, and every file beside it.
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
    "api package, read map missing": ("connector.json", {
        "connector.json": _API, "endpoints/v1__records.json": _endpoint("v1__records")}),
    "api package, read map unparseable": ("connector.json", {
        **_API_PACKAGE, "type-map-read.json": "{not json"}),
    # Text-mode reads turn `\r\n` and a lone `\r` into `\n`, and a parse error
    # quotes offsets into the text it was handed.
    "api package, read map unparseable with CRLF endings": ("connector.json", {
        **_API_PACKAGE, "type-map-read.json": '{\r\n  "direction": "read",\r\n  "rules": [,]\r\n}'}),
    "api package, read map unparseable with lone CR endings": ("connector.json", {
        **_API_PACKAGE, "type-map-read.json": '{\r  "direction": "read",\r  "rules": [,]\r}'}),
    "api package, write map beside the read map": ("connector.json", {
        **_API_PACKAGE, "type-map-write.json": _map("write")}),
    "api package, legacy type-map name": ("connector.json", {
        **_API_PACKAGE, "type-map.json": _map("read")}),
    "api package, no endpoints": ("connector.json", {
        "connector.json": _API, "type-map-read.json": _map("read")}),
    "api package, endpoints holding no json": ("connector.json", {
        "connector.json": _API, "type-map-read.json": _map("read"),
        "endpoints/README.md": "notes"}),
    "api package, a file named endpoints": ("connector.json", {
        "connector.json": _API, "type-map-read.json": _map("read"), "endpoints": "notes"}),
    "api package, legacy type-map name on a directory": ("connector.json", {
        **_API_PACKAGE, "type-map.json/x.json": _map("read")}),
    "api package, type-map name on a directory": ("connector.json", {
        **_API_PACKAGE, "type-map-extra.json/x.json": _map("read")}),
    # Every other layout sits at the root, where a key's name and the key are
    # the same string; below it they are not.
    "api package below the root": ("connectors/c/definition/connector.json", {
        f"connectors/c/definition/{key}": doc for key, doc in _API_PACKAGE.items()}),
    "api package, endpoint unparseable": ("connector.json", {
        **_API_PACKAGE, "endpoints/v2__broken.json": "{not json"}),
    "api package, directory under an endpoint name": ("connector.json", {
        **_API_PACKAGE, "endpoints/x.json/y.json": _endpoint("y")}),
    "database package, both maps": ("connector.json", {
        "connector.json": _corpus("valid_connector_sync_driver.json"),
        "type-map-read.json": _map("read"),
        "type-map-write.json": _map("write"),
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
    # Not under `endpoints/`, so there is no package to look for a connector in,
    # though one sits beside it.
    "api endpoint at the package root": ("thing.json", {
        "connector.json": {**_API, "transports": {"api": {}}},
        "thing.json": _endpoint("thing", transport_ref="api"),
    }),
    "database endpoint at its stem-addressed home, misnamed": (
        "connections/c/definition/endpoints/wrong.json", {
            "connections/c/definition/endpoints/wrong.json": _db_endpoint(),
        }),
}


@pytest.mark.parametrize("entry, package", LAYOUTS.values(), ids=LAYOUTS.keys())
def test_memory_tree_grades_as_the_same_files_on_disk(validator, tmp_path, entry, package):
    texts = {key: _text(doc) for key, doc in package.items()}
    # The package sits one level down so that a lookup reaching above its root
    # lands in a directory holding nothing but the package.
    root = tmp_path / "pkg"
    for key, text in texts.items():
        path = root / key
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    document = package[entry]

    on_disk = validator.validate_document(document, doc_path=root / entry)
    in_memory = validator.validate_document(
        document, doc_path=Location(PurePosixPath(entry), MemoryTree(texts)))

    # A disk finding naming a sibling by its full path names it under the
    # package root; the same sibling in memory is named by its key.
    anchored = json.dumps(on_disk).replace(f"{root}/", "")
    assert json.loads(anchored) == in_memory


def test_a_covered_package_passes_from_memory(validator):
    entry, package = LAYOUTS["api package, covered"]
    tree = MemoryTree({key: _text(doc) for key, doc in package.items()})
    findings = validator.validate_document(
        package[entry], doc_path=Location(PurePosixPath(entry), tree))
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
    """pathlib crosses directories on `/` and `**`, and the memory tree matches
    names alone, so the two trees would answer such a pattern differently."""
    for root in (Location(tmp_path, DISK), Location(PurePosixPath("."), MemoryTree({"a/z.json": ""}))):
        with pytest.raises(ValueError, match="single name"):
            list(getattr(root, walk)(pattern))
