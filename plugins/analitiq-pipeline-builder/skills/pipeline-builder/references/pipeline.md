# Orchestration policies that hold across every phase

`SKILL.md` §Pipeline is where the phases live — what each one does, what it
halts on, and what it writes. This file carries only what is true of *all* of
them, so a policy is stated once instead of at every phase that obeys it.

## Halting

A phase that halts stops the build. Halting means: do not write partial files,
do not advance to a later phase, and do not auto-retry without user input.

The orchestrator surfaces what it halted on and waits. Where the halt came from
a validator or a sub-agent, the finding travels verbatim, exactly as it was
emitted (`skills/pipeline-builder/references/io-contracts.md`'s `Diagnostics`
section owns the shape) — because the user is the one who has to fix the file,
and a paraphrased diagnostic sends them to the wrong line.

## Parallel dispatch

A phase that dispatches more than one agent issues every call in a single
message, as multiple tool invocations in one turn, so they run concurrently.
Do not sequence them artificially.

## Fix-and-revalidate loop (phase 9)

For each artifact:

1. Run the validator.
2. If `passed: true`, accept and move on.
3. If any finding's `validator` is `adapter-crash`, halt immediately and
   surface it instead of entering the fix loop below: it names a validator or
   runtime failure, not an authored-document defect, so the creator has no
   correction to make — feeding it in would re-invoke the creator against an
   artifact that may already be valid.
4. Otherwise, collect the findings and re-invoke the matching creator with the
   findings attached, asking it to fix exactly the reported errors — and only
   those, no opportunistic edits.
5. Re-validate. Increment the pass counter.
6. Stop after **5 passes** regardless of state. If still failing, halt and
   surface the diagnostics.

The validator is stateless — the pass cap and the discipline live here, not in
`scripts/validate.py`, and every artifact the orchestrator writes goes through
this same loop.
