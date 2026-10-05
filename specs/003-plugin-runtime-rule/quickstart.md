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

Each part runs in its own throwaway worktree, with seeds as uncommitted changes. The results
below are for the rule text committed together with this table; a later change to the rule means
running both parts again.

1. **`/rules-audit` end to end.** Seed S18 together with a change only under `tests/`, and run
   `/rules-audit` over the copy: its selection step and the plugin-runtime reviewer it dispatches.
   Row A1.
2. **Clause matrix.** A clause with a condition has a row on each side of it. Every seed is
   applied, and one read-only reviewer agent, given the rule as its whole standard, returns a
   verdict per seed judged only on what that seed added.

| Row | Seed | Expected | Result (2026-10-05) |
|---|---|---|---|
| A1 | S18 and a `tests/`-only change, under `/rules-audit` | `plugin-runtime.md` selected by the plugin files only; its reviewer flags the shipped script and the `python3` step | as expected: selected by `endpoint-creator.md` and the shipped `.py` file, not by the `tests/` file; FLAG under "Ship a script or an executable" and "Need anything the user installs" |
| | **What a plugin may use — tools and agents** | | |
| S1 | `Bash` in a plugin skill's `allowed-tools` | PASS | PASS |
| S2 | `Bash` in a plugin agent's `tools:` | PASS | PASS |
| S3 | A plugin agent with no `tools:` line | PASS | PASS |
| S4 | `Agent` in a plugin agent's `tools:`, dispatching a plugin agent | PASS | PASS |
| S5 | Plugin prose dispatching a `general-purpose` agent | PASS | PASS |
| | **What a plugin may use — other plugins** | | |
| S6 | Plugin prose requiring a plugin that ships only agents, skills and a remote `http` MCP server | PASS | PASS |
| S7 | Plugin prose requiring a plugin whose `stdio` MCP server runs Python | FLAG | FLAG |
| | **Hooks** | | |
| S8 | A `prompt` hook | PASS | PASS |
| S9 | An `http` hook to a remote `https://` URL | PASS | PASS |
| S10 | An `http` hook to `localhost` | FLAG | FLAG |
| S11 | An `mcp_tool` hook calling the server the plugin's `.mcp.json` declares | PASS | PASS |
| S12 | An `mcp_tool` hook calling a server the plugin does not declare | FLAG | FLAG |
| S13 | A `command` hook running a `.sh` file the plugin ships | FLAG | FLAG |
| S14 | A `command` hook whose command is `echo` | PASS | PASS |
| | **MCP servers** | | |
| S15 | A third-party remote `http` server added to `.mcp.json` | PASS | PASS |
| S16 | A `stdio` server added to `.mcp.json` | FLAG | FLAG |
| S17 | An `http` server at `localhost` added to `.mcp.json` | FLAG | FLAG |
| | **What it may not — ship a script or an executable** | | |
| S18 | A `.py` file under the plugin and agent prose running it with `python3` | FLAG | FLAG |
| S19 | A `.sh` file under the plugin that the README tells the user to run | FLAG | FLAG |
| S20 | An executable under `bin/` | FLAG | FLAG |
| S21 | A `.py` template the agent copies into the user's connector with `Write` and never runs | PASS | PASS |
| | **What it may not — need anything the user installs** | | |
| S22 | An agent step invoking `jq` | FLAG | FLAG |
| S23 | An agent step invoking `gh` | FLAG | FLAG |
| S24 | An LSP server in `.lsp.json` | FLAG | FLAG |
| S26 | A generated "run `python scripts/…` to regenerate" comment | PASS | PASS |
| S27 | Optional "review the result with `git diff`" advice | PASS | PASS |
| S30 | An agent step calling an MCP server the plugin does not declare | FLAG | FLAG |
| | **No exception path** | | |
| S28 | A file read through a remote MCP server the plugin declares and landed with `Write` | PASS | PASS |
| S29 | A plugin README telling the user to install Python | FLAG | FLAG |
| | **Not covered — which shell a command is written for** | | |
| S25 | An agent step using `mktemp -d` and `tar -xzf` | PASS | PASS |
| | **Scope** | | |
| S31 | A change only under `tests/` | rule not selected | not selected (A1) |

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
