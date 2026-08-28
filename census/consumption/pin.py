"""The pin — the single place the vendored consumption manifest is stated.

The engine publishes ``contract-consumption``: for every contract model its
run-time path can hold, the fields that path reads. It is a fact about the
engine, so only the engine can make it true, and it is published the way the
Arrow type grammar is (``analitiq.contracts.arrow_grammar``):

    {BASE_URL}/{CONSUMPTION_RESOURCE}/latest.json                          (pointer)
    {BASE_URL}/{CONSUMPTION_RESOURCE}/v{CONSUMPTION_VERSION}/{CONSUMPTION_FILENAME}

(``BASE_URL`` is the serving host ``scripts/_guard_lib.py`` states.)

This module vendors ONE pinned, immutable version — the file beside it,
byte-identical to the published object — and names the pin (version + sha256)
once. Guards: ``tests/census/test_contract_consumption.py`` re-hashes the
vendored bytes against the pin and checks the document's self-declared
version, offline, so an edited or swapped copy fails in any plain pytest run;
the CI guard fetches the published object and byte-compares it against the
vendored copy, so a withdrawn pin leaves the guard unable to run (its exit-2
verdict), and a superseded one surfaces as a notice.

The envelope, and why each key is read the way it is:

- ``version`` — the artifact's self-declaration, asserted against
  ``CONSUMPTION_VERSION`` so a consumer checks the version it got rather
  than trusting the URL it asked for.
- ``contract_models_version`` — the ``analitiq-contract-models`` release the
  engine generated the manifest against. Compared to the version in
  ``packages/contract-models/pyproject.toml``: it must be at or behind the
  tree, because a manifest generated against a newer models release can
  claim fields this tree does not declare. (``tests/census`` holds it;
  the envelope check only requires the key to be a string.)
- ``roots`` — the models the engine hands to its run-time path directly.
  Coverage is defined by them: a field belongs to the census only when its
  model is reachable from a root through field annotations, because that is
  the only way the engine could ever hold the object. A model no root
  reaches is not covered — unknown, not unread.
- ``claims`` — ``model -> field -> sites``: the attribute reads the engine's
  run-time path performs. A reachable field absent here, or present with
  no sites, is unread.
- ``opaque`` — ``model -> consumer record``: models the engine consumes
  whole, as a JSON grammar (``model_dump`` into an expression tree or a
  predicate resolver), each mapped to the record of what consumes it. Their
  fields are never read by attribute, so they never appear in ``claims``,
  and the walk records the model but does not descend into it: a field
  under an opaque model is neither read nor unread.
- ``kit_reads`` — reads performed by the conformance kit, which grades a
  connector rather than running one. They are recorded so the engine can
  say what its kit looks at; they are never claims.
- ``transport`` — sites that re-serialise a document unchanged. Passing a
  field through is not reading it. Optional: an envelope without it is
  accepted, since nothing here consults it.

Keys this module does not name are not read here.

This module is stdlib-only: the pin and the envelope check are read by the
CI guard and by the census tests before any contract model is imported.

Updating the pin is the pin-bump section of
``.claude/rules/reachability-dispositions.md``.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

CONSUMPTION_RESOURCE = "contract-consumption"
CONSUMPTION_VERSION = "0.3.0"
CONSUMPTION_SHA256 = (
    "7c5ef009ce65e74dddee3a54a121dac021482677bca7ccab572e45f29784deff"
)
CONSUMPTION_FILENAME = "contract_consumption.json"

#: Key the artifact stamps its own version under, so a consumer asserts the
#: version it got rather than trusting the URL it asked for.
ARTIFACT_VERSION_KEY = "version"
#: Key the artifact names the ``analitiq-contract-models`` release it was
#: generated against under.
CONTRACT_MODELS_VERSION_KEY = "contract_models_version"
#: Key the ``latest.json`` pointer states the sha256 of the object it names
#: under; the pin guard holds it to ``CONSUMPTION_SHA256`` when the pointer
#: names the pinned version.
POINTER_SHA256_KEY = "sha256"
#: Envelope keys, each read by name — never the document itself.
ROOTS_KEY = "roots"
CLAIMS_KEY = "claims"
OPAQUE_KEY = "opaque"
KIT_READS_KEY = "kit_reads"
TRANSPORT_KEY = "transport"

MANIFEST_PATH = Path(__file__).with_name(CONSUMPTION_FILENAME)


def load_manifest(path: Path = MANIFEST_PATH) -> dict[str, Any]:
    """The vendored manifest, parsed and envelope-checked.

    Refuses (``ValueError``) an envelope the walk cannot read — one that
    would define no census, or would make the walk look up a claim on a
    shape that is not ``model -> field -> sites``. Kept as a function so the
    guards re-read the file: hash checks compare bytes, not this parse.
    """
    document = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise ValueError(f"{path.name}: manifest is not a JSON object")
    version = document.get(ARTIFACT_VERSION_KEY)
    if not isinstance(version, str):
        raise ValueError(
            f"{path.name}: `{ARTIFACT_VERSION_KEY}` must be a string, got {version!r}"
        )
    generated_against = document.get(CONTRACT_MODELS_VERSION_KEY)
    if not isinstance(generated_against, str):
        raise ValueError(
            f"{path.name}: `{CONTRACT_MODELS_VERSION_KEY}` must be a string, "
            f"got {generated_against!r}"
        )
    roots = document.get(ROOTS_KEY)
    if not isinstance(roots, list) or not roots:
        raise ValueError(
            f"{path.name}: `{ROOTS_KEY}` must be a non-empty list — with no roots "
            "nothing is covered and the census passes vacuously"
        )
    if not all(isinstance(root, str) for root in roots):
        raise ValueError(f"{path.name}: every `{ROOTS_KEY}` entry must be a string")
    for key in (CLAIMS_KEY, OPAQUE_KEY, KIT_READS_KEY):
        if not isinstance(document.get(key), dict):
            raise ValueError(f"{path.name}: `{key}` must be a mapping")
    for model, fields in document[CLAIMS_KEY].items():
        if not isinstance(fields, dict) or not all(
            isinstance(sites, list) for sites in fields.values()
        ):
            raise ValueError(
                f"{path.name}: `{CLAIMS_KEY}` entry {model!r} must map each field "
                "to a list of sites"
            )
    if not isinstance(document.get(TRANSPORT_KEY, []), list):
        raise ValueError(f"{path.name}: `{TRANSPORT_KEY}` must be a list")
    return document
