# Package locations

Where each definition document of a connector package sits — the connector
document, its type map, each endpoint document — is read from
`https://schemas.analitiq.ai/connector-package/latest.json`, never from this
plugin's prose. An endpoint document's filename stem is its `endpoint_id`
(`RULE-PKG-031`), and the release directory is named for the `connector_id`
its connector document declares (`RULE-CTOR-042`).

<!-- BEGIN GENERATED: package-location-lookup -->
In a package or workspace schema, each `patternProperties` key is a location: a pattern matched against a path from that package's own directory, or from the workspace root. Its `$ref` is the schema of the document or package that sits there. A package's root document is its schema's `required` entry, and a location marked `x-secret` holds secret values.
<!-- END GENERATED: package-location-lookup -->

Read that schema for placement only: to write, stage or read a document of the
package. It is never read to interpret a validator finding.
