# Contract: rule file read by the rules loader and `rules-audit`

The only interface this feature exposes. The rule file is plain Markdown whose frontmatter two
consumers read.

## Frontmatter

```yaml
---
paths:
  - "plugins/**"
---
```

- **Claude Code rules loader**: loads the rule into context when a file matching `paths` is read
  or edited.
- **`rules-audit` skill**: selects the rule for every diff that touches a matching file and runs
  one reviewer agent against the body.

A rule without `paths:` always applies. That is not wanted here, because FR-006 limits the scope
to the plugin runtime.

## Body

Free Markdown with an `# Rule:` title line, matching the sibling rules. Each FR in
`data-model.md` must map to at least one sentence in the body (SC-001). A reviewer agent reads the
body as its grading brief, so each forbidden thing is stated as something a diff can be checked
against.
