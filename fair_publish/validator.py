"""
Validation engine.

Loads a maDMP via madmpy (structural validation first), then runs all
policy rules against each dataset in the DMP.

A ValidationReport groups results by dataset so the GHA step summary
and CLI output are readable even for DMPs with many datasets.
"""

from __future__ import annotations
import contextlib
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

import madmpy

from .rules import RuleResult
from .policy import PolicyConfig, load as load_policy


# ---------------------------------------------------------------------------
# Report types
# ---------------------------------------------------------------------------

@dataclass
class DatasetReport:
    dataset_title: str
    dataset_id: str
    results: list[RuleResult] = field(default_factory=list)

    @property
    def errors(self) -> list[RuleResult]:
        return [r for r in self.results if not r.passed and r.severity == "error"]

    @property
    def warnings(self) -> list[RuleResult]:
        return [r for r in self.results if not r.passed and r.severity == "warning"]

    @property
    def passed(self) -> bool:
        return len(self.errors) == 0


@dataclass
class ValidationReport:
    dmp_title: str
    datasets: list[DatasetReport] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return all(d.passed for d in self.datasets)

    @property
    def total_errors(self) -> int:
        return sum(len(d.errors) for d in self.datasets)

    @property
    def total_warnings(self) -> int:
        return sum(len(d.warnings) for d in self.datasets)

    def summary(self) -> str:
        lines = [
            f"DMP: {self.dmp_title}",
            f"Datasets: {len(self.datasets)}  "
            f"Errors: {self.total_errors}  Warnings: {self.total_warnings}",
        ]
        for dr in self.datasets:
            lines.append(f"\n  Dataset: {dr.dataset_title} [{dr.dataset_id}]")
            for r in dr.results:
                icon = "✓" if r.passed else ("✗" if r.severity == "error" else "⚠")
                lines.append(f"    {icon} [{r.fair}] {r.rule}")
                if not r.passed:
                    lines.append(f"        {r.message}")
        lines.append("")
        lines.append("PASSED" if self.passed else "FAILED — fix errors before publishing.")
        return "\n".join(lines)

    def to_dict(self) -> dict:
        return {
            "dmp_title": self.dmp_title,
            "passed": self.passed,
            "total_errors": self.total_errors,
            "total_warnings": self.total_warnings,
            "datasets": [
                {
                    "title": dr.dataset_title,
                    "dataset_id": dr.dataset_id,
                    "passed": dr.passed,
                    "results": [
                        {
                            "rule": r.rule, "passed": r.passed,
                            "severity": r.severity, "field": r.field,
                            "message": r.message, "fair": r.fair,
                        }
                        for r in dr.results
                    ],
                }
                for dr in self.datasets
            ],
        }


# ---------------------------------------------------------------------------
# Validation functions
# ---------------------------------------------------------------------------

def check_schema(dmp_path: Path) -> None:
    """Validate a maDMP against the RDA maDMP Common Standard schema.

    Raises an exception if the document is structurally invalid.
    This is intentionally separate from policy checking so it can be
    surfaced as a distinct step in CI pipelines.
    """
    with contextlib.redirect_stdout(sys.stderr):
        madmpy.validate_DMP(str(dmp_path))


def _load_dmp(dmp_path: Path, *, skip_schema: bool = False):
    """Parse a maDMP into a madmpy DMP object.

    If skip_schema is False (default) the RDA schema is validated first via
    madmpy.validate_DMP; pass skip_schema=True when the schema step has
    already been run as a separate pipeline step.
    """
    with contextlib.redirect_stdout(sys.stderr):
        if not skip_schema:
            madmpy.validate_DMP(str(dmp_path))
        dmp_module = madmpy.load()
    with dmp_path.open() as f:
        data = json.load(f)
    return dmp_module.DMP(**data["dmp"])


def validate(dmp_path: Path, policy: PolicyConfig,
             *, skip_schema: bool = False) -> ValidationReport:
    dmp = _load_dmp(dmp_path, skip_schema=skip_schema)
    report = ValidationReport(dmp_title=getattr(dmp, "title", str(dmp_path)))

    datasets = getattr(dmp, "dataset", None) or []
    for dataset in datasets:
        did = getattr(getattr(dataset, "dataset_id", None), "identifier", "") or ""
        dr = DatasetReport(
            dataset_title=getattr(dataset, "title", str(did)),
            dataset_id=str(did),
        )
        for rule in policy.rules:
            dr.results.append(rule(dmp, dataset))
        report.datasets.append(dr)

    return report


def validate_file(dmp_path: Path, policy_path: Path,
                  *, skip_schema: bool = False) -> ValidationReport:
    policy = load_policy(policy_path)
    return validate(dmp_path, policy, skip_schema=skip_schema)
