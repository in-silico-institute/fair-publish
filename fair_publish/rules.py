"""
Rule protocol and built-in FAIR rules for maDMP validation.

Rules operate on a (DMP, Dataset) pair loaded via madmpy.  This keeps
rule logic typed and free of raw-dict access.

Institutions add domain-specific rules by implementing the Rule protocol
and registering them in policy.yml under extra_rules.
"""

from __future__ import annotations
from dataclasses import dataclass
from typing import Protocol, Any, runtime_checkable


# ---------------------------------------------------------------------------
# Core types
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class RuleResult:
    passed: bool
    rule: str
    severity: str       # "error" | "warning"
    field: str          # maDMP field path checked
    message: str        # explanation when failed
    fair: str           # FAIR sub-principles addressed


@runtime_checkable
class Rule(Protocol):
    name: str
    severity: str       # "error" blocks publish; "warning" only reports
    fair: str

    def __call__(self, dmp: Any, dataset: Any) -> RuleResult: ...


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _result(rule_name: str, severity: str, fair: str,
            field: str, passed: bool, message: str = "") -> RuleResult:
    return RuleResult(
        passed=passed, rule=rule_name, severity=severity, fair=fair,
        field=field, message=message if not passed else "OK",
    )


# ---------------------------------------------------------------------------
# Built-in FAIR rules
# ---------------------------------------------------------------------------

class OrcidPresent:
    """At least the DMP contact must have an ORCID identifier."""
    name = "Creator ORCID present"
    severity = "warning"
    fair = "F1, A1"

    def __call__(self, dmp: Any, dataset: Any) -> RuleResult:
        contact = getattr(dmp, "contact", None)
        cid = getattr(contact, "contact_id", None)
        has_orcid = (
            cid is not None
            and str(getattr(cid, "type", "")).lower() == "orcid"
            and bool(getattr(cid, "identifier", ""))
        )
        # Also accept any contributor with ORCID
        if not has_orcid:
            for contrib in getattr(dmp, "contributor", []) or []:
                cid2 = getattr(contrib, "contributor_id", None)
                if cid2 and str(getattr(cid2, "type", "")).lower() == "orcid":
                    has_orcid = True
                    break
        return _result(self.name, self.severity, self.fair,
                       "dmp.contact.contact_id",
                       has_orcid,
                       "No ORCID found for DMP contact or any contributor.")


class LicenseRecognized:
    """Each dataset distribution must carry a recognized SPDX license."""
    name = "License present and recognized"
    severity = "error"
    fair = "R1.1"

    _KNOWN = {
        "cc-by-4.0", "cc-by-sa-4.0", "cc-by-nc-4.0", "cc0-1.0",
        "mit", "apache-2.0", "gpl-2.0", "gpl-3.0", "lgpl-2.1",
        "bsd-2-clause", "bsd-3-clause", "eupl-1.2",
    }

    def __call__(self, dmp: Any, dataset: Any) -> RuleResult:
        distributions = getattr(dataset, "distribution", None) or []
        refs = []
        for dist in distributions:
            for lic in getattr(dist, "license", None) or []:
                ref = str(getattr(lic, "license_ref", "") or "").lower()
                if ref:
                    refs.append(ref)
        recognized = any(
            any(k in ref for k in self._KNOWN) for ref in refs
        )
        passed = bool(refs) and recognized
        msg = ("No license_ref found in any distribution."
               if not refs else
               f"License '{refs[0]}' not in recognized SPDX list.")
        return _result(self.name, self.severity, self.fair,
                       "dataset.distribution[].license[].license_ref",
                       passed, msg if not passed else "")


class KeywordsPresent:
    """Dataset must carry at least one keyword."""
    name = "At least one keyword"
    severity = "warning"
    fair = "F2"

    def __call__(self, dmp: Any, dataset: Any) -> RuleResult:
        keywords = getattr(dataset, "keyword", None) or []
        passed = len(keywords) >= 1
        return _result(self.name, self.severity, self.fair,
                       "dataset.keyword", passed,
                       "dataset.keyword is absent or empty.")


class DescriptionLength:
    """Dataset description must meet a minimum length."""
    name = "Description length"
    severity = "warning"
    fair = "F2, R1"
    min_chars: int = 100

    def __call__(self, dmp: Any, dataset: Any) -> RuleResult:
        desc = str(getattr(dataset, "description", "") or "").strip()
        passed = len(desc) >= self.min_chars
        return _result(self.name, self.severity, self.fair,
                       "dataset.description", passed,
                       f"Description is {len(desc)} chars; minimum is {self.min_chars}.")


class AccessRightsStated:
    """Each distribution must state data_access explicitly."""
    name = "Access rights stated"
    severity = "error"
    fair = "A1"
    _VALID = {"open", "shared", "closed"}

    def __call__(self, dmp: Any, dataset: Any) -> RuleResult:
        distributions = getattr(dataset, "distribution", None) or []
        if not distributions:
            return _result(self.name, self.severity, self.fair,
                           "dataset.distribution", False,
                           "No distribution found; cannot check access rights.")
        for dist in distributions:
            ar = str(getattr(dist, "data_access", "") or "").strip().lower()
            if ar not in self._VALID:
                return _result(self.name, self.severity, self.fair,
                               "dataset.distribution[].data_access", False,
                               f"data_access '{ar}' must be one of {self._VALID}.")
        return _result(self.name, self.severity, self.fair,
                       "dataset.distribution[].data_access", True)


class DatasetIdPresent:
    """Each dataset must have a persistent identifier (DOI, URL, …)."""
    name = "Dataset identifier present"
    severity = "error"
    fair = "F1"

    def __call__(self, dmp: Any, dataset: Any) -> RuleResult:
        did = getattr(dataset, "dataset_id", None)
        identifier = str(getattr(did, "identifier", "") or "").strip()
        passed = bool(identifier)
        return _result(self.name, self.severity, self.fair,
                       "dataset.dataset_id.identifier", passed,
                       "dataset.dataset_id.identifier is absent or empty.")


# ---------------------------------------------------------------------------
# Default rule set
# ---------------------------------------------------------------------------

DEFAULT_RULES: list[Rule] = [
    OrcidPresent(),
    LicenseRecognized(),
    KeywordsPresent(),
    DescriptionLength(),
    AccessRightsStated(),
    DatasetIdPresent(),
]
