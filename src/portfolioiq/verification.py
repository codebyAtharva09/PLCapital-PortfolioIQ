"""Shared vocabulary for checks and verification, used by every calculation and answer receipt.

INDEPENDENTLY_RECONCILED means two separate calculations agree. RESULT_VALIDATED means the result passed its checks.
NOT_INDEPENDENTLY_VERIFIABLE means a calculated figure could not be rebuilt from source records.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict


class CheckStatus(StrEnum):
    PASS = "PASS"
    WARNING = "WARNING"  # documented anomaly or limitation; the result is still usable
    FAIL = "FAIL"  # the result would be wrong; it must not be presented as verified
    NOT_APPLICABLE = "NOT_APPLICABLE"


class VerificationStatus(StrEnum):
    EXECUTED = "EXECUTED"
    STRUCTURE_VALIDATED = "STRUCTURE_VALIDATED"
    RESULT_VALIDATED = "RESULT_VALIDATED"
    INDEPENDENTLY_RECONCILED = "INDEPENDENTLY_RECONCILED"
    NOT_INDEPENDENTLY_VERIFIABLE = "NOT_INDEPENDENTLY_VERIFIABLE"  # calculated figures that could not be rebuilt from source records
    VERIFICATION_FAILED = "VERIFICATION_FAILED"
    VERIFICATION_UNAVAILABLE = "VERIFICATION_UNAVAILABLE"


class Check(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    status: CheckStatus
    detail: str
    rule: str | None = None
