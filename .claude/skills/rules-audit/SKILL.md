---
name: rules-audit
description: Audit a diff against the authoring rules in .claude/rules/ — selects every rule whose paths frontmatter matches a changed file (a rule without paths always applies) and runs one reviewer agent per selected rule. Use before opening a PR, as part of the PR review loop, or when asked to audit a branch, PR, or working tree for rule compliance ("rules audit", "audit the rules", "check the diff against our rules").
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
- **Ref/branch** → `git diff $(git merge-base origin/main <ref>) <ref>`;
  add `--name-only` for the file list.
- **Nothing** → the current work, committed, staged and unstaged in one range:
  `git diff $(git merge-base origin/main HEAD)`; add `--name-only` for the
  file list. Untracked files never appear in that diff — take them from
  `git status --porcelain` and audit their full content as added lines.
- **A path to another checkout** → the same commands with **every** git
  invocation carrying `-C <path>`, including the `git merge-base` inside the
  command substitution — otherwise the range resolves in the wrong checkout.
  Hand the same path to the selection snippet as its root.

The audit judges what the change introduces: only added/modified lines, never
pre-existing text outside the hunks.

## 2. Select the rules

Match the changed files against each rule's `paths:` globs. A rule with no
`paths:` frontmatter applies to every diff, mirroring the loader's documented
unconditional load; matching delegates to `pathlib.PurePath.full_match`
(Python 3.13+) rather than restating glob semantics by hand. Run this with
two arguments — the checkout root and a file listing one changed path per
line:

```python
import sys
from pathlib import Path, PurePosixPath

if sys.version_info < (3, 13):
    sys.exit("needs Python 3.13+ (PurePath.full_match)")

root = Path(sys.argv[1])
changed = [l.strip() for l in open(sys.argv[2]) if l.strip()]
rules = sorted((root / ".claude/rules").rglob("*.md"))
if not rules:
    sys.exit(f"no rules under {root}/.claude/rules — wrong root; refusing "
             "to report a vacuously clean audit")
for rule in rules:
    lines = rule.read_text().splitlines()
    globs, in_paths, has_key = [], False, False
    for line in (lines[1:] if lines and lines[0] == "---" else []):
        if line == "---":
            break
        if line == "paths:":
            in_paths = has_key = True
        elif line.startswith("paths:"):
            sys.exit(f"{rule}: unsupported paths: spelling")
        elif in_paths and line.lstrip().startswith("- "):
            globs.append(line.lstrip()[2:].strip().strip("\"'"))
        elif not line.startswith((" ", "\t")):
            in_paths = False
    if has_key and not globs:
        sys.exit(f"{rule}: paths: present but no items parsed")
    hits = sorted({f for f in changed for g in globs
                   if PurePosixPath(f).full_match(g)}) if globs \
        else list(changed)
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
- the matching changed files and the exact diff command for their hunks;
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
