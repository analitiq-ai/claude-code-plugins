# Server-managed fields

An authored document declares only what its model names; every other key is
rejected (`RULE-SHRD-014`). The registry stamps the rest on insert and update,
so a validator finding naming an unknown field is usually a server-managed name
that leaked in from a fetched document — drop the field rather than looking for
a way to author it. The authorable set per entity is the generated field table
in that entity's spec skill.

## Reservation is per-namespace

A name reserved on an artifact is reserved **only there**. It does not reserve
the same name in a provider-owned namespace: a database column may legitimately
be called `version`, an operation parameter `org_id`. Reserve nothing on the
provider's behalf — copy discovered names verbatim.
