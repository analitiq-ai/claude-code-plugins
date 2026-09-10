"""A wall-clock budget over the evaluation of one recorded sample.

Grading a recorded sample runs the author's own JSON Schema keywords over the
author's own value. Every other guard in this package is an `except` clause, so
between them they contain every way that evaluation *raises* and none of the ways
it simply does not come back: `pattern` is evaluated by Python's `re`, which
backtracks exponentially on an ambiguous pattern paired with a subject that nearly
matches, and no `except` clause can interrupt an evaluation that never finishes.
The stall reaches whatever runs the validator — a CI job until its own timeout, an
authoring agent until it gives up with no diagnostics at all, which is the "no
answer under a failing exit code" shape the guards exist to eliminate.

So the evaluation happens somewhere it can be abandoned. Python offers no way to
interrupt a running `re` match from the thread executing it, and none at all from
another thread — a match holds the interpreter so completely that a `join` with a
timeout does not itself return. An interval timer does interrupt one, but
`SIGALRM` does not exist on Windows and arming a timer off the main thread raises,
so a package other programs import in-process would silently lose the bound on
platforms and threads it cannot see. A separate process can always be killed.

The boundary is one worker per *document*, started on the first sample there is to
grade. A document recording nothing starts nothing; a document recording samples
pays one interpreter start, against the validator's own; and a sample that blows
its budget costs its budget plus one restart, leaving every other sample its
verdict. The budget the worker cannot outlast is per sample, and a second budget
caps the document, since the sample count is bounded only by the document's size
and a per-sample bound alone would therefore not bound the pass.

The worker is this module run as `-m`. It speaks JSON lines over its pipes and
holds the current schema and node between jobs, so a document is serialized once
rather than once per sample. What it returns carries only what a finding renders:
an unresolved reference's `ref` is sent already `repr`'d and never the resource it
was searched for in, so a message stays a finding rather than a copy of the
document that produced it.
"""
from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import tempfile
import threading
import time
from typing import Any

#: Wall clock for one sample. A sample the node can grade at all grades in well
#: under a millisecond, so this is orders of magnitude of headroom and will not
#: fire on slow hardware — what it has to separate is a bounded evaluation from an
#: unbounded one, and between those the gap is astronomical.
SAMPLE_BUDGET_SECONDS = 2.0

#: Wall clock for every sample in one document together. Without it a document
#: could hold as many pathological samples as it has room for and spend the
#: per-sample budget on each, which is a bound on no useful quantity.
DOCUMENT_BUDGET_SECONDS = 10.0

#: How long the worker gets to start and answer a handshake. Not evaluation time
#: and so not charged to the document budget: it covers an interpreter start on a
#: cold or contended machine, which is unrelated to what any sample costs.
_START_DEADLINE_SECONDS = 30.0

_WORKER_MODULE = __name__


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
    """Read jobs from stdin, write one verdict line per graded sample.

    Held between jobs so a document crosses the pipe once: the schema, which is
    also what roots every in-document reference, and the node under it.
    """
    from jsonschema import Draft202012Validator

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
            verdict: dict = {"v": "pong"}
        else:
            verdict = _grade(document, node, job["value"])
        sys.stdout.write(json.dumps(verdict) + "\n")
        sys.stdout.flush()
    return 0


# ---------------------------------------------------------------------------
# The parent
# ---------------------------------------------------------------------------

class BudgetedGrader:
    """Grades samples in a worker process that a breached budget can kill.

    Started lazily, so a document with nothing to grade starts nothing. Killed
    and restarted on a breach, because the worker is stuck inside an evaluation
    and there is no other way to get it back.

    Not reusable across documents: the document budget is spent, not reset.
    """

    def __init__(self, sample_budget: float = SAMPLE_BUDGET_SECONDS,
                 document_budget: float = DOCUMENT_BUDGET_SECONDS) -> None:
        self._sample_budget = sample_budget
        self._remaining = document_budget
        self._proc: subprocess.Popen | None = None
        self._replies: queue.Queue | None = None
        self._stderr = None
        self._unavailable: str | None = None
        # Identity, not equality: the caller hands the same objects down its own
        # walk, and comparing whole documents per sample would cost more than
        # resending them.
        self._schema: Any = None
        self._node: Any = None

    def __enter__(self) -> "BudgetedGrader":
        return self

    def __exit__(self, *_exc) -> None:
        self.close()

    def grade(self, schema: Any, node: dict, sample: Any) -> dict:
        """This sample's verdict — one of the worker's, or the budget's own.

        `{"v": "budget"}` and `{"v": "exhausted"}` say nothing was decided about
        the sample, which is the same shape as an unresolvable reference and is
        reported the same way. They are never a pass.
        """
        if self._remaining <= 0:
            return {"v": "exhausted"}
        if self._proc is None and not self._start():
            return {"v": "unavailable", "reason": self._unavailable}
        if schema is not self._schema:
            self._send({"op": "schema", "value": schema})
            self._schema = schema
            self._node = None
        if node is not self._node:
            self._send({"op": "node", "value": node})
            self._node = node

        budget = min(self._sample_budget, self._remaining)
        started = time.monotonic()
        verdict = self._exchange({"op": "grade", "value": sample}, budget)
        self._remaining -= time.monotonic() - started
        if verdict is _TIMED_OUT:
            # The worker is inside the evaluation and will not come back to be
            # asked anything else. Killing it IS the interrupt.
            self._kill()
            return {"v": "budget", "seconds": budget}
        if verdict is _WORKER_GONE:
            self._kill()
            self._unavailable = "the grading worker exited while grading it"
            return {"v": "unavailable", "reason": self._unavailable}
        return verdict

    def close(self) -> None:
        self._kill()
        if self._stderr is not None:
            self._stderr.close()
            self._stderr = None

    # -- worker lifecycle ---------------------------------------------------

    def _start(self) -> bool:
        """Start a worker and prove it answers. False leaves a stated reason.

        The handshake is what separates an environment that cannot host a worker
        from a sample that cannot be graded, so the first sample is not blamed
        for the former.
        """
        if self._unavailable is not None:
            return False
        if not sys.executable:
            self._unavailable = "this interpreter does not report its own path"
            return False
        # The worker must import what this process imported. Passing the path
        # explicitly covers a run from a source checkout, where the package is
        # importable only because something put it on the path.
        env = {**os.environ,
               "PYTHONPATH": os.pathsep.join(p for p in sys.path if p)}
        if self._stderr is None:
            # A real file, not a pipe: the parent reads it only after a failure,
            # and a pipe nobody drains is a way for the worker to block forever
            # on a diagnostic.
            self._stderr = tempfile.TemporaryFile(mode="w+")
        try:
            self._proc = subprocess.Popen(  # noqa: S603 - argv is this interpreter and this module
                [sys.executable, "-m", _WORKER_MODULE],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=self._stderr, text=True, env=env)
        except OSError as exc:
            self._proc = None
            self._unavailable = f"the grading worker could not be started ({exc})"
            return False

        self._replies = queue.Queue()
        # Daemon: if the worker outlives an abandoned parent, this thread must
        # not keep the interpreter alive waiting on its pipe.
        threading.Thread(target=_drain, args=(self._proc.stdout, self._replies),
                         daemon=True).start()
        if self._exchange({"op": "ping"}, _START_DEADLINE_SECONDS) != {"v": "pong"}:
            self._kill()
            self._unavailable = (
                f"the grading worker did not start ({self._worker_stderr()})")
            return False
        return True

    def _kill(self) -> None:
        if self._proc is not None:
            self._proc.kill()
            self._proc.wait()
        self._proc = None
        self._replies = None
        # A restarted worker holds nothing, so neither may the record of what it
        # was sent.
        self._schema = None
        self._node = None

    def _worker_stderr(self) -> str:
        if self._stderr is None:
            return "no diagnostic"
        self._stderr.seek(0)
        text = self._stderr.read().strip()
        return text.splitlines()[-1] if text else "no diagnostic"

    # -- the pipe -----------------------------------------------------------

    def _send(self, message: dict) -> None:
        """State the worker holds until the next job. A pipe that refuses it is
        not reported here: the exchange that follows reports it as the sample's
        outcome, where a finding can name the sample."""
        try:
            self._proc.stdin.write(json.dumps(message) + "\n")
            self._proc.stdin.flush()
        except (OSError, ValueError):
            pass

    def _exchange(self, message: dict, budget: float) -> Any:
        try:
            self._proc.stdin.write(json.dumps(message) + "\n")
            self._proc.stdin.flush()
        except (OSError, ValueError):
            return _WORKER_GONE
        try:
            line = self._replies.get(timeout=budget)
        except queue.Empty:
            return _TIMED_OUT
        if line is None:
            return _WORKER_GONE
        try:
            return json.loads(line)
        except json.JSONDecodeError:
            return _WORKER_GONE


class _Signal:
    """A verdict the worker did not send, distinguishable from one it did."""

    def __init__(self, name: str) -> None:
        self._name = name

    def __repr__(self) -> str:  # pragma: no cover - diagnostics only
        return self._name


_TIMED_OUT = _Signal("_TIMED_OUT")
_WORKER_GONE = _Signal("_WORKER_GONE")


def _drain(stream, replies: queue.Queue) -> None:
    """Move the worker's lines onto a queue the parent can wait on with a
    deadline. Reading a pipe is the one wait Python can time out portably, and
    only from a thread that is not the one holding the deadline."""
    try:
        for line in stream:
            replies.put(line)
    finally:
        replies.put(None)


if __name__ == "__main__":
    sys.exit(_serve())
