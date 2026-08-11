# Endpoint identity

How `endpoint_id` relates to the resource it addresses — one model across API
and database connectors.

## The rule

`endpoint_id` is a **lookup handle, not the target** (`RULE-DBEP-007`). The
engine uses it only to resolve the on-disk file (`endpoints/{endpoint_id}.json`)
and to namespace checkpoint / state. The **verbatim locator** lives in a
separate field the engine reads:

- **API** — `operations.<op>.request.path`; the request URL is built from this.
- **Database** — `database_object.{catalog,schema,name}`, whose identifiers
  `analitiq.contracts.endpoints.DatabaseObject` stores verbatim from
  introspection; the engine dialect-quotes them into the qualified SQL
  identifier.

Because the engine acts on the verbatim field, the handle can be derived,
sanitized, or hashed freely — as long as it is unique and matches the slug
charset (see Invariants below). Same locator ⇒ same handle
on every re-author / re-discovery (idempotent).

## Derivation differs by source material, not philosophy

- **API — lossless flatten.** URL paths are charset-safe, so the full locator
  encodes losslessly into the handle. The exact operational rule (delimiter,
  version handling, path-param treatment, worked example) lives in the
  `resources[].key` description in `io-contracts.md` — authoritative there, not
  restated here. The derivation is enforced (`RULE-ENDP-046`), including the
  case where the locator admits no derivable handle at all.
- **Database — sanitizing slug + hash.** Object names are hostile to the
  charset (case-sensitive quoted identifiers, dots, spaces, unicode), so a
  lossless encode is impossible. The handle slugs the object's identifiers and
  appends a stable hash over the *exact verbatim* `catalog`, `schema` and
  `name`; the derivation named below is the authority on the composition. For a
  catalog-less `Sales."Order Items"` that is `sales__order_items__0e62f7e9`. The
  slug keeps it legible; the hash guarantees uniqueness and determinism.

  The database derivation ships in the contract package as
  `analitiq.contracts.endpoint_identity.derive_db_endpoint_id`, and both the
  endpoint document (`RULE-DBEP-011`) and the stream that pins it
  (`RULE-STRM-003`) are checked against it, so it is never
  reimplemented — this plugin authors no database endpoints, and the runtime
  discovery that does produce them derives the id from that function. (The API
  flatten lives in the validator, which recomputes it to enforce
  `RULE-ENDP-046`.)

## Invariants

- Charset `^[a-z0-9][a-z0-9_-]*$`; `__` is reserved as the level delimiter.
- The file ships at `endpoints/{endpoint_id}.json` (`RULE-PKG-031`).
- Unique across the release (`RULE-PKG-032`); a database endpoint gets
  uniqueness from its derivation (`RULE-DBEP-011`).
- The verbatim locator field is the **sole** source for building the query /
  request (`RULE-DBEP-007`).

## Immutability (`RULE-ENDP-043`)

A released `endpoint_id` is never renamed: streams pin endpoints by id, and the
pin does not fail loudly — it resolves to nothing. A resource whose locator
moves therefore ships as a new endpoint document beside a removal of the old
one. The removal is what sets the release bump — `metadata-and-versioning.md`
§Release version (`version`) renders the tier.
