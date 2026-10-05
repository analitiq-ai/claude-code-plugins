# Quickstart: validate the plugin-runtime rule

Run every command from the worktree root.

## 1. The diff is confined (SC-002, FR-008)

```bash
git diff --name-only origin/main...HEAD
```

Expected: only `.claude/rules/plugin-runtime.md`, `CLAUDE.md` and paths under `specs/`. Nothing
under `plugins/`.

## 2. Every requirement maps to a sentence (SC-001)

Read `.claude/rules/plugin-runtime.md` against the table in [data-model.md](data-model.md). For
each of FR-002–FR-006 and FR-010–FR-013, name the sentence that states it. This is a reader's
check (`guards.md`), not a grep.

## 3. The rule is reachable (US1)

- In the root `CLAUDE.md` Rules section, the `plugin-runtime.md` entry is there with its trigger.
- The rule's frontmatter `paths:` is `plugins/**` (see [contracts/rule-file.md](contracts/rule-file.md)).

## 4. The rule grades a violation (US2)

Run `/rules-audit` against scratch diffs, in a throwaway copy of the repo and never committed,
one per case. Record FLAG or PASS for each.

| Scratch diff | Expected | Result (2026-10-05, final rule text) |
|---|---|---|
| A `python3 …` step in a plugin agent's prose | FLAG | FLAG |
| A step naming only POSIX utilities in a plugin agent's prose | FLAG | FLAG |
| A `command` hook in `plugins/<name>/hooks/hooks.json` | FLAG | FLAG |
| An `http` hook to `localhost` | FLAG | FLAG |
| An `mcp_tool` hook calling an undeclared server | FLAG | FLAG |
| A `stdio` server in a plugin's `.mcp.json` | FLAG | FLAG |
| An executable under `plugins/<name>/bin/` | FLAG | FLAG |
| `Bash` in a plugin agent's `tools:` | FLAG | FLAG |
| `Bash` in a plugin skill's `allowed-tools` | FLAG | FLAG |
| A plugin agent with no `tools:` line | FLAG | FLAG |
| Plugin prose dispatching a `general-purpose` agent | FLAG | FLAG |
| `Agent` in a plugin agent's `tools:` | FLAG | FLAG |
| `Task` in a plugin agent's `tools:` | FLAG | FLAG |
| Plugin prose requiring a third-party plugin that runs a `stdio` MCP server | FLAG | FLAG |
| A `prompt` hook | PASS | PASS |
| An `agent` hook | PASS | PASS |
| An `http` hook to a remote URL | PASS | PASS |
| An `mcp_tool` hook calling the server the plugin's `.mcp.json` declares | PASS | PASS |
| A third-party remote `http` server in `.mcp.json`, needing sign-in | PASS | PASS |
| A file fetched through a declared remote MCP server and landed with `Write` | PASS | PASS |
| A generated "run `python scripts/…` to regenerate" comment | PASS | PASS |
| Optional "review the result with `git diff`" advice in a README | PASS | PASS |
| A change only under `tests/` | rule not selected | rule not selected |

## 5. The branch passes its own rules (SC-003)

```text
/rules-audit
```

Expected: no finding against any rule in `.claude/rules/`.

## 6. The suite still passes (SC-004)

```bash
python -m pytest -q
```

Expected: green, with the same result as on `main`.
