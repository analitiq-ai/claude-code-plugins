"""Walking a rule corpus that ships inside a package.

Both shipped corpora are laid out the same way —
``<corpus>/<RULE-ID>/{valid,invalid}/<item>``: the single-document fixtures
here (:mod:`analitiq.contracts.shared.rule_fixtures`) and the cross-document
cases in ``analitiq-validator`` (``analitiq.validator.rule_cases``). The layout
is one decision, so the walk that enforces it is written once here, and each
corpus adds only what is its own: what an item is, and what binds a rule id to
that corpus.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Iterator, Literal, get_args

Verdict = Literal["valid", "invalid"]

VERDICTS: tuple[Verdict, ...] = get_args(Verdict)


def corpus_items(
    corpus_dir: Path, resolve_rule: Callable[[str], Any]
) -> Iterator[tuple[str, Verdict, Any, Path]]:
    """Every item in the corpus, sorted by rule id, verdict and name, as
    ``(rule_id, verdict, resolved, path)``.

    ``resolve_rule`` is how a corpus says which rules belong in it: it is given
    each rule id and raises ``ValueError`` for one that does not, and whatever
    it returns — the model a fixture validates against, or nothing — comes back
    with every item of that rule, so the caller resolves once per rule rather
    than once per item.

    A group directory that names no verdict raises here, because the layout is
    what this walk owns. What an item *is* — a JSON file, a directory holding a
    document set — is the caller's to check.
    """
    for rule_dir in sorted(corpus_dir.iterdir()):
        resolved = resolve_rule(rule_dir.name)
        for group_dir in sorted(rule_dir.iterdir()):
            if group_dir.name not in VERDICTS:
                raise ValueError(f"{group_dir}: a corpus group is one of {VERDICTS}")
            for path in sorted(group_dir.iterdir()):
                yield rule_dir.name, group_dir.name, resolved, path
