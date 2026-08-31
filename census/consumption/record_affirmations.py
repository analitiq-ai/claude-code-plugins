"""The record affirmations — one per rule record governing an unread field.

Each entry is a reader's judgment that the record's ``rationale`` is honest
about fields the pinned consumption manifest claims no read of, pinned to
the refs judged and the wording judged. What the judgment holds the
rationale to is the record-affirmation section of
``.claude/rules/reachability-dispositions.md``; the guard that summons it is
``census.consumption.records.record_report``. Re-affirming means re-reading
the rationale against the current unread set, then re-computing the entry —
never re-computing alone.
"""
from __future__ import annotations

from census.consumption.records import RecordAffirmation

AFFIRMATIONS: tuple[RecordAffirmation, ...] = (
    RecordAffirmation(
        "RULE-DBEP-013",
        refs=(
            "endpoints.DatabaseObject.object_type",
        ),
        rationale_sha256="d0b1bea51d12dd2bb4d4226920d1a3acf2d41f42d986adb527bc0a3ae58e466f",
    ),
    RecordAffirmation(
        "RULE-ENDP-038",
        refs=(
            "endpoints.Replication.supported_methods",
        ),
        rationale_sha256="354e92d5820ab78ceb7580c33599028ee0d10c17f03d6b71ccaf7185578d7d67",
    ),
    RecordAffirmation(
        "RULE-ENDP-050",
        refs=(
            "endpoints.Param.location",
        ),
        rationale_sha256="576e9b9677e1929063ea500b65d4fb1ec9e55bef4bbac020b1be348fd4a7125d",
    ),
    RecordAffirmation(
        "RULE-ENDP-055",
        refs=(
            "endpoints.Param.operators",
        ),
        rationale_sha256="03979511bfaaa39602c0c2db7cac2a54ee19cdae414425c2f68ff19db9d022a2",
    ),
    RecordAffirmation(
        "RULE-ENDP-056",
        refs=(
            "endpoints.SingleCursorMapping.format",
            "endpoints.SingleCursorMapping.operator",
        ),
        rationale_sha256="e13fc43a9a4338cad23b6d451b5d901ba3f9603631963da0236d29cd7c85cc00",
    ),
    RecordAffirmation(
        "RULE-ENDP-057",
        refs=(
            "endpoints.WindowCursorMapping.end_operator",
            "endpoints.WindowCursorMapping.format",
            "endpoints.WindowCursorMapping.start_operator",
        ),
        rationale_sha256="c1729f75bb64ad71d49f5765a18d8c5d5d987828aface7be32d8f624b2778976",
    ),
    RecordAffirmation(
        "RULE-PIPE-017",
        refs=(
            "pipelines.config.Logging.log_level",
        ),
        rationale_sha256="9a15344f2685796ed5f07952b0efe89dcad06a2074e6b5b06f5c99569d2d57ef",
    ),
    RecordAffirmation(
        "RULE-STRM-040",
        refs=(
            "stream.Validation.error_handling",
        ),
        rationale_sha256="097d0229508016640e7d3c68142510cd17600d6be1d66eb60cf7fe1ea79e0a3a",
    ),
)
