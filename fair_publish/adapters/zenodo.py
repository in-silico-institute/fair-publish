"""
Zenodo deployment adapter.

Iterates over every dataset in a validated maDMP and either creates a
new Zenodo draft or adds a new draft version of an existing record,
using the state file (zenodo_state.json) to decide which path to take.

Records are left as DRAFTS — a human curator must visit Zenodo and
click Publish to make them publicly accessible and register the DOI.
The prereserved DOI is available immediately in the draft and is stored
in the state file so it can be referenced before publication.

State file schema: dataset_id.identifier → Zenodo deposition record ID
  (integer stored as string, e.g. "12345")

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

    # Zenodo only accepts specific schemes for related identifiers; "other" is not valid.
    # Auto-detect URL/DOI from the identifier itself if the maDMP type is generic.
    _ZENODO_SCHEMES = {"ark", "arxiv", "doi", "handle", "isbn", "issn",
                       "pmid", "purl", "url", "urn", "w3id"}
    dmp_id = getattr(dmp, "dmp_id", None)
    dmp_identifier = str(getattr(dmp_id, "identifier", "") or "")
    scheme = str(getattr(dmp_id, "type", "") or "").lower().split(".")[-1]
    if scheme not in _ZENODO_SCHEMES:
        if dmp_identifier.startswith("http"):
            scheme = "url"
        elif dmp_identifier.startswith("10."):
            scheme = "doi"
        else:
            scheme = ""
    if dmp_identifier and scheme:
        meta["related_identifiers"] = [{
            "identifier": dmp_identifier,
            "relation": "isDocumentedBy",
            "scheme": scheme,
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

    def _create_draft(self, metadata: dict) -> dict:
        """Create a new Zenodo draft deposition without publishing it."""
        resp = requests.post(f"{self.base}/deposit/depositions",
                             json={}, headers=self._headers(), timeout=30)
        resp.raise_for_status()
        dep = resp.json()
        dep_id = dep["id"]

        resp = requests.put(f"{self.base}/deposit/depositions/{dep_id}",
                            json={"metadata": metadata},
                            headers=self._headers(), timeout=30)
        resp.raise_for_status()
        dep = resp.json()

        self._upload_placeholder(dep_id)

        prereserved_doi = (dep.get("metadata", {})
                           .get("prereserve_doi", {})
                           .get("doi", ""))
        return {"id": str(dep_id), "doi": prereserved_doi}

    def _new_version_draft(self, record_id: str, metadata: dict) -> dict:
        """Add a new draft version to an existing published record.

        record_id must be the Zenodo deposition ID of a *published* record.
        The caller is responsible for ensuring the previous draft was
        published before triggering a new version.
        """
        resp = requests.post(
            f"{self.base}/deposit/depositions/{record_id}/actions/newversion",
            headers=self._headers(), timeout=30)
        resp.raise_for_status()
        new_id = resp.json()["links"]["latest_draft"].rstrip("/").split("/")[-1]

        resp = requests.put(f"{self.base}/deposit/depositions/{new_id}",
                            json={"metadata": metadata},
                            headers=self._headers(), timeout=30)
        resp.raise_for_status()
        dep = resp.json()

        prereserved_doi = (dep.get("metadata", {})
                           .get("prereserve_doi", {})
                           .get("doi", ""))
        return {"id": str(new_id), "doi": prereserved_doi}

    def publish_dmp(self, dmp: Any, state: dict[str, str],
                    version_tag: str) -> dict[str, str]:
        """
        Create Zenodo draft depositions for every dataset in the DMP.

        For each dataset:
          - If its dataset_id is in `state` (stored as a deposition record ID),
            create a new draft version of that record.
          - Otherwise, create a new draft deposition.

        Records are left unpublished; a curator must publish them on Zenodo
        to register the DOI and make the record publicly accessible.

        Returns an updated state dict (dataset_id → deposition record ID).
        """
        from ..state import dataset_key

        updated_state = dict(state)

        for dataset in getattr(dmp, "dataset", None) or []:
            key = dataset_key(dataset)
            metadata = _dataset_to_zenodo_metadata(dmp, dataset, version_tag)

            if key in state:
                result = self._new_version_draft(state[key], metadata)
                action = "new draft version"
            else:
                result = self._create_draft(metadata)
                action = "draft created"

            record_id = result["id"]
            doi = result.get("doi", "(reserved after publish)")
            updated_state[key] = record_id
            print(f"  [{action}] {metadata['title']} → id={record_id} doi={doi}",
                  file=sys.stderr)

        return updated_state
