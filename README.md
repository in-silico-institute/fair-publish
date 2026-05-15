# fair-publish

Policy-enforced FAIR metadata validation and Zenodo publishing pipeline.

Treats dataset metadata publication as a software release process: a
machine-actionable DMP (RDA maDMP schema) is the versioned source of truth;
a GitHub Actions pipeline validates it against institutional policy and deploys
each dataset's metadata to Zenodo on every release.

## How it works

```
Push dmp.json to main  →  fair-validate.yml  →  pass?
                                                    ↓ yes
PI creates GitHub release  →  fair-publish.yml  →  publish each dataset to Zenodo
                                                    →  commit zenodo_state.json
                                                    →  create published/<tag> git tag
```

Two separate workflows keep validation and deployment cleanly separated:

| Workflow | Trigger | Purpose |
|---|---|---|
| `fair-validate.yml` | push / pull_request to main | Validate maDMP against institutional policy; blocks merge if it fails |
| `fair-publish.yml` | release published | Publish each dataset in the DMP to Zenodo; update state; tag |

## Institutional adoption

**Step 1 — Set up the org-level policy (once)**

In your organisation's `ORG/.github` repository:

```
.github/
  workflows/
    fair-validate.yml     ← copy from this repo
    fair-publish.yml      ← copy from this repo
  policies/
    fair-policy.yml       ← copy and edit example/policy.yml
```

Store the Zenodo personal access token as an organisation secret named
`ZENODO_TOKEN`.

**Step 2 — Configure each project repository**

Add two workflow files that call the reusable org workflows:

`.github/workflows/validate.yml`
```yaml
on:
  push:
    branches: [main]
    paths: ["dmp.json"]
  pull_request:
    paths: ["dmp.json"]
jobs:
  validate:
    uses: MY-ORG/.github/.github/workflows/fair-validate.yml@v1
    with:
      dmp_path: dmp.json
```

`.github/workflows/publish.yml`
```yaml
on:
  release:
    types: [published]
jobs:
  publish:
    uses: MY-ORG/.github/.github/workflows/fair-publish.yml@v1
    with:
      dmp_path: dmp.json
    secrets:
      zenodo_token: ${{ secrets.ZENODO_TOKEN }}
```

Commit `dmp.json` (your RDA maDMP) to the repository root.  On the first
successful release, `zenodo_state.json` is created automatically and committed
back by the pipeline.

**Step 3 — Configure branch protection (required)**

> **This step is essential.** Without it, a PI can create a release from a
> branch where validation failed or never ran, bypassing the quality gate
> entirely.

In each project repository go to **Settings → Branches → Branch protection
rules** and add a rule for `main`:

- ✅ Require status checks to pass before merging
  - Add status check: `Validate maDMP` (the job name from `fair-validate.yml`)
- ✅ Require branches to be up to date before merging

At organisation level, you can enforce this automatically using a GitHub
Organisation Ruleset (Settings → Rules → Rulesets) so all new repositories
inherit it without manual configuration.

## CLI usage

Install locally:
```bash
pip install fair-publish
```

Validate before pushing:
```bash
fair-publish validate dmp.json --policy example/policy.yml
```

Simulate a full release cycle against the Zenodo sandbox:
```bash
python simulate_release.py --dmp example/dmp.json \
                           --policy example/policy.yml \
                           --release-tag v1.0.0
```

## Assumptions

- **Institutional GitHub repositories.** DMPs are pushed to GitHub repositories
  owned by the institution, not by individual PIs. This ensures the institution
  retains control and prevents accidental state corruption (forking, deletion).

- **External DMP authoring tool.** The maDMP JSON is produced by an external
  tool with a researcher-friendly interface (e.g. ARGOS, DMPTool, or an
  institutional system). This pipeline is the back-end; the authoring front-end
  is out of scope and assumed to exist or be developed separately.

- **Branch protection is configured.** The two-workflow design only prevents
  non-compliant releases if branch protection rules require the validation
  workflow to pass before a release is allowed. See Step 3 above.

- **Metadata only.** Data file upload is out of scope. Zenodo records are
  created with rich validated metadata; data files must be attached separately
  if required.

## Adding custom rules

Implement the `Rule` protocol from `fair_publish.rules` and register it in
your `policy.yml`:

```python
# myorg_rules/biomedical.py
from fair_publish.rules import RuleResult

class SnomedCtKeyword:
    name = "SNOMED CT keyword present"
    severity = "error"
    fair = "I1, R1.3"

    def __call__(self, dmp, dataset) -> RuleResult:
        keywords = getattr(dataset, "keyword", []) or []
        has_snomed = any("snomed" in k.lower() for k in keywords)
        return RuleResult(
            passed=has_snomed, rule=self.name, severity=self.severity,
            field="dataset.keyword", fair=self.fair,
            message="No SNOMED CT keyword found." if not has_snomed else "OK",
        )
```

```yaml
# policy.yml
extra_rules:
  - module: myorg_rules.biomedical
    class: SnomedCtKeyword
    severity: error
```
