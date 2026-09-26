# Package locations

Where each definition document of a connector package sits — the connector
document, its type map, each endpoint document — is read from
`https://schemas.analitiq.ai/connector-package/latest.json`, never from this
plugin's prose. Each `patternProperties` key is a location, and its `$ref`
names the kind of document that sits there; `required` names the root, the
connector document. An endpoint document's filename stem is its `endpoint_id`
(`RULE-PKG-031`), and the release directory is named for the `connector_id`
its connector document declares (`RULE-CTOR-042`).

Read that schema for placement only: to write, stage or read a document of the
package. It is never read to interpret a validator finding.
