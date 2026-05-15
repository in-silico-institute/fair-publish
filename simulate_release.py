"""
Simulate a Principal Investigator pushing a DMP release.

In production, the PI uses an institutional DMP tool (e.g. ARGOS, DMPTool)
that exports a machine-actionable RDA DMP JSON and then creates a GitHub
release.  This script reproduces that action locally so the pipeline can
be tested end-to-end without a real GitHub repository or Zenodo account.

What it does:
  1. Copies the DMP file to a temp working directory.
  2. Runs fair-publish validate to check institutional policy.
  3. If valid, runs fair-publish publish --sandbox to push each dataset
     to the Zenodo sandbox and write zenodo_state.json.
  4. Prints a summary of what was published.

Usage:
    python simulate_release.py --dmp example/dmp.json \\
                               --policy example/policy.yml \\
                               --release-tag v1.0.0
"""

import argparse
import subprocess
import sys
from pathlib import Path


def run(cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
    print(f"\n$ {' '.join(cmd)}")
    result = subprocess.run(cmd, **kwargs)
    if result.returncode != 0:
        print(f"\n[FAILED] Exit code {result.returncode}", file=sys.stderr)
        sys.exit(result.returncode)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Simulate a PI DMP release.")
    parser.add_argument("--dmp", required=True, help="Path to the maDMP JSON file.")
    parser.add_argument("--policy", default=None, help="Path to institution policy YAML.")
    parser.add_argument("--release-tag", default="v1.0.0",
                        help="Simulated release tag (used as Zenodo version string).")
    parser.add_argument("--sandbox", action="store_true", default=True,
                        help="Publish to Zenodo sandbox (default: true).")
    parser.add_argument("--no-sandbox", dest="sandbox", action="store_false",
                        help="Publish to Zenodo production.")
    args = parser.parse_args()

    dmp = Path(args.dmp)
    policy_args = ["--policy", args.policy] if args.policy else []
    sandbox_args = ["--sandbox"] if args.sandbox else []

    print("=" * 60)
    print(f"  FAIR Pipeline — simulated release {args.release_tag}")
    print(f"  DMP: {dmp}")
    print(f"  Target: {'Zenodo SANDBOX' if args.sandbox else 'Zenodo PRODUCTION'}")
    print("=" * 60)

    # Step 1: Validate
    print("\n── Step 1: Validate maDMP ──")
    run(["fair-publish", "validate", str(dmp)] + policy_args)

    # Step 2: Publish
    print("\n── Step 2: Publish datasets ──")
    run([
        "fair-publish", "publish", str(dmp),
        "--release-tag", args.release_tag,
        "--format", "text",
    ] + policy_args + sandbox_args)

    print("\n── Done ──")
    state_file = dmp.parent / "zenodo_state.json"
    if state_file.exists():
        print(f"State written to: {state_file}")
        print("Commit this file to your repository so future releases\n"
              "update existing Zenodo records instead of creating new ones.")


if __name__ == "__main__":
    main()
