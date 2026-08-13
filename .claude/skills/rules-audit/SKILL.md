---
name: rules-audit
description: Audit a diff against the authoring rules in .claude/rules/ — selects every rule whose paths frontmatter matches a changed file and runs one reviewer agent per selected rule. Use before opening a PR, as part of the PR review loop, or when asked to audit a branch, PR, or working tree for rule compliance ("rules audit", "audit the rules", "check the diff against our rules").
---

# Rules audit

Reads a diff, selects every rule in `.claude/rules/` whose `paths:` frontmatter
matches a changed file, and reviews the diff against each selected rule with
one agent per rule. The frontmatter is the only trigger map — the same globs
the native rules loader uses at authoring time. Never add a file→rule mapping
here; that would be the hand-maintained second copy
`.claude/rules/no-drift-surfaces.md` forbids.

This audit is a reader, not a guard (`.claude/rules/guards.md`): its verdicts
are semantic and advisory. It reports findings; it never fixes, blocks, or
edits anything.

## 1. Resolve the diff

From the invocation argument:

- **PR number** → `gh pr diff <n> --name-only` for the file list,
  `gh pr diff <n>` for hunks.
- **Ref/branch** → `git diff $(git merge-base origin/main <ref>) <ref>`.
- **Nothing** → the current work: branch commits since
  `$(git merge-base origin/main HEAD)` plus uncommitted changes
  (`git diff HEAD` covers staged and unstaged).
- **A path to another checkout** → same commands with `git -C <path>`.

The audit judges what the change introduces: only added/modified lines, never
pre-existing text outside the hunks.

## 2. Select the rules

Match the changed files against each rule's `paths:` globs, with loader
semantics: `*` and `?` do not cross `/`, `**` does; a rule with no `paths:`
frontmatter applies to every diff. Run this (file list one path per line in
`changed.txt`, from the repo root):

```python
import re, sys
from pathlib import Path

def to_rx(g):
    g = re.escape(g)
    g = g.replace(r"\*\*/", "(?:.*/)?").replace(r"\*\*", ".*")
    g = g.replace(r"\*", "[^/]*").replace(r"\?", "[^/]")
    return re.compile(g + "$")

changed = [l.strip() for l in open(sys.argv[1]) if l.strip()]
for rule in sorted(Path(".claude/rules").glob("*.md")):
    m = re.match(r"^---\n(.*?)\n---", rule.read_text(), re.S)
    globs = re.findall(r'-\s*"([^"]+)"', m.group(1)) if m else []
    hits = sorted({f for f in changed for g in globs if to_rx(g).match(f)}) \
        if globs else list(changed)
    if hits:
        print(rule.name)
        for f in hits:
            print("  ", f)
```

## 3. Review — one agent per selected rule, in parallel

Launch the reviewers concurrently, read-only agents, one per rule. Each prompt
contains:

- the rule file to read in full — it is the entire standard; judge against
  nothing else, and do not import taste from outside it;
- the matching changed files and the exact `git diff` command for their hunks;
- the instruction: judge only added/modified lines; for each finding cite
  `file:line`, quote the offending sentence, and name the section of the rule
  it violates; return PASS when nothing violates the rule. Findings only — no
  fixes, no restyling suggestions.

## 4. Report

One summary, findings-first:

- per rule: PASS, or the findings with `file:line`, quoted sentence, violated
  section;
- where a finding lands on a `.github/pull_request_template.md` attestation
  checkbox, name the checkbox — the audit turns that attestation into a
  checked claim;
- close with which rules were triggered and by which files, so a clean run
  still shows what was actually audited.

Do not edit anything. Fixing what the audit found is the caller's next
decision, not this skill's.
