"""The pin — the single place the vendored consumption manifest is stated.

The engine publishes ``contract-consumption``: for every contract model its
run-time path can hold, the fields that path reads. It is a fact about the
engine, so only the engine can make it true, and it is published the way the
Arrow type grammar is (``analitiq.contracts.arrow_grammar``):

    https://schemas.analitiq.ai/contract-consumption/latest.json            (pointer)
    https://schemas.analitiq.ai/contract-consumption/v{V}/contract_consumption.json

This module vendors ONE pinned, immutable version — the file beside it,
byte-identical to the published object — and names the pin (version + sha256)
once. Guards: ``tests/census/test_contract_consumption.py`` re-hashes the
vendored bytes against the pin and checks the document's self-declared
version, offline, so an edited or swapped copy fails in any plain pytest run;
the CI guard fetches the published object and byte-compares it against the
vendored copy, so a pin the engine has withdrawn or superseded is reported.

The envelope, and why each key is read the way it is:

- ``roots`` — the models the engine hands to its run-time path directly.
  Coverage is defined by them: a field belongs to the census only when its
  model is reachable from a root through field annotations, because that is
  the only way the engine could ever hold the object. A model no root
  reaches is not covered — unknown, not unread.
- ``claims`` — ``model -> field -> sites``: the attribute reads the engine's
  run-time path performs. A reachable field absent here is unread.
- ``opaque`` — models the engine consumes whole, as a JSON grammar
  (``model_dump`` into an expression tree or a predicate resolver). Their
  fields are never read by attribute, so they never appear in ``claims``,
  and the walk records the model but does not descend into it: a field
  under an opaque model is neither read nor unread.
- ``kit_reads`` — reads performed by the conformance kit, which grades a
  connector rather than running one. They are recorded so the engine can
  say what its kit looks at; they are never claims.
- ``transport`` — sites that re-serialise a document unchanged. Passing a
  field through is not reading it.

This module is stdlib-only: the pin and the envelope check are read by the
CI guard and by the census tests before any contract model is imported.

Updating the pin: replace the vendored file with the newly published object
and move ``CONSUMPTION_VERSION`` / ``CONSUMPTION_SHA256`` together (the sha is
``sha256`` of the published bytes; ``latest.json`` also states it), then run
``scripts/render_contract_consumption.py check`` and disposition whatever
the new manifest leaves unread.
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
#: Envelope keys, each read by name — never the document itself.
ROOTS_KEY = "roots"
CLAIMS_KEY = "claims"
OPAQUE_KEY = "opaque"
KIT_READS_KEY = "kit_reads"
TRANSPORT_KEY = "transport"

MANIFEST_PATH = Path(__file__).with_name(CONSUMPTION_FILENAME)


def load_manifest(path: Path = MANIFEST_PATH) -> dict[str, Any]:
    """The vendored manifest, parsed and envelope-checked.

    Refuses (``ValueError``) a document that cannot define a census: no
    non-empty ``roots`` list (nothing would be covered, and an empty census
    passes vacuously), or a ``claims`` / ``opaque`` / ``kit_reads`` that is
    not a mapping, or a ``version`` that is not a string (the self-declared
    version is what the pin is asserted against). Kept as a function so the
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
    if not isinstance(document.get(TRANSPORT_KEY, []), list):
        raise ValueError(f"{path.name}: `{TRANSPORT_KEY}` must be a list")
    return document
