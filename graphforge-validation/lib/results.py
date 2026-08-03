"""Shared result vocabulary every validation script reports in — one
consistent shape `generate_report.py` and `run_validation.py`'s scoring
can consume regardless of which validation produced it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class Verdict(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    MISSING = "MISSING"
    UNEXPECTED = "UNEXPECTED"
    SKIP = "SKIP"


@dataclass
class CheckResult:
    """One atomic assertion — e.g. "customer-service-python node_count",
    or "CALLS_SERVICE order-service-python -> payment-service-java"."""

    name: str
    verdict: Verdict
    detail: str = ""
    expected: object = None
    actual: object = None

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "verdict": self.verdict.value,
            "detail": self.detail,
            "expected": self.expected,
            "actual": self.actual,
        }


@dataclass
class ValidationSection:
    """One of the ten numbered validations from the RFC — a named bucket
    of `CheckResult`s with a derived score."""

    validation_id: int
    title: str
    checks: list[CheckResult] = field(default_factory=list)
    skipped_reason: str | None = None

    def add(self, check: CheckResult) -> None:
        self.checks.append(check)

    @property
    def counts(self) -> dict[str, int]:
        counts = {v.value: 0 for v in Verdict}
        for check in self.checks:
            counts[check.verdict.value] += 1
        return counts

    @property
    def total(self) -> int:
        return len(self.checks)

    @property
    def pass_rate(self) -> float:
        if not self.checks:
            return 1.0 if self.skipped_reason is None else 0.0
        return sum(1 for c in self.checks if c.verdict == Verdict.PASS) / len(self.checks)

    @property
    def overall(self) -> str:
        if self.skipped_reason is not None:
            return "SKIPPED"
        return "PASS" if self.pass_rate == 1.0 else "FAIL"

    def to_dict(self) -> dict:
        return {
            "validation_id": self.validation_id,
            "title": self.title,
            "overall": self.overall,
            "pass_rate": self.pass_rate,
            "total": self.total,
            "counts": self.counts,
            "skipped_reason": self.skipped_reason,
            "checks": [c.to_dict() for c in self.checks],
        }
