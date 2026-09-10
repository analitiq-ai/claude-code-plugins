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
grade, so a document recording nothing starts nothing. A sample that outlasts its
budget costs its budget plus one restart, and the samples around it still get their
verdicts. A second budget caps the document, since the sample count is bounded only
by the document's size; it charges everything a sample costs the caller except the
first worker start, which is setup rather than evaluation and has its own deadline.

**Every outcome that is not a verdict is reported as one.** A breach, a spent
document budget, a worker that cannot start, a worker that dies, a reply that is not
a verdict, a value that cannot be encoded — each returns a kind the caller renders
as a finding naming the sample and saying nothing was decided about it. None is ever
a pass, and none costs another sample its answer.

The invariant that makes the last part true: **nothing is recorded as delivered
until it has been.** The worker holds the current schema and node between jobs so a
document crosses the pipe once rather than once per sample, and the parent's record
of what it holds is updated only after a send that succeeded. A send that failed and
was remembered anyway would have the worker grade the next sample against a node it
no longer holds, and answer cleanly — a pass for a sample nothing graded.

Samples cross the pipe as JSON. That is what a recorded sample is: `examples`
entries are read out of a JSON document, so re-encoding one is lossless. A value
that cannot be encoded reached this check from a caller that built the document in
Python rather than parsing it, and every sample under it is reported ungraded
rather than guessed at — a sample sits inside the schema that records it, so a
value neither can be encoded is one defect, not two.

What comes back carries only what a finding renders: an unresolved reference's
`ref` is sent already `repr`'d and never the resource it was searched for in, so a
message stays a finding rather than a copy of the document that produced it.
"""
from __future__ import annotations

import contextlib
import json
import os
import queue
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
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

#: How long a worker gets to start and answer a handshake. Not evaluation time and
#: so not charged to the document budget for the first start: it covers an
#: interpreter start on a cold or contended machine, which is unrelated to what any
#: sample costs. A restart IS charged — it is a cost the samples caused.
_START_DEADLINE_SECONDS = 30.0

#: The worker is this file, run directly rather than as `-m analitiq.validator…`:
#: importing the package would pull the contract models and pydantic into a child
#: that needs neither, and `runpy` warns about the double import on every start —
#: into the same stderr a failure reason is read from.
_WORKER_SCRIPT = str(Path(__file__).resolve())

#: How much of a stray line or a diagnostic reaches a finding. Long enough to
#: identify what happened, short enough that a finding stays a finding.
_DETAIL_LIMIT = 200


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


def _clipped(text: str) -> str:
    return text if len(text) <= _DETAIL_LIMIT else f"{text[:_DETAIL_LIMIT]}…"


# ---------------------------------------------------------------------------
# The worker
# ---------------------------------------------------------------------------

#: The kinds a worker may answer with. The parent refuses anything else rather
#: than reading it as a verdict.
_WORKER_KINDS = frozenset({"pong", "graded", "unresolvable", "recursion", "crash"})


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
        verdict = {"v": "pong"} if op == "ping" else _grade(document, node, job["value"])
        channel.write(json.dumps(verdict) + "\n")
        channel.flush()
    return 0


# ---------------------------------------------------------------------------
# The parent
# ---------------------------------------------------------------------------

class _Unsent:
    """Distinguishes "never sent" from a node or schema that is legitimately
    `None`, which a comparison against `None` could not."""

    def __repr__(self) -> str:  # pragma: no cover - diagnostics only
        return "_UNSENT"


_UNSENT = _Unsent()


class _Reply:
    """What came back, or why nothing did."""

    def __init__(self, verdict: dict | None = None, timed_out: bool = False,
                 gone: str | None = None) -> None:
        self.verdict = verdict
        self.timed_out = timed_out
        self.gone = gone


def _encoded(message: dict) -> str | None:
    """The message as a wire line, or `None` when its value is not JSON."""
    try:
        return json.dumps(message) + "\n"
    except (TypeError, ValueError):
        return None


def _drain(stream, replies: queue.Queue) -> None:
    """Move the worker's lines onto a queue the parent can wait on with a
    deadline. Reading a pipe is the one wait Python can time out portably, and
    only from a thread that is not the one holding the deadline."""
    try:
        for line in stream:
            replies.put(line)
    finally:
        replies.put(None)


class BudgetedGrader:
    """Grades samples in a worker process that a breached budget can kill.

    Started lazily, so a document with nothing to grade starts nothing. Killed and
    replaced when a sample outlasts its budget or kills the worker, because in
    both cases there is no other way to get a usable worker back.

    An environment that cannot host a worker is a different thing from a sample
    that cannot be graded, and only the first is permanent: the handshake is what
    tells them apart, so a failed START gives up for the document while a worker
    lost mid-grade is simply replaced. Latching on the second would report, for
    every sample after the one that killed the worker, a failure they never had.

    Not reusable across documents: the document budget is spent, not reset.
    """

    def __init__(self, sample_budget: float | None = None,
                 document_budget: float | None = None) -> None:
        # Read at call time rather than bound as defaults at import, so the
        # budgets are reachable by the tests that grade what happens once they
        # run out.
        self._sample_budget = SAMPLE_BUDGET_SECONDS if sample_budget is None else sample_budget
        self._remaining = DOCUMENT_BUDGET_SECONDS if document_budget is None else document_budget
        self._proc: subprocess.Popen | None = None
        self._replies: queue.Queue | None = None
        self._stderr = None
        self._started_once = False
        self._unhostable: str | None = None
        # Identity, not equality: the caller hands the same objects down its own
        # walk, and comparing whole documents per sample would cost more than
        # resending them.
        self._schema: Any = _UNSENT
        self._node: Any = _UNSENT

    def __enter__(self) -> "BudgetedGrader":
        return self

    def __exit__(self, *_exc) -> None:
        self.close()

    def grade(self, schema: Any, node: dict, sample: Any) -> dict:
        """This sample's verdict — one of the worker's, or the budget's own.

        Every kind other than `graded` says nothing was decided about the sample.
        None of them is a pass, and the caller renders each as a finding naming it.
        """
        if self._remaining < self._sample_budget:
            # What is left is not a per-sample budget. Spending it would report a
            # breach the sample did not commit, under a number naming no rule.
            return {"v": "exhausted"}
        begin = time.monotonic()
        uncharged = 0.0
        try:
            if self._proc is None:
                started = time.monotonic()
                usable = self._start()
                if not self._started_once:
                    self._started_once = True
                    uncharged = time.monotonic() - started
                if not usable:
                    return {"v": "unavailable", "reason": self._unhostable}
            return self._graded(schema, node, sample)
        finally:
            self._remaining -= time.monotonic() - begin - uncharged

    def close(self) -> None:
        self._kill()
        if self._stderr is not None:
            self._stderr.close()
            self._stderr = None

    # -- one sample ---------------------------------------------------------

    def _graded(self, schema: Any, node: dict, sample: Any) -> dict:
        """Bring the worker up to date, then ask it. A worker that could not be
        brought up to date is never asked, because its answer would be about a
        node it does not hold."""
        for op, value, held in (("schema", schema, self._schema),
                                ("node", node, self._node)):
            if value is held:
                continue
            payload = _encoded({"op": op, "value": value})
            if payload is None:
                return {"v": "unserializable"}
            if not self._write(payload):
                return self._lost("it stopped accepting work")
            # Only now. A send that failed and was remembered anyway would have
            # the worker grade this sample against a node it does not hold.
            if op == "schema":
                self._schema, self._node = schema, _UNSENT
            else:
                self._node = node

        payload = _encoded({"op": "grade", "value": sample})
        if payload is None:
            return {"v": "unserializable"}
        reply = self._exchange(payload, self._sample_budget)
        if reply.timed_out:
            # The worker is inside the evaluation and will not come back to be
            # asked anything else. Killing it IS the interrupt.
            self._kill()
            return {"v": "budget", "seconds": self._sample_budget}
        if reply.gone is not None:
            return self._lost(reply.gone)
        return reply.verdict

    def _lost(self, what_happened: str) -> dict:
        """The worker is unusable. Report it against THIS sample and replace it:
        a worker one sample killed says nothing about the samples after it."""
        diagnostic = self._worker_stderr()
        self._kill()
        detail = f"{what_happened}; {diagnostic}" if diagnostic else what_happened
        return {"v": "unavailable", "reason": f"the grading worker was lost — {detail}"}

    # -- worker lifecycle ---------------------------------------------------

    def _start(self) -> bool:
        """Start a worker and prove it answers. False leaves a stated reason.

        The handshake is what separates an environment that cannot host a worker
        from a sample that cannot be graded, so only a failure HERE is permanent.
        """
        if self._unhostable is not None:
            return False
        if not sys.executable:
            self._unhostable = "this interpreter does not report its own path"
            return False
        if not os.path.isfile(_WORKER_SCRIPT):
            self._unhostable = f"the grading worker {_WORKER_SCRIPT!r} is not on disk"
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
        else:
            # Emptied per worker, so a reason quotes the worker it is about.
            self._stderr.seek(0)
            self._stderr.truncate()
        try:
            self._proc = subprocess.Popen(  # noqa: S603 - argv is this interpreter and this file
                [sys.executable, _WORKER_SCRIPT],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=self._stderr, text=True, env=env)
        except OSError as exc:
            self._proc = None
            self._unhostable = f"the grading worker could not be started ({exc})"
            return False

        self._replies = queue.Queue()
        # Daemon: if the worker outlives an abandoned parent, this thread must
        # not keep the interpreter alive waiting on its pipe.
        threading.Thread(target=_drain, args=(self._proc.stdout, self._replies),
                         daemon=True).start()
        handshake = self._exchange(_encoded({"op": "ping"}), _START_DEADLINE_SECONDS)
        if handshake.verdict != {"v": "pong"}:
            why = handshake.gone or "it did not answer"
            diagnostic = self._worker_stderr()
            self._kill()
            self._unhostable = (f"the grading worker did not start — {why}"
                                + (f"; {diagnostic}" if diagnostic else ""))
            return False
        return True

    def _kill(self) -> None:
        if self._proc is not None:
            self._proc.kill()
            self._proc.wait()
            # Closing is not tidiness. A buffered pipe left to finalization
            # raises BrokenPipeError, and the interpreter prints that traceback
            # on the validator's own stderr — which is where a caller looks when
            # the JSON report is missing.
            for pipe in (self._proc.stdin, self._proc.stdout):
                if pipe is not None:
                    with contextlib.suppress(OSError):
                        pipe.close()
        self._proc = None
        self._replies = None
        # A replacement worker holds nothing, so neither may the record of what
        # it was sent.
        self._schema = _UNSENT
        self._node = _UNSENT

    def _worker_stderr(self) -> str:
        """The last line the worker wrote, or nothing. It is what says WHY."""
        if self._stderr is None:
            return ""
        with contextlib.suppress(OSError, ValueError):
            self._stderr.seek(0)
            text = self._stderr.read().strip()
            if text:
                return _clipped(text.splitlines()[-1])
        return ""

    # -- the pipe -----------------------------------------------------------

    def _write(self, payload: str) -> bool:
        try:
            self._proc.stdin.write(payload)
            self._proc.stdin.flush()
        except (OSError, ValueError):
            return False
        return True

    def _exchange(self, payload: str, budget: float) -> _Reply:
        if not self._write(payload):
            return _Reply(gone="it stopped accepting work")
        try:
            line = self._replies.get(timeout=budget)
        except queue.Empty:
            return _Reply(timed_out=True)
        if line is None:
            return _Reply(gone="it exited without answering")
        try:
            verdict = json.loads(line)
        except json.JSONDecodeError:
            return _Reply(gone=f"it wrote {_clipped(line.strip())!r} where a verdict belonged")
        if not isinstance(verdict, dict) or verdict.get("v") not in _WORKER_KINDS:
            # Refused rather than passed on. An unrecognised shape reaching the
            # caller's dispatch would take the whole document's findings with it.
            return _Reply(gone=f"it answered {_clipped(str(verdict))!r}, which is not a verdict")
        return _Reply(verdict=verdict)


if __name__ == "__main__":
    # Run by path, so Python prepended this file's own directory to `sys.path`,
    # where a sibling module could shadow a name the grader's own imports need.
    # The worker takes nothing from that directory.
    if sys.path and os.path.realpath(sys.path[0]) == os.path.dirname(_WORKER_SCRIPT):
        del sys.path[0]
    sys.exit(_serve())
