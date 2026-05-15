"""
Zenodo state file management.

zenodo_state.json is committed in each project repository alongside the DMP.
It maps dataset_id.identifier → concept_doi, allowing the pipeline to
create a new Zenodo version when a dataset already has a published record,
or create a new record for datasets that appear for the first time.

Example zenodo_state.json:
{
  "https://doi.org/10.5281/zenodo.11111": "10.5281/zenodo.11111",
  "https://github.com/myorg/project/dataset-B": "10.5281/zenodo.22222"
}

Keys are dataset_id.identifier values from the maDMP.
Values are Zenodo concept DOIs (stable across versions).

Datasets removed from the DMP are simply absent from the next publish run;
their Zenodo records remain accessible but receive no further updates.
"""

from __future__ import annotations
import json
from pathlib import Path

STATE_FILENAME = "zenodo_state.json"


def load(repo_root: Path) -> dict[str, str]:
    """Return mapping of dataset_id → concept_doi, or empty dict."""
    path = repo_root / STATE_FILENAME
    if not path.exists():
        return {}
    with path.open() as f:
        return json.load(f)


def save(repo_root: Path, state: dict[str, str]) -> None:
    """Write the updated state back to zenodo_state.json."""
    path = repo_root / STATE_FILENAME
    with path.open("w") as f:
        json.dump(state, f, indent=2)
        f.write("\n")


def dataset_key(dataset: object) -> str:
    """Return the stable key for a dataset (its dataset_id.identifier)."""
    did = getattr(dataset, "dataset_id", None)
    return str(getattr(did, "identifier", "") or "").strip()
