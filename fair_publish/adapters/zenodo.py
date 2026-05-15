"""
Zenodo deployment adapter.

Iterates over every dataset in a validated maDMP and either creates a
new Zenodo record or publishes a new version of an existing one, using
the state file (zenodo_state.json) to decide which path to take.

Field mapping (RDA maDMP → Zenodo metadata):
  dmp.contact / dmp.contributor       → metadata.creators
  dataset.title                       → metadata.title
  dataset.description                 → metadata.description
  dataset.keyword                     → metadata.keywords
  dataset.distribution[0].data_access → metadata.access_right
  dataset.distribution[0].license[0]  → metadata.license (SPDX id)
  dmp_id.identifier                   → metadata.related_identifiers
  release_tag (passed explicitly)     → metadata.version
"""

from __future__ import annotations
import os
import re
import sys
from typing import Any

import requests

ZENODO_API = "https://zenodo.org/api"
ZENODO_SANDBOX_API = "https://sandbox.zenodo.org/api"

# Zenodo expects SPDX IDs; CC URLs need remapping
_CC_URL_TO_SPDX = {
    "by/4.0": "CC-BY-4.0",
    "by-sa/4.0": "CC-BY-SA-4.0",
    "by-nc/4.0": "CC-BY-NC-4.0",
    "publicdomain/zero/1.0": "CC0-1.0",
    "zero/1.0": "CC0-1.0",
}
_ACCESS_MAP = {"open": "open", "shared": "restricted", "closed": "closed"}


# ---------------------------------------------------------------------------
# Metadata mapping
# ---------------------------------------------------------------------------

def _enum_tail(value: Any) -> str:
    """Normalize madmpy enum strings like 'dataaccess.open' → 'open'."""
    s = str(value or "").lower().strip()
    return s.split(".")[-1] if "." in s else s


def _license_id(license_ref: str) -> str:
    """Map a license_ref (URL or SPDX id) to a Zenodo-recognized SPDX id."""
    ref = license_ref.lower()
    for fragment, spdx in _CC_URL_TO_SPDX.items():
        if fragment in ref:
            return spdx
    # Try extracting the SPDX id from a spdx.org URL or plain string
    m = re.search(r"spdx\.org/licenses/([^/\s]+)", ref, re.I)
    if m:
        return m.group(1).rstrip("/")
    # Last segment of a URL as fallback
    m2 = re.search(r"/([^/\s]+?)/?$", ref)
    return (m2.group(1) if m2 else license_ref).lower()


def _build_creator(person: Any) -> dict:
    entry: dict = {"name": getattr(person, "name", "Unknown")}
    cid = getattr(person, "contact_id", None) or getattr(person, "contributor_id", None)
    if cid and "orcid" in _enum_tail(getattr(cid, "type", "")):
        entry["orcid"] = str(getattr(cid, "identifier", ""))
    affiliation = str(getattr(person, "affiliation", "") or "")
    if affiliation:
        entry["affiliation"] = affiliation
    return entry


def _dataset_to_zenodo_metadata(dmp: Any, dataset: Any, version_tag: str) -> dict:
    creators = []
    contact = getattr(dmp, "contact", None)
    if contact:
        creators.append(_build_creator(contact))
    for contrib in getattr(dmp, "contributor", None) or []:
        creators.append(_build_creator(contrib))
    if not creators:
        creators = [{"name": "Unknown"}]

    distributions = getattr(dataset, "distribution", None) or [None]
    dist = distributions[0]

    licenses = getattr(dist, "license", None) or [] if dist else []
    license_id = (_license_id(str(getattr(licenses[0], "license_ref", "") or ""))
                  if licenses else "")
    data_access = _enum_tail(getattr(dist, "data_access", "open") or "open") if dist else "open"

    meta: dict = {
        "upload_type": "dataset",
        "title": str(getattr(dataset, "title", "Untitled")),
        "description": str(getattr(dataset, "description", "") or ""),
        "creators": creators,
        "keywords": [str(k) for k in (getattr(dataset, "keyword", None) or [])],
        "access_right": _ACCESS_MAP.get(data_access, "open"),
        "version": version_tag,
    }
    if license_id:
        meta["license"] = license_id

    dmp_id = getattr(dmp, "dmp_id", None)
    dmp_identifier = str(getattr(dmp_id, "identifier", "") or "")
    if dmp_identifier:
        meta["related_identifiers"] = [{
            "identifier": dmp_identifier,
            "relation": "isDocumentedBy",
            "scheme": str(getattr(dmp_id, "type", "url")),
        }]

    return meta


# ---------------------------------------------------------------------------
# Adapter
# ---------------------------------------------------------------------------

class ZenodoAdapter:
    def __init__(self, token: str | None = None, sandbox: bool = False):
        self.token = token or os.environ.get("ZENODO_TOKEN", "")
        self.base = ZENODO_SANDBOX_API if sandbox else ZENODO_API
        if not self.token:
            raise ValueError("ZENODO_TOKEN environment variable is not set.")

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json"}

    def _upload_placeholder(self, dep_id: int) -> None:
        """Upload a minimal placeholder file so Zenodo can publish."""
        content = (
            "This record was published via the fair-publish pipeline.\n"
            "It contains metadata only — actual data files are managed separately.\n"
        ).encode()
        headers = {"Authorization": f"Bearer {self.token}"}
        resp = requests.post(
            f"{self.base}/deposit/depositions/{dep_id}/files",
            headers=headers,
            data={"name": "README.txt"},
            files={"file": ("README.txt", content, "text/plain")},
            timeout=30,
        )
        resp.raise_for_status()

    def _create(self, metadata: dict) -> dict:
        resp = requests.post(f"{self.base}/deposit/depositions",
                             json={}, headers=self._headers(), timeout=30)
        resp.raise_for_status()
        dep_id = resp.json()["id"]

        resp = requests.put(f"{self.base}/deposit/depositions/{dep_id}",
                            json={"metadata": metadata},
                            headers=self._headers(), timeout=30)
        resp.raise_for_status()

        self._upload_placeholder(dep_id)

        resp = requests.post(
            f"{self.base}/deposit/depositions/{dep_id}/actions/publish",
            headers=self._headers(), timeout=30)
        resp.raise_for_status()
        return resp.json()

    def _new_version(self, concept_doi: str, metadata: dict) -> dict:
        resp = requests.get(f"{self.base}/records",
                            params={"q": f"conceptdoi:\"{concept_doi}\"", "size": 1},
                            headers=self._headers(), timeout=30)
        resp.raise_for_status()
        hits = resp.json().get("hits", {}).get("hits", [])
        if not hits:
            raise ValueError(f"No record found for concept DOI {concept_doi}")
        record_id = hits[0]["id"]

        resp = requests.post(
            f"{self.base}/deposit/depositions/{record_id}/actions/newversion",
            headers=self._headers(), timeout=30)
        resp.raise_for_status()
        new_id = resp.json()["links"]["latest_draft"].rstrip("/").split("/")[-1]

        resp = requests.put(f"{self.base}/deposit/depositions/{new_id}",
                            json={"metadata": metadata},
                            headers=self._headers(), timeout=30)
        resp.raise_for_status()

        resp = requests.post(
            f"{self.base}/deposit/depositions/{new_id}/actions/publish",
            headers=self._headers(), timeout=30)
        resp.raise_for_status()
        return resp.json()

    def publish_dmp(self, dmp: Any, state: dict[str, str],
                    version_tag: str) -> dict[str, str]:
        """
        Publish every dataset in the DMP.

        For each dataset:
          - If its dataset_id is in `state`, create a new Zenodo version.
          - Otherwise, create a new Zenodo record.

        Returns an updated state dict (dataset_id → concept_doi).
        """
        from ..state import dataset_key

        updated_state = dict(state)

        for dataset in getattr(dmp, "dataset", None) or []:
            key = dataset_key(dataset)
            metadata = _dataset_to_zenodo_metadata(dmp, dataset, version_tag)

            if key in state:
                result = self._new_version(state[key], metadata)
                action = "updated"
            else:
                result = self._create(metadata)
                action = "created"

            concept_doi = result.get("conceptdoi") or result.get("doi", "")
            updated_state[key] = concept_doi
            print(f"  [{action}] {metadata['title']} → {concept_doi}", file=sys.stderr)

        return updated_state
