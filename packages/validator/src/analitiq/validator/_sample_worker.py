"""The process a budgeted grading job runs in.

Its own module, and one that imports nothing from this package. It is executed as
a SCRIPT — the parent starts it by path, because `-m analitiq.validator…` would
import the contract models and pydantic into a child that uses neither, and a
script has no package to resolve a relative import against. Keeping this half in
the parent's module would make any import that half grows a start-up failure for
every worker.

Its stdout is the protocol and nothing else may write there, so the channel is a
private duplicate of the original descriptor and the inherited one is pointed at
stderr. What it answers carries only what a finding renders: an unresolved
reference is sent already `repr`'d and never the resource it was searched for in.
"""
from __future__ import annotations

import faulthandler
import json
import os
import sys
from pathlib import Path
from typing import Any

#: How far past its deadline a worker lets a job run before ending its own
#: process. Wide enough that the parent, which reports the overrun, gets there
#: first while it is alive; narrow enough that an orphan does not outlive the run
#: by much. Racing is harmless either way — a parent that timed out kills a
#: process that has already gone, and reports the same breach.
_SELF_DESTRUCT_MARGIN = 2.0

def _offline_registry():
    """An empty reference registry — one with no way to retrieve anything.

    Validation here is offline by contract, and `jsonschema`'s default registry
    is not: a `$ref` naming an `http(s)` URL is FETCHED, so an authored endpoint
    could make the validator issue a request to any address its author chose and
    block on the answer. Retrieval is a `retrieve` callable a registry either has
    or does not, so an empty one refuses instead, and the per-entry guard turns
    the refusal into a finding.

    Empty is all it has to be, and this is the fact that makes the whole scheme
    work: a validator roots its OWN schema as a resource, and that root is what
    every in-document reference resolves against — a pointer, an `$anchor`, a
    nested `$id`. So the document needs no registry entry, and a registry with
    nothing in it can still resolve every reference the contract allows while
    refusing every one it does not.

    That the contract models separately refuse a non-local `$ref` does not cover
    this: grading runs on documents the models have already rejected, and an
    offline guarantee that holds only while another check keeps its rule is not
    one."""
    from referencing import Registry

    return Registry()


# ---------------------------------------------------------------------------
# The worker
# ---------------------------------------------------------------------------

def _grade(document, node: dict, sample: Any) -> dict:
    """One sample's verdict, as the data a finding needs rather than as objects.

    The arms are the ones the caller already tells apart, kept apart here because
    each has a different fix: a reference naming nothing is the node's defect, a
    value no keyword can evaluate is the sample's, and a verdict is neither.
    """
    from jsonschema.exceptions import best_match
    from referencing.exceptions import Unresolvable

    try:
        # `evolve` swaps the schema and keeps the resolver, so a node written as
        # `{"$ref": "#/$defs/..."}` is graded against what it points at rather
        # than against a node with no assertions in it.
        error = best_match(document.evolve(schema=node).iter_errors(sample))
    except (Unresolvable, RecursionError) as exc:
        ref = getattr(exc, "ref", None)
        # `repr` here rather than in the parent: an unresolved-reference error
        # renders the whole resource it searched, and `ref` is the part of it
        # that names the defect. Sending anything else would put the embedded
        # schema on the wire once per recorded sample.
        return {"v": "unresolvable", "ref": repr(ref)} if ref is not None else {"v": "recursion"}
    except Exception as exc:  # noqa: BLE001 - author input, no total gate
        return {"v": "crash", "type": type(exc).__name__, "detail": str(exc)}
    if error is None:
        return {"v": "graded", "error": None}
    return {"v": "graded", "error": {"message": error.message, "path": error.json_path}}


def _serve() -> int:
    """Read jobs from stdin, write one verdict line per graded sample."""
    from jsonschema import Draft202012Validator

    # The protocol channel is this process's original stdout, taken privately.
    # Anything else here that writes to fd 1 — a library banner, a `print` in a
    # dependency, a warning routed to stdout — would otherwise land mid-verdict
    # and desynchronise the parent for every remaining sample. Pointing fd 1 at
    # stderr keeps such writes visible as diagnostics instead of destroying the
    # channel.
    channel = os.fdopen(os.dup(1), "w", encoding="utf-8")
    os.dup2(2, 1)
    sys.stdout = sys.stderr

    document = None
    node: dict = {}
    for line in sys.stdin:
        job = json.loads(line)
        op = job["op"]
        if op == "schema":
            document = Draft202012Validator(job["value"], registry=_offline_registry())
            continue
        if op == "node":
            node = job["value"]
            continue
        if op == "ping":
            verdict = {"v": "pong"}
        else:
            # A Python-level watchdog cannot do this: a runaway match holds the
            # interpreter outright, so no other thread in this process runs at
            # all while one is going — measured, not assumed. `faulthandler`'s
            # is a native thread that needs neither the interpreter nor the
            # evaluating thread to yield, which makes it the only thing here
            # that can still act. It writes its traceback to stderr, never to
            # the channel fd this worker took for itself.
            faulthandler.dump_traceback_later(
                job["deadline"] + _SELF_DESTRUCT_MARGIN, exit=True)
            try:
                verdict = _grade(document, node, job["value"])
            finally:
                faulthandler.cancel_dump_traceback_later()
        channel.write(json.dumps(verdict) + "\n")
        channel.flush()
    return 0



if __name__ == "__main__":
    # Run by path, so Python prepended this file's own directory to `sys.path`,
    # where a sibling module could shadow a name the grader's own imports need.
    # The worker takes nothing from that directory.
    if sys.path and os.path.realpath(sys.path[0]) == str(Path(__file__).resolve().parent):
        del sys.path[0]
    sys.exit(_serve())
