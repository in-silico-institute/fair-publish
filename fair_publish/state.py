"""
Zenodo state file management.

zenodo_state.json is committed in each project repository alongside the DMP.
It maps dataset_id.identifier → structured entry recording the Zenodo
deposition record ID and the policy version under which the record was
last published.

Example zenodo_state.json:
{
  "metabolic-biomarker-cohort-wave1": {
    "record_id": "12345",
    "policy_version": "1.0"
  }
}

Keys are dataset_id.identifier values from the maDMP.
record_id is the Zenodo deposition ID (integer stored as string).
policy_version is the version field from the institutional fair-policy.yml
  at the time of publication — used to identify records that may be
  non-compliant after a policy update.

Old state files with string values are migrated automatically on load.
"""

from __future__ import annotations
import json
from pathlib import Path

STATE_FILENAME = "zenodo_state.json"


def load(repo_root: Path) -> dict[str, dict]:
    """Return mapping of dataset_id → state entry, or empty dict.

    Migrates legacy string-valued entries (record_id only) to the
    structured format transparently.
    """
    path = repo_root / STATE_FILENAME
    if not path.exists():
        return {}
    with path.open() as f:
        raw = json.load(f)
    result = {}
    for k, v in raw.items():
        if isinstance(v, str):
            result[k] = {"record_id": v, "policy_version": "unknown"}
        else:
            result[k] = v
    return result


def save(repo_root: Path, state: dict[str, dict]) -> None:
    """Write the updated state back to zenodo_state.json."""
    path = repo_root / STATE_FILENAME
    with path.open("w") as f:
        json.dump(state, f, indent=2)
        f.write("\n")


def record_id(entry: dict) -> str:
    """Extract the Zenodo deposition record ID from a state entry."""
    return entry["record_id"]


def dataset_key(dataset: object) -> str:
    """Return the stable key for a dataset (its dataset_id.identifier)."""
    did = getattr(dataset, "dataset_id", None)
    return str(getattr(did, "identifier", "") or "").strip()
