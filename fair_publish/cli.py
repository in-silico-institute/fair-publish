"""
CLI entry point.

    fair-publish validate  dmp.json [--policy policy.yml]
    fair-publish publish   dmp.json [--policy policy.yml] [--state zenodo_state.json]
                                    [--release-tag v1.2.0] [--sandbox] [--format json|text]
    fair-publish report    dmp.json [--policy policy.yml] [--format json|text]
"""

from __future__ import annotations
import json
import sys
from pathlib import Path

import click

from .validator import validate_file, check_schema
from .policy import load as load_policy


@click.group()
def cli():
    """Policy-enforced FAIR metadata validation and Zenodo publishing."""


# ── schema-check ──────────────────────────────────────────────────────────

@cli.command("schema-check")
@click.argument("dmp", type=click.Path(exists=True, path_type=Path))
def schema_check(dmp: Path):
    """Check maDMP structural validity against the RDA maDMP Common Standard.

    Exits 0 if the document is schema-valid, 1 otherwise.
    Run this as a first CI step before policy checking.
    """
    try:
        check_schema(dmp)
        click.echo(f"Schema valid: {dmp}")
    except Exception as exc:
        click.echo(f"Schema error: {exc}", err=True)
        sys.exit(1)


# ── validate ──────────────────────────────────────────────────────────────

@cli.command()
@click.argument("dmp", type=click.Path(exists=True, path_type=Path))
@click.option("--policy", default="policy.yml", show_default=True,
              type=click.Path(path_type=Path))
@click.option("--skip-schema", is_flag=True, default=False,
              help="Skip RDA schema check (use when schema-check ran as a prior step).")
def validate(dmp: Path, policy: Path, skip_schema: bool):
    """Validate maDMP against institutional policy. Exit 1 if any error-level rule fails."""
    report = validate_file(dmp, policy, skip_schema=skip_schema)
    click.echo(report.summary())
    sys.exit(0 if report.passed else 1)


# ── report ────────────────────────────────────────────────────────────────

@cli.command()
@click.argument("dmp", type=click.Path(exists=True, path_type=Path))
@click.option("--policy", default="policy.yml", show_default=True,
              type=click.Path(path_type=Path))
@click.option("--format", "fmt", default="text",
              type=click.Choice(["text", "json"]), show_default=True)
@click.option("--skip-schema", is_flag=True, default=False,
              help="Skip RDA schema check (use when schema-check ran as a prior step).")
def report(dmp: Path, policy: Path, fmt: str, skip_schema: bool):
    """Print validation report without publishing."""
    r = validate_file(dmp, policy, skip_schema=skip_schema)
    click.echo(json.dumps(r.to_dict(), indent=2) if fmt == "json" else r.summary())
    sys.exit(0 if r.passed else 1)


# ── publish ───────────────────────────────────────────────────────────────

@cli.command()
@click.argument("dmp", type=click.Path(exists=True, path_type=Path))
@click.option("--policy", default="policy.yml", show_default=True,
              type=click.Path(path_type=Path))
@click.option("--state", "state_path", default="zenodo_state.json", show_default=True,
              type=click.Path(path_type=Path),
              help="Zenodo state file mapping dataset_id → concept_doi.")
@click.option("--release-tag", default="",
              help="Git release tag used as the Zenodo version string (e.g. v1.2.0).")
@click.option("--sandbox", is_flag=True, default=False)
@click.option("--format", "fmt", default="text",
              type=click.Choice(["text", "json"]), show_default=True)
def publish(dmp: Path, policy: Path, state_path: Path,
            release_tag: str, sandbox: bool, fmt: str):
    """Validate then publish all datasets in the maDMP to Zenodo."""
    import madmpy
    import json as _json

    # --- Validate first ---
    policy_cfg = load_policy(policy)
    report_obj = validate_file(dmp, policy)
    if fmt == "text":
        click.echo(report_obj.summary())
    if not report_obj.passed:
        click.echo("Aborting: fix errors before publishing.", err=True)
        sys.exit(1)

    # --- Load DMP via madmpy ---
    madmpy.validate_DMP(str(dmp))
    dmp_module = madmpy.load()
    with dmp.open() as f:
        data = _json.load(f)
    dmp_obj = dmp_module.DMP(**data["dmp"])

    # --- Load state ---
    from .state import load as load_state, save as save_state
    repo_root = dmp.parent
    state = load_state(repo_root)

    # --- Publish ---
    from .adapters.zenodo import ZenodoAdapter
    adapter = ZenodoAdapter(sandbox=sandbox)
    version_tag = release_tag or "1.0.0"
    updated_state = adapter.publish_dmp(
        dmp_obj, state, version_tag,
        policy_version=policy_cfg.version,
    )

    # --- Persist state ---
    save_state(repo_root, updated_state)

    # --- Output ---
    new_entries = {k: v for k, v in updated_state.items() if k not in state}
    updated_entries = {k: v for k, v in updated_state.items() if k in state}
    result = {"published": updated_state,
              "new": new_entries, "updated": updated_entries}

    if fmt == "json":
        click.echo(_json.dumps(result, indent=2))
    else:
        click.echo(f"\nPublished {len(updated_state)} dataset(s).")
        for k, entry in updated_state.items():
            action = "new" if k in new_entries else "updated"
            click.echo(f"  [{action}] {k} → record={entry['record_id']} "
                       f"policy={entry['policy_version']}")


def main():
    cli()
