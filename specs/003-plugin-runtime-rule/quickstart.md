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

Both parts run in one throwaway worktree, with seeds as uncommitted changes.

1. **`/rules-audit` end to end.** Seed S23 and S33 together and run `/rules-audit` over the copy:
   its selection step and the plugin-runtime reviewer it dispatches. Row A1.
2. **Clause matrix.** One row per clause of `.claude/rules/plugin-runtime.md`; a clause with a
   condition has a row on each side of it. Every plugin seed is applied, and one read-only reviewer
   agent, given the rule as its whole standard, returns a verdict per seed judged only on what that
   seed added.

| Row | Seed | Expected | Result (2026-10-05, rule at `ce5d0901`) |
|---|---|---|---|
| A1 | S23 and S33 together, under `/rules-audit` | `plugin-runtime.md` selected by the plugin agent only; its reviewer flags S23 | as expected: selected by `endpoint-creator.md` alone, FLAG under "An agent that can reach a shell" |
| | **What a plugin may use — Claude Code primitives and built-in tools** | | |
| S1 | A new plugin skill whose `allowed-tools` is `Read, Glob, Grep` | PASS | PASS |
| S2 | A new plugin agent with `tools: Read, Glob, Grep, WebFetch, WebSearch` | PASS | PASS |
| S3 | Plugin prose requiring a third-party plugin that ships only agents, skills and a remote `http` MCP server | PASS | PASS |
| S4 | Plugin prose requiring a third-party plugin whose `stdio` MCP server runs Python | FLAG | FLAG |
| | **Hooks** | | |
| S5 | A `prompt` hook | PASS | PASS |
| S6 | An `agent` hook | PASS | PASS |
| S7 | An `http` hook to a remote `https://` URL | PASS | PASS |
| S8 | An `http` hook to `localhost` | FLAG | FLAG |
| S9 | An `mcp_tool` hook calling the server the plugin's `.mcp.json` declares | PASS | PASS |
| S10 | An `mcp_tool` hook calling a server the plugin does not declare | FLAG | FLAG |
| S11 | A `command` hook | FLAG | FLAG |
| | **MCP servers** | | |
| S12 | A third-party remote `http` server in `.mcp.json` | PASS | PASS |
| S13 | A `stdio` server in `.mcp.json` | FLAG | FLAG |
| S14 | An `http` server at `localhost` in `.mcp.json` | FLAG | FLAG |
| | **What it may not — anything run on the user's machine** | | |
| S15 | A `python3 scripts/…` step in a plugin agent's prose | FLAG | FLAG |
| S16 | A monitor in `monitors/monitors.json` | FLAG | FLAG |
| S17 | An executable under `bin/` | FLAG | FLAG |
| S18 | An LSP server in `.lsp.json` | FLAG | FLAG |
| | **What it may not — a shell command as a step** | | |
| S19 | A step naming only POSIX utilities in a plugin agent's prose | FLAG | FLAG |
| S20 | Plugin prose asking the user to run a shell command | FLAG | FLAG |
| S21 | A generated "run `python scripts/…` to regenerate" comment | PASS | PASS |
| S22 | Optional "review the result with `git diff`" advice | PASS | PASS |
| | **What it may not — an agent that can reach a shell** | | |
| S23 | `Bash` in a plugin agent's `tools:` | FLAG | FLAG |
| S24 | `Bash` in a plugin skill's `allowed-tools` | FLAG | FLAG |
| S25 | `PowerShell` in a plugin command's `allowed-tools` | FLAG | FLAG |
| S26 | A plugin agent with no `tools:` line | FLAG | FLAG |
| S27 | `Agent` in a plugin agent's `tools:` | FLAG | FLAG |
| S28 | `Task` in a plugin agent's `tools:` | FLAG | FLAG |
| S29 | Plugin prose dispatching an agent the plugin defines | PASS | PASS |
| S30 | Plugin prose dispatching a `general-purpose` agent | FLAG | FLAG |
| | **No exception path** | | |
| S31 | A file fetched through a declared remote MCP server and landed with `Write` | PASS | PASS |
| S32 | A plugin ADR stating that users install Python | FLAG | FLAG |
| | **Scope** | | |
| S33 | A change only under `tests/` | rule not selected | not selected (A1) |

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
