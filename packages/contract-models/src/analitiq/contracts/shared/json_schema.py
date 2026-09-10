"""Reading an embedded JSON Schema: pointers, composition, declared-path queries.

**What lives here.** Resolving an RFC 6901 pointer inside one document
(:func:`resolve_local_pointer`, :func:`resolve_schema_ref`); folding a node
together with the `$ref` target and `allOf` branches that unconditionally
apply to it (:func:`composed_schema_keys`, :func:`materialize_node`,
:func:`effective_properties`); and the questions asked of the composed
result — whether a dotted path is declared (:func:`resolve_declared_path`),
whether a node is typed (:func:`_declares_a_type`), whether an instance can be
an object (:func:`_composed_permits_object`). The walks that check an embedded
schema position by position against the endpoint contract's rules, and the
record-shape and `from_input` queries built on these functions, stay beside
the models that call them in `analitiq.contracts.endpoints`. Nothing here
imports that module: a refusal is raised as :class:`SchemaResolutionError`
carrying the diagnosis, and the caller frames it with its site and the rule
an author reads. The contract's type markers are the one thing this module
knows beyond JSON Schema: an explicit `null` on `native_type` or `arrow_type`
spells "not declared" and cannot overwrite an inherited value, a node
carrying every marker is typed, and an `arrow_type` other than `Object` rules
out an object-shaped node.

**Structural positions.** ``JSON_SCHEMA_SUBSCHEMA_KEYS`` (maps of schemas),
``JSON_SCHEMA_LIST_OF_SCHEMA_KEYS`` (lists of schemas) and
``JSON_SCHEMA_SINGLE_SCHEMA_KEYS`` (one schema) are the positions a schema can
hold another schema in. They are the vocabulary every reader of an embedded
schema shares: the position walks in `analitiq.contracts.endpoints` and
`analitiq.validator`, :func:`resolve_schema_ref`, which lets a pointer land
only in one of them, and :func:`_position_kind`, which decides how a merged
value is treated. `default`, `examples`, `const` and `enum` are never among
them; they carry user data that may be shaped exactly like a schema.

**Composition.** A schema is a DAG with back-edges, not a tree: `$defs`
entries are reached from several places, `allOf` multiplies the routes, and a
recursive `$defs` is legal. Three folds walk it, each following the `$ref`
target and the `allOf` branches and nothing else: :func:`_compose_schema_keys`
(a node's own keys, `properties` left out), :func:`_contributors` (every
declaration of each property name) and :func:`_materialize` (the node with
`$ref` and `allOf` consumed, its `properties` composed per name). All three
read their sources in ONE order — `$ref` target, then `allOf` branches in
document order, then the node itself — and precedence follows that order:
`_contributors` lists a name's declarations lowest first, a scalar or list
value is the last source's, an object value merges the sources
(:func:`_combine_schema_values`), and `type` intersects in the keys fold
because `$ref` and `allOf` are intersections. Order is part of the answer:
flattening the fold into one pre-order and reversing it inverts precedence
between a direct later branch and a transitively reached contributor of an
earlier one, so a nearby `allOf` override silently loses to a distant base and
nothing here raises. The folds are one fold seen from several angles, and they
disagree, silently, the moment they are computed differently.

**Memo and cycles.** Each fold is memoised on its RESULT, keyed by node
identity with the node held beside the result so a freed identity cannot be
reused mid-walk, and carries a separate `on_path` set. The memo and the set
answer different questions: a node already VISITED returns its result, a node
on the CURRENT path contributes nothing. That reproduces the fold exactly on
an acyclic document, visits each node once, and makes a cyclic document cost
the same as an acyclic one. Refusing to cache anything computed under a cycle
is not an alternative: the refusal reaches every ancestor of the back-edge
and the walk is exponential again. A memo belongs to one walk —
:func:`_property_contributors` starts a fresh one on every call, and
:func:`materialize_node` shares its keys memo between the fold and the object
gate so the two cannot report different types for one document.

**Walks that are not the fold.** :func:`_composed_unions` yields the
`anyOf`/`oneOf` lists a node inherits through `$ref` and `allOf`, with a
VISITED set and no memo: every union must hold, so yielding a shared node's
unions once is enough, and that is also what keeps a `$defs` subgraph reached
from several branches linear. :func:`_declares_a_type_walk` descends
`anyOf`/`oneOf` branches asking whether every branch is typed, materialising
a branch first when a root is in hand; it keeps the RAW branch object on its
path, because each materialization builds a fresh memo and so a materialised
result has a new identity every time, which would hide a recursive alias
through a union from the fold's own guard.

**Declared-path resolution.** ONE algorithm answers "does this dotted path
address something the document declares?" — :func:`resolve_declared_path`
for one path and :func:`effective_properties` for a whole node, each over
:func:`_property_contributors` and :func:`_compose_declarations`. A segment
resolves when one of the node's unconditional contributors declares it under
`properties`; several declarations compose as `allOf`, the refinement idiom,
and only a contradiction composition can PROVE is refused — a branch that is
the boolean schema `false` (:func:`_reject_unsatisfiable_branch`) or `type`
sets with an empty intersection (:func:`_refuse_disjoint_types`). The
algorithm does not guess: the keywords in ``_CONDITIONAL_DECLARATION_KEYWORDS``,
a schema-valued catch-all in ``_SCHEMA_VALUED_CATCHALL_KEYWORDS``, and a
`$ref` that did not resolve may or may not declare a name depending on the
instance, so a segment one of them might declare is reported as not
statically resolvable, with the fix named, rather than picked from a branch.
That check runs ONLY after the segment was not found, which keeps resolution
monotone: a node declaring `properties.next` beside a `oneOf` still resolves
`next`. The one deliberate narrowing is the object gate
(:func:`_composed_permits_object`): a node whose composed declaration
excludes an object instance contributes no properties, so a name written
under its `properties` is not found.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from enum import Enum
from typing import Any
from urllib.parse import unquote


# Sentinel for "key absent", distinct from a key present with value null: a
# pointer can legitimately land on a JSON null, and `resolve_local_pointer`
# answers with this rather than `None` so that
# `analitiq.contracts.endpoints._validate_schema_refs` can tell a dangling
# pointer from one that found `null`.
_MISSING = object()


# JSON Schema 2020-12 keywords whose values are themselves schemas (or maps/
# lists of schemas). A walk recurses only through these structural positions —
# never through `default`, `examples`, `const`, etc., which can legally carry
# arbitrary user data shaped like a schema.
JSON_SCHEMA_SUBSCHEMA_KEYS: frozenset[str] = frozenset({
    "properties", "patternProperties", "$defs", "definitions",
    "dependentSchemas",
})
JSON_SCHEMA_LIST_OF_SCHEMA_KEYS: frozenset[str] = frozenset({
    "allOf", "anyOf", "oneOf", "prefixItems",
})
JSON_SCHEMA_SINGLE_SCHEMA_KEYS: frozenset[str] = frozenset({
    "items", "contains", "additionalProperties", "propertyNames",
    "unevaluatedItems", "unevaluatedProperties",
    "not", "if", "then", "else",
    # 2020-12 §8.5. An annotation rather than an assertion, but it IS a schema
    # position: a subtree here would otherwise escape every walk over these
    # sets, which is the one thing they exist to prevent. Draft-07 `dependencies`
    # is deliberately absent — 2020-12 does not define it, so a conformant
    # validator never applies its subtree at all (it is inert data, like
    # `default`), and treating its property-name-array form as a schema map
    # would reject legal draft-07 shapes.
    "contentSchema",
})


class SchemaResolutionError(ValueError):
    """Base for the two ways declared-path resolution refuses to answer.

    A caller that only needs "this did not resolve, and here is why" catches
    this; one that needs the failing position catches
    :class:`DeclaredPathError` specifically. Both carry ``reason`` — the
    resolver's half of the message, kept free of site framing so every call
    site reports the same diagnosis in its own words.
    """

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        #: The diagnosis, without any site framing.
        self.reason = reason


class DeclarationConflictError(SchemaResolutionError):
    """Contributors for one name cannot all hold. Carries NO path coordinates.

    Raised wherever composition proves a contradiction — `_refuse_disjoint_types`
    (its callers are listed on it) and `_reject_unsatisfiable_branch` (from
    every fold).
    Each inspects a node or a name; none knows where it sits in anyone's path.
    :func:`resolve_declared_path` catches every one of them and re-raises a
    :class:`DeclaredPathError` carrying the segment it was resolving, so a
    caller may catch only the narrow class.

    A separate class, rather than a :class:`DeclaredPathError` with placeholder
    coordinates: the placeholder (`segment=None, index=-1`) was constructible,
    reachable through the public :func:`effective_properties`, and actively
    misleading — `segments[-1]` silently yields the LAST segment rather than
    "no segment", so a consumer following the in-repo idiom reported the failure
    at the wrong end of the path.
    """

class DeclaredPathError(SchemaResolutionError):
    """A declared path did not resolve AT A KNOWN POSITION.

    Call sites frame the failure with their own site name (which block the path
    came from) and re-raise a plain ``ValueError``; the ``reason`` is the
    resolver's half of the message, kept separate so every site reports the
    same diagnosis in its own words.

    ``segment`` and ``index`` always locate a real position in the path that was
    requested — ``index`` is a valid subscript of the caller's ``segments``, so
    ``".".join(segments[: exc.index + 1])`` is the walked prefix. A failure with
    no position is a :class:`DeclarationConflictError` instead.
    """

    def __init__(self, reason: str, segment: str, index: int) -> None:
        if index < 0:
            raise ValueError(
                f"DeclaredPathError.index must be a position in the path, got {index!r}; "
                "a failure with no position is a DeclarationConflictError"
            )
        super().__init__(reason)
        #: The segment that failed.
        self.segment = segment
        #: Position of ``segment`` in the requested path.
        self.index = index


def _unescape_pointer_token(token: str) -> str:
    """One JSON Pointer reference token, decoded.

    Two decodings, in the order the specs stack them:

    1. **Percent-decoding** (RFC 6901 §6). A `$ref` is a URI-reference and its
       fragment is the URI-fragment form of a pointer, so `%XX` escapes are the
       URI layer and are decoded FIRST. This is not cosmetic: a stock
       JSON-Pointer library decodes them, so skipping it would make this
       resolver disagree with the engine and the conformance kit about which
       refs are dangling (`#/$defs/my%20def` would resolve there and not here).
       A literal `%` in a key must therefore be written `%25`, exactly as the
       spec requires.
    2. **Pointer unescaping** (RFC 6901 §3): `~1` is `/` and `~0` is `~`, in
       that order — reversing them would turn an encoded `~1` into `/` twice.
    """
    return unquote(token).replace("~1", "/").replace("~0", "~")


def _pointer_array_index(token: str, array: list[Any]) -> int | None:
    """RFC 6901 §4 array index, or ``None`` when the token is not one.

    The index production is `0 | [1-9][0-9]*` — ASCII digits only, no leading
    zero, no sign. `str.isdigit()` is NOT that test: it accepts superscripts and
    other Unicode digit forms that `int()` then rejects with a raw `ValueError`,
    which would escape a resolver whose callers only catch
    :class:`DeclaredPathError`. It also accepts `00`, which the spec forbids and
    which would otherwise resolve while `01` did not — two spellings, two
    answers.
    """
    if token != "0" and not (token[:1] in "123456789" and token.isascii() and token.isdigit()):
        return None
    index = int(token)
    return index if index < len(array) else None


def resolve_local_pointer(root: Any, ref: str) -> Any:
    """Resolve an in-document `$ref` (`#`, `#/a/b`) to its node, or ``_MISSING``.

    The raw RFC 6901 walk over the document. Returns the sentinel — not
    ``None`` — because ``None`` is a legal thing to find at a JSON pointer and
    "found null" must not read as "not found".

    Three rules a re-implementation must match, stated because a stock library
    settles them differently or not at all:

    * tokens are percent-decoded then pointer-unescaped
      (:func:`_unescape_pointer_token`);
    * array indices follow RFC 6901 §4 exactly (:func:`_pointer_array_index`);
    * a plain-name fragment (`#name`) resolves to nothing here. It is an
      `$anchor` reference, and this contract does not author anchors — the
      guard (:func:`analitiq.contracts.endpoints._validate_schema_refs`)
      rejects both the anchor declaration and the reference to it with
      their own message, so an author is never told a working anchor is
      "dangling".

    A non-local `ref` (anything not starting with `#`) is never resolved here:
    the same guard refuses those outright, because nothing in the offline
    validate/author/execute path can fetch them.

    This walk is position-blind: it will happily land inside a `default` or an
    `examples` payload. Everything that *follows* a ref goes through
    :func:`resolve_schema_ref`, which does not.
    """
    if not isinstance(ref, str) or not ref.startswith("#"):
        return _MISSING
    node: Any = root
    fragment = ref[1:]
    if not fragment:
        return node
    if not fragment.startswith("/"):
        return _MISSING
    for token in fragment[1:].split("/"):
        key = _unescape_pointer_token(token)
        if isinstance(node, dict):
            if key not in node:
                return _MISSING
            node = node[key]
        elif isinstance(node, list):
            index = _pointer_array_index(key, node)
            if index is None:
                return _MISSING
            node = node[index]
        else:
            return _MISSING
    return node


#: Schema positions a JSON Pointer must not CROSS. Each holds a real subschema,
#: but one that applies only to some instances — a branch of a choice, an arm of
#: a condition, a rule for names or extras the document did not name outright.
#: A pointer that lands inside one addresses a conditional declaration, and
#: declared-path resolution reports only unconditional ones, so following it
#: would smuggle in exactly the guess the algorithm refuses to make when it
#: meets the same keyword on a node directly.
#:
#: `allOf` is absent on purpose: its branches all apply, so a pointer through
#: one addresses something unconditional. `properties`, `items`, `prefixItems`,
#: `$defs` and `definitions` are likewise unconditional positions.
_CONDITIONAL_POINTER_KEYWORDS: frozenset[str] = frozenset({
    "anyOf", "oneOf", "if", "then", "else", "not",
    "dependentSchemas", "patternProperties", "contains",
    "additionalProperties", "unevaluatedProperties", "unevaluatedItems",
})


def resolve_schema_ref(root: Any, ref: str) -> Any:
    """Resolve an in-document `$ref` that lands on a SCHEMA, or ``_MISSING``.

    :func:`resolve_local_pointer` restricted to pointers that stay inside
    schema positions — the ``JSON_SCHEMA_*_KEYS`` inventory: a map of schemas
    (`$defs`, `properties`, …), a list of schemas, or one schema.

    Why the restriction is load-bearing: the walks that check an embedded
    schema position by position (in `analitiq.contracts.endpoints` and
    `analitiq.validator`) never descend into `default`, `examples`, `const` or
    `enum`, because those carry arbitrary user data that may be shaped exactly
    like a schema. A pointer such as
    `#/properties/x/default` would therefore reach a subtree that nothing ever
    annotation-checked and no type map ever covered — and declared-path
    resolution would then hand that unvalidated node to its callers as if it
    were a declaration. Refusing to resolve there is what makes the guarantee
    of :func:`analitiq.contracts.endpoints._validate_schema_refs` true rather
    than aspirational.

    A pointer whose path CROSSES a conditional keyword is refused for the same
    reason, and it is the subtler half. `#/anyOf/0` names a perfectly real
    schema position — but that subschema applies only to instances that take
    that branch, so following the pointer would let the resolver commit to one
    branch of an `anyOf` and report its fields as unconditionally declared.
    That is precisely the guess the whole algorithm refuses to make when it
    meets `anyOf` on a node directly; reaching the same branch through a `$ref`
    must not be a way around it. Refused with the rest, so "never picks a
    branch" holds however the branch is addressed.

    Note this deliberately ignores `$id`. Under 2020-12 an `$id` retargets the
    base URI, which would make a `#`-leading ref external; the contract refuses
    `$id` in an embedded schema outright
    (:func:`analitiq.contracts.endpoints._validate_schema_refs`) rather than
    tracking base URIs, so by the time anything resolves a ref there is
    exactly one base — the embedded document.
    """
    if not isinstance(ref, str) or not ref.startswith("#"):
        return _MISSING
    fragment = ref[1:]
    if not fragment:
        return root
    if not fragment.startswith("/"):
        return _MISSING
    tokens = [_unescape_pointer_token(t) for t in fragment[1:].split("/")]
    node: Any = root
    index = 0
    while index < len(tokens):
        if not isinstance(node, dict):
            return _MISSING
        key = tokens[index]
        if key in _CONDITIONAL_POINTER_KEYWORDS:
            return _MISSING
        child = node.get(key, _MISSING)
        if child is _MISSING:
            return _MISSING
        if key in JSON_SCHEMA_SUBSCHEMA_KEYS:
            # A map of schemas: the next token names one of them.
            if not isinstance(child, dict) or index + 1 >= len(tokens):
                return _MISSING
            name = tokens[index + 1]
            if name not in child:
                return _MISSING
            node = child[name]
            index += 2
            continue
        if key in JSON_SCHEMA_LIST_OF_SCHEMA_KEYS or key in JSON_SCHEMA_SINGLE_SCHEMA_KEYS:
            if isinstance(child, list):
                # A list of schemas (including draft-07 tuple-form `items`):
                # the next token is the position.
                if index + 1 >= len(tokens):
                    return _MISSING
                position = _pointer_array_index(tokens[index + 1], child)
                if position is None:
                    return _MISSING
                node = child[position]
                index += 2
                continue
            if key in JSON_SCHEMA_SINGLE_SCHEMA_KEYS:
                node = child
                index += 1
                continue
            return _MISSING
        # Any other key is not a schema position — see the docstring.
        return _MISSING
    return node


#: The contract's own type annotations. Each spells "not declared" as an explicit
#: `null` rather than by absence, so :func:`_compose_schema_keys` drops a `null`
#: one instead of letting it win the merge.
_CONTRACT_TYPE_MARKERS = ("native_type", "arrow_type")


def composed_schema_keys(
    node: dict[str, Any],
    root: Any,
    memo: dict[int, tuple[Any, dict[str, Any]]] | None = None,
) -> dict[str, Any]:
    """``node``'s keys folded over its `$ref` target and `allOf` branches, with
    `properties` left out.

    THE ONE composition of a node's own declarations in this module.
    :func:`_materialize` builds its result on this, and
    :func:`_composed_permits_object` reads the composed type off it, so the two
    cannot answer differently about the same document — every way they were
    computed separately, they eventually disagreed.

    `properties` is excluded because it is not a declaration a node makes about
    itself: it is composed per NAME from the contributor walk, and that walk is
    gated on the type this fold reports. Folding it here would make the answer
    depend on the question.

    Source order is `$ref` target, then `allOf` branches in document order, then
    the node itself, with the last contributor winning — the order the module
    docstring states for every fold. `type` is the exception and
    INTERSECTS: `$ref` and `allOf` are intersections in 2020-12, so a node
    reached through both `{"type": ["string", "null"]}` and
    `{"type": ["object", "string"]}` can only be a string. Last-wins on that key
    reported `["object", "string"]` and made a scalar look object-shaped.
    :func:`_refuse_disjoint_types` already computes this intersection to prove
    the empty case; this keeps the result instead of discarding it.

    ``memo`` is shared by every fold belonging to ONE walk — a materialization
    and the gate it applies, say — so a `$defs` subgraph reached from several
    branches is folded once. Passing none starts a fresh walk. It is never
    shared BETWEEN walks: a node folded while an ancestor was on the path is
    truncated by the cycle rule, and that result belongs to the walk that
    computed it.
    """
    return _compose_schema_keys(node, root, {} if memo is None else memo, set())


def _compose_schema_keys(
    node: dict[str, Any],
    root: Any,
    memo: dict[int, tuple[Any, dict[str, Any]]],
    on_path: set[int],
) -> dict[str, Any]:
    """:func:`composed_schema_keys`' memoized worker. Same memo-and-`on_path`
    scheme as :func:`_materialize` and :func:`_contributors`, stated in the
    module docstring: the result is cached, and a node met again while it is
    still on the current path contributes nothing."""
    key = id(node)
    cached = memo.get(key)
    if cached is not None:
        return cached[1]
    if key in on_path:
        return {}
    on_path.add(key)

    sources: list[dict[str, Any]] = []
    ref = node.get("$ref")
    if isinstance(ref, str):
        target = resolve_schema_ref(root, ref)
        if isinstance(target, dict):
            sources.append(_compose_schema_keys(target, root, memo, on_path))
    branches = node.get("allOf")
    if isinstance(branches, list):
        for branch in branches:
            _reject_unsatisfiable_branch(branch)
            if isinstance(branch, dict):
                sources.append(_compose_schema_keys(branch, root, memo, on_path))
    sources.append({
        k: v for k, v in node.items()
        if k not in ("$ref", "allOf", "properties")
        # An explicit `null` on either contract marker spells "not declared"
        # (:func:`analitiq.contracts.endpoints._validate_arrow_type_in_json_schema`
        # reads it the same way), so it is not a contributor and must not
        # overwrite one inherited from a `$ref` target or an `allOf` branch.
        and not (k in _CONTRACT_TYPE_MARKERS and v is None)
    })

    _refuse_disjoint_types(_MATERIALIZED_NODE, sources)

    merged: dict[str, Any] = {}
    for source in sources:
        for name, value in source.items():
            merged[name] = (
                _combine_schema_values(merged[name], value, kind=_position_kind(name))
                if name in merged
                else value
            )

    declared = [t for t in (_declared_types(s) for s in sources) if t is not None]
    if len(declared) > 1:
        intersection = sorted(set.intersection(*declared))
        merged["type"] = intersection[0] if len(intersection) == 1 else intersection

    on_path.discard(key)
    memo[key] = (node, merged)
    return merged


def _composed_permits_object(
    node: dict[str, Any],
    root: Any,
    memo: dict[int, tuple[Any, dict[str, Any]]] | None = None,
) -> bool:
    """Whether ``node``'s `properties` map describes fields an instance can
    actually carry — the gate `resolve_declared_path`/`effective_properties`
    (via :func:`_property_contributors`) and :func:`materialize_node` (on the
    finished node, at its own entry) both put in front of `properties`.

    JSON Schema applies `properties` only to object instances, so
    `{"type": "string", "properties": {"age": …}}` describes a string and
    nothing more: the map is written down, and no conforming instance carries a
    single name out of it.

    REFUSE ONLY ON PROOF. The two mistakes this can make are not symmetric.
    Reporting a field that no record can carry is the defect this exists to
    catch, and it ships a connector wired to nothing. Failing to prove an
    exclusion costs nothing at all: the path resolves, exactly as it did before
    this gate existed. So precision is worth nothing bought at the price of
    refusing a document that works, and this deliberately proves less than a
    schema evaluator could.

    What carries a proof is the composed declaration
    (:func:`composed_schema_keys`), read as two facts that each refuse on their
    own: a bare `type` that excludes `object`, and an `arrow_type` — the
    contract's own type marker — that is present and is not `Object`. Neither
    overrules the other.

    `anyOf`/`oneOf` fold through :func:`_union_permits_object`, which carries the
    same burden. A union constrains every instance of the node that carries it,
    so one reached through a `$ref` target or an `allOf` branch counts exactly as
    one written here.

    `oneOf` folds as a plain union. Its exactly-one requirement can leave an
    object matching two branches and so satisfying neither, but proving that
    means deciding whether two arbitrary subschemas overlap, and the asymmetry
    above says that precision is not worth buying.
    """
    memo = {} if memo is None else memo
    composed = composed_schema_keys(node, root, memo)
    arrow_type = composed.get("arrow_type")
    if isinstance(arrow_type, str) and arrow_type != "Object":
        return False
    declared_types = _declared_types(composed)
    if declared_types is not None and "object" not in declared_types:
        return False
    return _union_permits_object(node, root, memo)


def _union_permits_object(
    node: dict[str, Any],
    root: Any,
    memo: dict[int, tuple[Any, dict[str, Any]]],
) -> bool:
    """Whether every `anyOf`/`oneOf` in ``node``'s composition leaves `object`
    possible.

    An instance need only match ONE branch, so a union refuses only when every
    branch is proven to exclude object — the nullable-record idiom
    `{"anyOf": [{"type": "object", …}, {"type": "null"}]}` keeps resolving on
    its object branch. Each branch is judged by the same composed declaration
    the node itself is, so no evidence crosses between alternatives.

    A branch is not an alternative when it cannot be taken: boolean `false`, or
    a branch whose own composition is refused (`allOf: [false]`, disjoint
    types). That refusal is :func:`_reject_unsatisfiable_branch` and
    :func:`_refuse_disjoint_types` doing the deciding, not a case written here,
    and it is contained to the branch — one impossible alternative must never
    reject the document. A list offering no alternative at all refuses; an empty
    list is malformed rather than impossible and proves nothing.

    Boolean `true`, and anything else this cannot read, proves nothing and
    leaves the union permitting.
    """
    for branches in _composed_unions(node, root, set()):
        alternatives: list[bool] = []
        for branch in branches:
            if branch is True:
                alternatives.append(True)
                continue
            if branch is False:
                continue  # not an alternative at all
            if not isinstance(branch, dict):
                alternatives.append(True)  # unreadable, so it proves nothing
                continue
            try:
                alternatives.append(_composed_permits_object(branch, root, memo))
            except DeclarationConflictError:
                continue  # an alternative no instance can take
        if branches and not any(alternatives):
            return False
    return True


def _composed_unions(
    node: dict[str, Any], root: Any, seen: set[int]
) -> Iterator[list[Any]]:
    """Every `anyOf`/`oneOf` list ``node``'s composition carries.

    A union binds every instance of the node it sits on, and `$ref` and `allOf`
    are intersections, so a union moved into a `$ref` target still binds the
    referencing node. Same traversal as :func:`_compose_schema_keys`, yielding
    the lists rather than composing them, because a union is not a declaration
    that merges.

    ``seen`` is a VISITED set, not a current-path set. Every union must hold, so
    yielding a shared node's unions once is enough, and a `$defs` subgraph
    reached from several branches is a DAG — walking it once per route is the
    exponential blowup `TestCompositionIsLinearNotExponential` exists to catch.
    Visiting each node once terminates on a cycle for the same reason.
    """
    key = id(node)
    if key in seen:
        return
    seen.add(key)
    ref = node.get("$ref")
    if isinstance(ref, str):
        target = resolve_schema_ref(root, ref)
        if isinstance(target, dict):
            yield from _composed_unions(target, root, seen)
    branches = node.get("allOf")
    if isinstance(branches, list):
        for branch in branches:
            if isinstance(branch, dict):
                yield from _composed_unions(branch, root, seen)
    for keyword in ("anyOf", "oneOf"):
        union = node.get(keyword)
        if isinstance(union, list):
            yield union


def _property_contributors(
    node: dict[str, Any],
    root: Any,
    keys_memo: dict[int, tuple[Any, dict[str, Any]]] | None = None,
) -> dict[str, list[Any]]:
    """Every UNCONDITIONAL declaration of each property name, LOWEST precedence
    first.

    Structurally identical to :func:`_materialize` — same recursion, same source
    order (`$ref` target, `allOf` branches in document order, then the node
    itself), same memo. That is not a coincidence to be optimised away: the two
    are the same fold seen from two angles, and every time they were computed
    differently they disagreed, silently, about which declaration wins.

    The ordering contract is the whole point. `_compose_declarations` wraps these
    into `{"allOf": [...]}` and every consumer materializes that LAST-WINS, so
    the list must run lowest-precedence first: a node's own `properties.<name>`
    beats its `$ref` base's, and a later `allOf` branch beats an earlier one.
    Three previous attempts got this wrong in three different ways — a reversed
    pre-order inverted sibling branches, an un-reversed one let the base beat the
    refinement, a flattened post-order emitted a shared node at its first
    (lowest) position — and all three shipped green, because the failure is a
    different `arrow_type` on a derived column, not an error.

    Declarations are deduplicated by equality, and a declaration re-contributed
    by a LATER source moves to the end — its highest-precedence position. Keeping
    the first occurrence is NOT safe and was one of the three bugs above; the
    inline comment at the dedup states the failure.
    """
    # The gate is applied to the WHOLE node, once, and not threaded into the
    # walk: `type` on one `allOf` branch and `properties` on a sibling describe
    # the same instance, so a branch judged alone proves nothing about it.
    if not _composed_permits_object(node, root, keys_memo):
        return {}
    return _contributors(node, root, {}, set())


def _contributors(
    node: dict[str, Any],
    root: Any,
    memo: dict[int, tuple[Any, dict[str, list[Any]]]],
    on_path: set[int],
) -> dict[str, list[Any]]:
    """Memoized worker. See the module docstring for the memo/cycle scheme."""
    key = id(node)
    cached = memo.get(key)
    if cached is not None:
        return cached[1]
    if key in on_path:
        return {}
    on_path.add(key)

    sources: list[dict[str, list[Any]]] = []
    ref = node.get("$ref")
    if isinstance(ref, str):
        target = resolve_schema_ref(root, ref)
        if isinstance(target, dict):
            sources.append(_contributors(target, root, memo, on_path))
    branches = node.get("allOf")
    if isinstance(branches, list):
        for branch in branches:
            _reject_unsatisfiable_branch(branch)
            if isinstance(branch, dict):
                sources.append(_contributors(branch, root, memo, on_path))
    own = node.get("properties")
    if isinstance(own, dict):
        sources.append({name: [declaration] for name, declaration in own.items()})

    contributors: dict[str, list[Any]] = {}
    for source in sources:
        for name, declarations in source.items():
            bucket = contributors.setdefault(name, [])
            for declaration in declarations:
                # A declaration contributed again by a LATER source moves to the
                # end — its highest-precedence position. Keeping the first
                # occurrence and skipping the rest was the bug: an identical
                # declaration reached early through a base pinned itself below a
                # later sibling that restated it, so the sibling lost. Dedup is
                # about not stacking the same shape twice, not about which
                # position it holds.
                for index, seen in enumerate(bucket):
                    if declaration == seen:
                        bucket.pop(index)
                        break
                bucket.append(declaration)

    on_path.discard(key)
    memo[key] = (node, contributors)
    return contributors


def _reject_unsatisfiable_branch(branch: Any) -> None:
    """`allOf: [false, …]` is an empty intersection.

    Checked in every fold rather than only the materializing one: a rule
    enforced by one view and not the other is how `effective_properties` came to
    answer `{}` where `materialize_node` raised, which crashed a public helper on
    a document the gate had accepted. `true` is vacuous and is simply skipped.
    """
    if branch is False:
        raise DeclarationConflictError(
            "an `allOf` branch is the boolean schema `false`, which no instance "
            "satisfies, so the whole intersection is empty"
        )


def _declared_types(declaration: Any) -> set[str] | None:
    """The `type` values a bare declaration allows, or ``None`` when it names none.

    Reads only a direct `type` key (string or list) — the JSON Schema
    vocabulary `_refuse_disjoint_types` intersects to prove a contradiction
    between already-materialized `allOf`/`$ref` contributors. It does not
    know about `anyOf`/`oneOf` unions or the contract's own `native_type`/
    `arrow_type` pair; `_declares_a_type` is the union-and-contract-aware
    "is this typed at all" question, and recurses through both on its own.
    """
    if not isinstance(declaration, dict):
        return None
    declared = declaration.get("type")
    if isinstance(declared, str):
        return {declared}
    if isinstance(declared, list) and all(isinstance(t, str) for t in declared):
        return set(declared)
    return None


def _compose_declarations(key: str, declarations: list[Any]) -> Any:
    """The single declaration a name resolves to, given its contributors.

    One contributor resolves to itself. Several compose as `allOf` — which is
    exactly what the document said: `allOf` and `$ref` are intersections, so a
    branch that NARROWS a base declaration (`{type: string}` refined to
    `{type: string, format: date-time}`) is the dominant real-world idiom and
    must resolve, not fail. The resolver's job is to LOCATE a declaration, not
    to decide its content, so it composes rather than picks.

    The one refusal is a contradiction it can prove: contributors whose `type`
    sets are disjoint cannot both hold, so nothing satisfies the intersection
    and no answer about the field is honest. Proof is required — mere
    inequality is not a contradiction — because rejecting on difference alone
    would refuse the refinement idiom above, which a `properties`-only reading
    of the same document accepts.
    """
    if not declarations:
        # Unreachable via `_property_contributors`, which only creates a bucket
        # when it has something to put in it. Asserted rather than assumed: the
        # natural "empty" answer here is `{"allOf": []}`, a vacuous schema
        # meaning "anything" — the one answer a resolver must never invent.
        raise DeclarationConflictError(
            f"no contributor declares {key!r}; refusing to compose a vacuous declaration"
        )
    if len(declarations) == 1:
        return declarations[0]
    _refuse_disjoint_types(key, declarations)
    # NOT reversed. `_contributors` already emits lowest-precedence first, in
    # the fold's own source order, which is exactly what a last-wins `allOf`
    # needs. Reversing here was an attempt to compensate for a pre-order walk
    # and it inverted sibling `allOf` branches instead.
    return {"allOf": list(declarations)}


#: Stand-in "name" for the whole-node case. `_refuse_disjoint_types`' message is
#: written for a property name; passing the literal "node" made it read as a
#: field called `node`, which no document has.
_MATERIALIZED_NODE = "<this node>"


def _refuse_disjoint_types(where: str, sources: list[Any]) -> None:
    """Raise when ``sources`` declare `type` sets whose intersection is empty.

    The one contradiction declared-path resolution can PROVE. Shared by every
    site that composes declarations — the contributors of one property name
    (:func:`_compose_declarations`) and a node folded with its `$ref`/`allOf`
    sources (:func:`_compose_schema_keys`, :func:`_combine_schema_values`,
    :func:`_materialize`) — so that they agree: composing and materializing
    are the same intersection seen from two directions, and a rule enforced by
    only one of them is a gate the other walks past. Proof is required — mere
    inequality is not a contradiction — or the `allOf` refinement idiom
    (:func:`_compose_declarations`) would be refused.

    Raises :class:`DeclarationConflictError`, which carries no path coordinates:
    every caller inspects a node or a name and none knows where it sits in
    anyone's path. :func:`resolve_declared_path` re-raises it with the segment
    it was resolving.
    """
    type_sets = [t for t in (_declared_types(s) for s in sources) if t is not None]
    if len(type_sets) > 1 and not set.intersection(*type_sets):
        raise DeclarationConflictError(
            f"conflicting redeclaration of {where!r} across allOf/$ref branches: "
            f"the declared types {sorted({t for s in type_sets for t in s})!r} "
            "are disjoint, so no instance can satisfy all of them"
        )


def effective_properties(
    node: dict[str, Any], root: Any = None
) -> dict[str, Any]:
    """The property declarations that UNCONDITIONALLY apply to ``node``.

    :func:`_property_contributors` composed per name
    (:func:`_compose_declarations`).

    ``root`` is the document `$ref` pointers resolve against; it defaults to
    ``node``, which is correct when ``node`` IS the whole embedded schema.
    Callers walking INTO a schema must pass the original root, or a `$ref`
    would resolve against a subtree and silently miss `$defs`.

    This composes EVERY name, so it reports a provable contradiction anywhere in
    the node — it is the whole-node view. :func:`resolve_declared_path` composes
    only the segment it is looking for, so a contradiction on an unrelated key
    never blocks an unrelated path; that difference is deliberate and is the
    reason the resolver does not simply call this.
    """
    root = node if root is None else root
    return {
        key: _compose_declarations(key, declarations)
        for key, declarations in _property_contributors(node, root).items()
    }


#: How a key's value should be merged. Only a SCHEMA position can hold a
#: contradiction worth proving; `default`, `const`, `examples` and `x-*` carry
#: arbitrary user data that may be shaped exactly like a schema, and running the
#: `type` check there proved two `default` objects "contradictory" because both
#: happened to have a field named `type`.
class _Position(str, Enum):
    """What a key's value IS, which decides how it merges and whether a
    contradiction in it is worth proving."""

    SCHEMA = "schema"
    SCHEMA_MAP = "schema_map"
    DATA = "data"


_SCHEMA_POSITION = _Position.SCHEMA
_SCHEMA_MAP_POSITION = _Position.SCHEMA_MAP
_DATA_POSITION = _Position.DATA


def _position_kind(key: str) -> _Position:
    if key in JSON_SCHEMA_SUBSCHEMA_KEYS:
        return _SCHEMA_MAP_POSITION
    if key in JSON_SCHEMA_SINGLE_SCHEMA_KEYS:
        return _SCHEMA_POSITION
    return _DATA_POSITION


def _combine_schema_values(
    existing: Any, incoming: Any, *, kind: _Position = _Position.DATA
) -> Any:
    """Fold ``incoming`` into ``existing`` for one key of a materialized node.

    Objects merge recursively — `allOf` and `$ref` are intersections, so BOTH
    declarations apply and an `allOf` branch that refines `items` must ADD to
    the base rather than replace it. Overwriting was a real defect: a record
    shape whose fields came from an inline `properties` map lost them to an
    `allOf` branch, silently changing the enumerated column set.

    Everything else — scalars AND lists — is last-wins. That is genuinely lossy
    for `required` and `enum`, whose true
    intersection this does not compute. The one lossy case that could produce a
    confidently-wrong answer, mutually exclusive `type` sets, is refused
    outright by :func:`_refuse_disjoint_types` before any merging happens.
    """
    if isinstance(existing, dict) and isinstance(incoming, dict):
        if kind == _SCHEMA_POSITION:
            # Only here. This is a real subschema, so two contributors declaring
            # disjoint `type`s cannot both hold — the same proof
            # `_compose_declarations` applies to a property name's contributors,
            # so the two views of one document cannot disagree. Applying it at
            # every depth instead read `default`/`const`/`x-*` payloads as
            # schemas and refused documents that were merely carrying data.
            _refuse_disjoint_types(_MATERIALIZED_NODE, [existing, incoming])
        child_kind = {
            _SCHEMA_MAP_POSITION: _SCHEMA_POSITION,
            _SCHEMA_POSITION: None,
            _DATA_POSITION: _DATA_POSITION,
        }[kind]
        merged = dict(existing)
        for key, value in incoming.items():
            merged[key] = (
                _combine_schema_values(
                    merged[key], value,
                    kind=_position_kind(key) if child_kind is None else child_kind,
                )
                if key in merged
                else value
            )
        return merged
    return incoming


def materialize_node(node: Any, root: Any = None) -> Any:
    """``node`` folded together with the contributors that unconditionally apply.

    The inspection counterpart of :func:`effective_properties`: it answers
    "what does this node say about `type` / `items` / `properties`?" when the
    answer is spread across a `$ref` target and `allOf` branches. Without it a
    consumer reading `node["type"]` off a `{"$ref": "#/$defs/Coll"}` sees
    nothing — which is how a document following RULE-ENDP-026's own advice
    ("put it in this document's `$defs`") could validate and then yield zero
    record fields.

    `$ref` and `allOf` are CONSUMED, so a materialized node never carries them.
    Sources apply in the order `$ref` target, `allOf` branches, then the node
    itself, each already materialized — so the node's own statements win, and
    among its contributors the LAST one stated wins. That is what makes the
    canonical `allOf: [{$ref: Base}, {refinement}]` idiom mean what it says.
    Object values (`items`, a nested `properties` map) merge recursively rather
    than replace, because `allOf` is an intersection and both declarations
    apply.

    ``root`` is the document `$ref` pointers resolve against; it defaults to
    ``node``, which is correct only when ``node`` IS the whole embedded schema.
    Pass the real root when materializing a subtree, or `$defs` is unreachable.
    """
    if not isinstance(node, dict):
        return node
    root = node if root is None else root
    # One keys-memo for this whole materialization, shared with the gate below:
    # a `$defs` subgraph reached from several branches is folded once.
    keys_memo: dict[int, tuple[Any, dict[str, Any]]] = {}
    materialized = _materialize(node, root, {}, set(), keys_memo)
    # Gated HERE, on the node the caller asked about, and never inside the
    # recursion: a `$ref` target can be scalar on its own and object-shaped once
    # an enclosing `arrow_type` composes over it, so pruning it mid-fold
    # discarded properties the finished node does carry.
    if (
        isinstance(materialized, dict)
        and isinstance(materialized.get("properties"), dict)
        and not _composed_permits_object(node, root, keys_memo)
    ):
        # KNOWN-EMPTY, not absent.
        # `analitiq.contracts.endpoints._json_schema_top_level_fields` tells
        # those apart, and a scalar instance carrying no named field is as knowable as
        # an explicit `properties: {}`; dropping the key would answer
        # "unknowable, skip" and turn off the `conflict_keys` and `from_input`
        # membership checks for exactly the documents this gate exists to catch.
        materialized["properties"] = {}
    return materialized


def _materialize(
    node: dict[str, Any],
    root: Any,
    memo: dict[int, tuple[Any, Any]],
    on_path: set[int],
    keys_memo: dict[int, tuple[Any, dict[str, Any]]],
) -> Any:
    """Memoized fold. Each node is materialized ONCE and its RESULT reused.

    Two earlier shapes of this were wrong, both silently, and the reason is
    worth keeping:

    * Refusing to cache anything computed under a cycle made a single `$ref`
      back-edge disable the cache for every ancestor, so the walk stayed
      exponential on exactly the schemas that motivated the cache.
    * Flattening the fold into one ordered chain with a global visited set
      cannot express the fold at all. A node reached from two places was
      emitted at its FIRST position — the LOWEST precedence in a last-wins
      merge — while the fold re-merged it at every position, giving it the
      precedence of its LAST. On 4000 acyclic shared-`$defs` schemas the two
      disagreed 1502 times: a nearby refinement lost to a distant base, and a
      destination column was created `Utf8` instead of `Timestamp` with no
      error anywhere.

    Memoizing the RESULT is what the fold did, so it reproduces the fold
    exactly on any acyclic document, and it visits each node once. A node
    already on the current path is a cycle: it contributes nothing the second
    time it is met, which is the rule the contract states. The memo holds the
    node beside its result so a freed node's `id()` cannot be reused mid-walk.

    Its non-`properties` keys come from :func:`composed_schema_keys`, the one
    fold `_composed_permits_object` also reads, so a materialized node and the
    gate cannot report different types for the same document.
    """
    key = id(node)
    cached = memo.get(key)
    if cached is not None:
        return cached[1]
    if key in on_path:
        return {}
    on_path.add(key)

    sources: list[Any] = []
    ref = node.get("$ref")
    if isinstance(ref, str):
        target = resolve_schema_ref(root, ref)
        if isinstance(target, dict):
            sources.append(_materialize(target, root, memo, on_path, keys_memo))
    branches = node.get("allOf")
    if isinstance(branches, list):
        for branch in branches:
            _reject_unsatisfiable_branch(branch)
            if isinstance(branch, dict):
                sources.append(_materialize(branch, root, memo, on_path, keys_memo))
    sources.append({k: v for k, v in node.items() if k not in ("$ref", "allOf")})

    # The same refusal `_compose_declarations` applies to a property name's
    # contributors. Without it the two disagree, and the PERMISSIVE one guards
    # the gate: `allOf: [{type: object}, {type: array, items: …}]` merged
    # last-wins yields a tidy `{type: "array"}` that
    # `analitiq.contracts.endpoints._validate_records_in_response_schema`
    # accepts, so a record collection no instance can satisfy validates as a
    # good array.
    _refuse_disjoint_types(_MATERIALIZED_NODE, sources)

    # Every key but `properties`, from the one fold the gate below also reads.
    merged: dict[str, Any] = dict(composed_schema_keys(node, root, keys_memo))

    # `properties` is composed per NAME here, and the satisfiability proof below
    # reads the same raw contributor list `_compose_declarations` proves, so the
    # two views cannot disagree about whether a name is self-contradictory.
    # Merging the maps as an ordinary dict key instead was how `materialize_node`
    # came to answer where `effective_properties` refused — and the permissive
    # one is what derives the destination column.
    #
    # Each declaration is FOLDED but NOT recursively materialized: a caller
    # reading an annotation off a child descriptor must materialize that
    # descriptor itself. `analitiq.contracts.endpoints.find_record_field_properties`
    # and `analitiq.contracts.endpoints._walk_input_schema_path` both do exactly
    # that, and the comment at the former explains why — materializing only the
    # walk left a field declared as
    # `{"$ref": "#/$defs/Addr"}` coming back raw, with no `type` and no
    # `arrow_type`.
    #
    # The emptiness test is on DECLARATION, not on the composed result: an
    # explicit `properties: {}` means "zero fields", which
    # `analitiq.contracts.endpoints._json_schema_top_level_fields` must be able
    # to tell apart from "no `properties` map anywhere", its unknowable→skip
    # case. Dropping the key
    # when the map is empty collapsed the two and turned `conflict_keys` and the
    # `from_input` membership check back off.
    own_properties = [
        source["properties"] for source in sources
        if isinstance(source, dict) and isinstance(source.get("properties"), dict)
    ]
    if own_properties:
        # The proof reads the RAW contributors — the same list
        # `_compose_declarations` proves — and NOT the maps hanging off
        # `sources`. Those sources have each already been materialized, and
        # `type` is a data position that merges last-wins, so a nested level
        # collapses its own contributors before this one can see them:
        # `{string,integer}` then `{integer,boolean}` folds to
        # `{integer,boolean}`, and the three-way emptiness against
        # `{string,boolean}` becomes a two-way overlap on `boolean`. Proving
        # from the folded maps therefore refused single-level contradictions
        # and accepted nested ones, which is `effective_properties` refusing a
        # record shape `materialize_node` was happy to derive columns from.
        #
        # The walk starts FRESH, exactly as `effective_properties(node, root)`
        # would run it. No second cache: `_materialize`'s own memo already
        # guarantees each node's body runs once per materialization, so a
        # proved-nodes cache here never gets a hit (measured: 0 hits across the
        # full suite and 40k random cyclic documents) — and the SHARED variant
        # that would get hits was the bug. `_contributors` caches `{}` for a
        # node it meets on the CURRENT path, so a shared entry computed while
        # some ancestor was mid-walk carries that truncation, and a later node
        # reads a weaker contributor set than `effective_properties` would
        # compute from a standing start:
        #
        #   A: {$ref: C, properties: {x: {type: [string, boolean]}}}
        #   B: {$ref: A, properties: {x: {type: [integer, boolean]}}}
        #   C: {$ref: A, properties: {x: {type: [string, integer]}}}
        #
        # gave `materialize_node(B)` an accepted `[integer, boolean]` while
        # `effective_properties(B)` refused. Truncation-tainting a shared cache
        # instead is exponential — the taint propagates to every ancestor, so
        # one back-edge uncaches the whole walk.
        #
        # THE PRICE, stated because nothing else in this file will: one full
        # contributor walk per node that declares `properties`, which is cubic
        # on a deep UNSHARED `allOf` chain (measured ~3s at depth 400, ~44ms at
        # depth 100). Real record shapes nest < 10 deep, where this is ~0.2ms;
        # the linearity pins bound the shared/cyclic shapes that actually occur.
        raw = _property_contributors(node, root, keys_memo)
        by_name: dict[str, list[Any]] = {}
        for source_map in own_properties:
            for name, declaration in source_map.items():
                by_name.setdefault(name, []).append(declaration)
        properties: dict[str, Any] = {}
        for name, declarations in by_name.items():
            # Fall back to the folded declarations only for a name the raw walk
            # cannot see (a cycle truncated it); never prove on the weaker set
            # when the stronger one exists.
            contributors = raw.get(name) or declarations
            if len(contributors) > 1:
                _refuse_disjoint_types(name, contributors)
            folded = declarations[0]
            for declaration in declarations[1:]:
                folded = _combine_schema_values(
                    folded, declaration, kind=_SCHEMA_POSITION
                )
            properties[name] = folded
        merged["properties"] = properties

    on_path.discard(key)
    memo[key] = (node, merged)
    return merged


#: Keywords whose presence means "this node MIGHT declare more fields, subject to
#: the instance". Their mere presence is enough to make a missing segment
#: ambiguous rather than absent.
_CONDITIONAL_DECLARATION_KEYWORDS: tuple[str, ...] = (
    "anyOf", "oneOf", "if", "then", "else", "patternProperties", "dependentSchemas",
)
#: Catch-alls that only widen the declared set when they carry a SCHEMA. As
#: `true`/`false` (or absent) they say nothing about names, so they must not
#: turn a plain typo into an "ambiguous" report.
_SCHEMA_VALUED_CATCHALL_KEYWORDS: tuple[str, ...] = (
    "additionalProperties", "unevaluatedProperties",
)


def _conditional_declaration_keywords(node: dict[str, Any], root: Any) -> list[str]:
    """Keywords on ``node`` that could conditionally declare an absent segment.

    Returned in a fixed order so error messages are stable and greppable.
    """
    found = [k for k in _CONDITIONAL_DECLARATION_KEYWORDS if k in node]
    found += [
        k for k in _SCHEMA_VALUED_CATCHALL_KEYWORDS if isinstance(node.get(k), dict)
    ]
    ref = node.get("$ref")
    # A `$ref` that DID resolve has already contributed everything it declares,
    # so it cannot be the reason a segment is missing. One that did not resolve
    # can be — and `analitiq.contracts.endpoints._validate_schema_refs` will
    # have rejected it in its own right.
    if ref is not None and not isinstance(resolve_schema_ref(root, ref), dict):
        found.append("$ref")
    return found


def resolve_declared_path(
    start_node: Any, segments: Sequence[str], *, root: Any = None
) -> Any:
    """Resolve ``segments`` against ``start_node`` by declared-path resolution.

    THE contract's path-resolution rule (see the module docstring). For each
    segment: the current node must be an object schema; the segment must be
    declared by one of that node's unconditional contributors
    (:func:`_property_contributors`); resolution moves to the composition of
    that name's declarations (:func:`_compose_declarations`). An empty
    ``segments`` resolves to ``start_node`` itself.

    ``root`` is the document `$ref` pointers resolve against, and defaults to
    ``start_node``. A caller that starts the walk at a SUBTREE — the record
    shape under `items`, say — must pass the whole embedded schema as ``root``,
    or a `#/$defs/...` ref inside that subtree resolves against the subtree,
    finds no `$defs`, and the path is misreported as undeclared.

    Only the segment being looked for is composed, so a provable contradiction
    on some unrelated property of the same node does not block this path;
    :func:`effective_properties` is the whole-node view that does report those.

    Raises :class:`DeclaredPathError` — never returns a sentinel — so no caller
    can accidentally treat "unresolvable" as "resolved to nothing". The
    exception's ``reason`` distinguishes the failure modes an author fixes
    differently: a non-object intermediate, a conditionally-declared
    (untightened) schema, a contradiction, and a plain undeclared segment (the
    typo case).
    """
    node: Any = start_node
    document: Any = start_node if root is None else root
    for index, segment in enumerate(segments):
        if not isinstance(node, dict):
            raise DeclaredPathError(
                "intermediate node is not an object schema",
                segment=segment,
                index=index,
            )
        # `_property_contributors` raises `DeclarationConflictError` too (an
        # `allOf` branch that is boolean `false`), so it sits INSIDE the same
        # try as the compose: a conflict raised while collecting contributors is
        # every bit as positioned as one raised while folding them, and leaving
        # it outside sent the bare conflict past the three narrow
        # `except DeclaredPathError` handlers — the document was still refused,
        # but the author lost the field name and the walked prefix.
        try:
            contributors = _property_contributors(node, document)
            if segment in contributors:
                node = _compose_declarations(segment, contributors[segment])
        except DeclarationConflictError as exc:
            # Neither callee has path context of its own (one inspects a node,
            # the other a name); re-raise with the segment/index this walk is at
            # so the caller can say where in the path the contradiction sits.
            raise DeclaredPathError(
                exc.reason, segment=segment, index=index
            ) from None
        if segment in contributors:
            continue

        # Not declared. Only NOW does ambiguity matter — checking it earlier
        # would reject paths that resolve perfectly well through `properties`.
        conditional = _conditional_declaration_keywords(node, document)
        if conditional:
            raise DeclaredPathError(
                "path is not statically resolvable: this node declares "
                f"{', '.join(conditional)}; declare {segment!r} under 'properties'",
                segment=segment,
                index=index,
            )
        raise DeclaredPathError(
            f"{segment!r} is not declared", segment=segment, index=index
        )
    return node


def _declares_a_type(node: Any, root: Any = None) -> bool:
    """Whether a resolved node says what kind of value lives there.

    `type` is the JSON Schema statement; the `native_type`/`arrow_type` pair is
    the contract's own; either answers the question RULE-ENDP-023 asks. So does
    `anyOf`/`oneOf` where EVERY branch answers it by either mechanism — the
    common nullable idiom (`{"anyOf": [{"type": "string"}, {"type": "null"}]}`)
    among them — since the value is then provably typed however the union
    resolves; one branch answering neither way makes the whole union
    unbounded. A branch can itself need `$ref`/`allOf` resolution first
    (`materialize_node` does not recurse into `anyOf`/`oneOf` branches on its
    own), so `root` — the same root `node` was materialized against — resolves
    each branch before it is inspected; a caller with no root in scope simply
    cannot recognise a `$ref` branch's type.
    """
    return _declares_a_type_walk(node, root, set())


def _declares_a_type_walk(node: Any, root: Any, on_path: set[int]) -> bool:
    """`_declares_a_type`'s own recursion, tracking `anyOf`/`oneOf` branches
    already on the current path.

    A recursive alias (a branch `$ref`erring to a `$defs` entry that contains
    that same `anyOf`/`oneOf`) is a valid Draft 2020-12 shape `materialize_node`
    does not collapse on its own — it is not a `$ref`/`allOf` cycle, the only
    kind that function's own memoization catches, and each top-level call to it
    folds through a fresh memo, so its result has a new identity every time
    even for the same `$ref` target. `on_path` therefore tracks each RAW
    branch (before materializing it) rather than the materialized result — the
    raw branch is the same object every time this exact schema position is
    reached, so a second visit is a real cycle. Revisiting it contributes
    nothing, the same rule `_materialize`'s own `on_path` applies to a
    `$ref`/`allOf` cycle, rather than recursing until `RecursionError`.
    """
    if not isinstance(node, dict):
        return False
    if _declared_types(node):
        return True
    if node.get("native_type") is not None and node.get("arrow_type") is not None:
        return True
    for branch_key in ("anyOf", "oneOf"):
        branches = node.get(branch_key)
        if not isinstance(branches, list) or not branches:
            continue
        every_branch_typed = True
        for branch in branches:
            if not isinstance(branch, dict):
                every_branch_typed = False
                break
            branch_id = id(branch)
            if branch_id in on_path:
                every_branch_typed = False
                break
            resolved_branch = branch
            if root is not None:
                try:
                    resolved_branch = materialize_node(branch, root)
                except SchemaResolutionError:
                    every_branch_typed = False
                    break
            if not _declares_a_type_walk(resolved_branch, root, on_path | {branch_id}):
                every_branch_typed = False
                break
        if every_branch_typed:
            return True
    return False
