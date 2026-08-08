"""Census entries for the small contract modules: ``connection``,
``credentials_file``, ``shared.common``, and ``type_map``."""
from __future__ import annotations

from analitiq.contracts.shared.prose_obligation import (
    ENGINE_CONDUCT,
    ENGINE_OWNED_DEFAULTING,
    ProseObligation,
)

PROSE_OBLIGATIONS: tuple[ProseObligation, ...] = (
    # === connection ==========================================================
    ProseObligation(
        model="ConnectionStoredMaps", field="secret_refs",
        prose_hash="7f5b3165f886",
        structural=(
            "every value is typed SecretRefValue, whose "
            "SECRET_REF_VALUE_PATTERN enumerates the cloud-free schemes the "
            "description lists"
        ),
    ),
    ProseObligation(model="ConnectionAuthored", prose_hash="9876daaf7229", descriptive=True),
    ProseObligation(
        model="ConnectionAuthored", field="connector_id",
        prose_hash="8ab928ade179",
        structural=(
            "required field with a `Field` length floor rejecting the empty "
            "string; no pattern constrains the identifier shape"
        ),
    ),
    ProseObligation(
        model="ConnectionAuthored", field="description",
        prose_hash="e8037a4395dd", descriptive=True,
    ),
    ProseObligation(
        model="ConnectionAuthored", field="display_name",
        prose_hash="ca5db73f31c0",
        structural=(
            "`Field` length bounds (`DISPLAY_NAME_MIN` / `DISPLAY_NAME_MAX`), "
            "the `NO_EDGE_WHITESPACE_PATTERN` pattern, and the "
            "`validate_display_name` field validator carry the label bounds "
            "and the edge-whitespace ban"
        ),
    ),
    ProseObligation(
        model="ConnectionAuthored", field="schema_url",
        prose_hash="7fdc940ebbda",
        structural=(
            "when present, a `Literal` pinned to `CONNECTION_SCHEMA_URL` "
            "carries the value"
        ),
        waiver=(
            "the presence half is context-dependent — whether the document is "
            "a standalone file or an API payload is not recorded in the "
            "document — and nothing enforces it today: the validator's "
            "connection kind accepts a standalone file that omits `schema_url`"
        ),
    ),
    ProseObligation(
        model="ConnectionAuthored", field="tags",
        prose_hash="2d7a5288f193",
        structural=(
            "a `Field` item ceiling (`TAGS_MAX`), items typed `TrimmedTag`, "
            "and the `validate_tags` field validator carry the count, length, "
            "uniqueness and trimming bounds"
        ),
    ),
    ProseObligation(
        model="ConnectionInput",
        prose_hash="da347a05e844",
        structural=(
            "extra='forbid' rejects service-assigned and unknown keys alike; "
            "`connection_id` is an optional field whose `UUID_PATTERN` "
            "constraint carries the authored-identifier shape"
        ),
        waiver=ENGINE_OWNED_DEFAULTING,
    ),
    ProseObligation(
        model="ConnectionInput", field="connection_id",
        prose_hash="002115926db6",
        structural=(
            "optional field with a `UUID_PATTERN` constraint carrying the "
            "RFC-4122 shape"
        ),
        waiver=ENGINE_OWNED_DEFAULTING,
    ),
    ProseObligation(
        model="ConnectionStoredMaps",
        prose_hash="635bda531ea7",
        structural=(
            "the docstring's only checkable claim — `secret_refs` holds "
            "pointers, never secret material — is carried at the field entry: "
            "values typed `SecretRefValue`, constrained by "
            "`SECRET_REF_VALUE_PATTERN`"
        ),
    ),
    ProseObligation(
        model="ConnectionStoredMaps", field="discovered",
        prose_hash="f456853380e0",
        rule_ids=("RULE-CONN-004",),
        waiver=(
            "the key vocabulary is cross-document: post-auth output names "
            "live on the owning connector's `post_auth_outputs`, which this "
            "document cannot see; and the non-secret-VALUES half is carried "
            "only by the rule's key-shape heuristic — values are not scanned, "
            "so a secret under an innocuous key is not caught offline"
        ),
    ),
    ProseObligation(
        model="ConnectionStoredMaps", field="parameters",
        prose_hash="de989e1e1dff",
        rule_ids=("RULE-CONN-004",),
        waiver=(
            "the per-key value vocabularies are cross-document — authored on "
            "the owning connector's `connection_contract` — and enforced "
            "server-side on save, as the prose itself states; the non-secret "
            "half is carried only by the rule's key-shape heuristic — values "
            "are not scanned, so a secret under an innocuous key is not "
            "caught offline"
        ),
    ),
    ProseObligation(
        model="ConnectionStoredMaps", field="selections",
        prose_hash="2df6349b013a",
        rule_ids=("RULE-CONN-004",),
        waiver=(
            "the key vocabulary is cross-document: post-auth output names "
            "live on the owning connector's `post_auth_outputs`, which this "
            "document cannot see; values are not scanned by the rule's "
            "key-shape heuristic, so a secret under an innocuous key is not "
            "caught offline"
        ),
    ),
    # === credentials-file ====================================================
    ProseObligation(
        model="CredentialsFile",
        prose_hash="1c10bbfb761d",
        structural=(
            "a `RootModel` whose `root` is a string-keyed object map carries "
            "the flat-file shape; keys and value types are deliberately "
            "unconstrained"
        ),
        waiver=(
            "the sidecar name correspondence is cross-document — a "
            "connection's `secret_refs` entries live in another document — "
            "and string-coercion on read is engine conduct"
        ),
    ),
    # === shared ==============================================================
    ProseObligation(
        model="StrictModel",
        prose_hash="280a934ea6fe",
        structural=(
            "the config and the constructor refusals the docstring describes — "
            "extra='forbid' rejects x-* keys like any unknown key, frozen=True "
            "rejects every assignment to a field, and the ParseOnly mixin "
            "refuses model_construct and model_copy(update=...); the stated "
            "limit (a list or dict a field holds stays mutable in place) is "
            "the boundary of that same mechanism, claimed as a limit rather "
            "than left to be discovered"
        ),
    ),
    ProseObligation(
        model="RetryErrorHandlingBase",
        prose_hash="df7e8cd3fe85",
        waiver=(
            "authoring convention about this module's own layering (a subclass "
            "re-declares a field only to attach a public description) — binds "
            "contract authors, not instances"
        ),
    ),
    ProseObligation(
        model="CorruptedPlaceholderBase", field="corrupted",
        prose_hash="8af218900ce3",
        structural="required Literal[True]",
    ),
    ProseObligation(
        model="CorruptedPlaceholderBase",
        prose_hash="fe44cca47757",
        structural=(
            "`wire` dumps by alias with exclude-none serialization, so an "
            "unreadable identity field is omitted rather than serialized as "
            "null; the pipeline placeholder's own `wire` override carries its "
            "legacy shape"
        ),
    ),
    ProseObligation(
        model="CorruptedPlaceholderBase", field="error", waiver=ENGINE_CONDUCT,
        prose_hash="882301d5edcb",
    ),
    # === type-map ============================================================
    ProseObligation(
        model="_TypeMapRuleBase", descriptive=True,
        prose_hash="3b29164f42c1",
    ),
    ProseObligation(
        model="TypeMapReadExactRule",
        prose_hash="bb49209dbee3",
        structural=(
            "`match` is the `Literal` discriminator, and `canonical` is typed "
            "`_ExactCanonical`, whose `ARROW_TYPE_PATTERN` field pattern "
            "holds the literal to the Arrow vocabulary"
        ),
    ),
    ProseObligation(
        model="TypeMapReadRegexRule",
        prose_hash="2226dbb14336",
        rule_ids=("RULE-TMAP-003", "RULE-TMAP-005", "RULE-TMAP-006"),
    ),
    ProseObligation(
        model="TypeMapWriteExactRule",
        prose_hash="f485f9589850",
        structural=(
            "`canonical` is typed `_ExactCanonical`, whose "
            "`ARROW_TYPE_PATTERN` field pattern holds the matcher to the "
            "Arrow vocabulary; the `native` DDL render is free-form"
        ),
    ),
    ProseObligation(
        model="TypeMapWriteRegexRule",
        prose_hash="2ba3689a611b",
        rule_ids=("RULE-TMAP-009",),
    ),
)
