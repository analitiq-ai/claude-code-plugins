"""The record affirmations — one per rule record governing an unread field.

``census.consumption.records`` is the guard that consumes this registry and
says what an entry is; what a reader holds an affirmed rationale to, and
what re-affirming means, is the record-affirmation section of
``.claude/rules/reachability-dispositions.md``.
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
        rationale_sha256="7cb72bd0b143e50770dafa63a6f59de4ca18121f45ba7e8840647a57678cf2b5",
    ),
    RecordAffirmation(
        "RULE-ENDP-050",
        refs=(
            "endpoints.Param.location",
        ),
        rationale_sha256="165be1e23af73d5130007e72dd80466e4336a42dee14f0064a33c18b6921c2c9",
    ),
    RecordAffirmation(
        "RULE-ENDP-055",
        refs=(
            "endpoints.Param.operators",
        ),
        rationale_sha256="9f43b803651c39df93e9e4b673b9c06dc3a33f860d382a7b2f7102f2c4dc375f",
    ),
    RecordAffirmation(
        "RULE-ENDP-056",
        refs=(
            "endpoints.SingleCursorMapping.format",
            "endpoints.SingleCursorMapping.operator",
        ),
        rationale_sha256="3478354df318863cb51b6b7799ab58770bffd35d038880884eb43a961e04a7f9",
    ),
    RecordAffirmation(
        "RULE-ENDP-057",
        refs=(
            "endpoints.WindowCursorMapping.end_operator",
            "endpoints.WindowCursorMapping.format",
            "endpoints.WindowCursorMapping.start_operator",
        ),
        rationale_sha256="87b0a8102c46f31ac91c81235a1fa9a2c1d0b764034143e700631c45cb6b1c58",
    ),
    RecordAffirmation(
        "RULE-PIPE-017",
        refs=(
            "pipelines.config.Logging.log_level",
        ),
        rationale_sha256="820e6d3722e5404221e33aefb9ba51899789e22101ed5686f2a606d09fa52e67",
    ),
    RecordAffirmation(
        "RULE-STRM-040",
        refs=(
            "stream.Validation.error_handling",
        ),
        rationale_sha256="097d0229508016640e7d3c68142510cd17600d6be1d66eb60cf7fe1ea79e0a3a",
    ),
)
