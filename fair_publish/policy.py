"""
Policy loader.

A policy.yml file specifies which rules to apply and at what severity,
and may reference external rule modules supplied by the institution.

Example policy.yml:

    rules:
      - name: Creator ORCID present
        severity: warning          # override built-in default
      - name: Version string present
        severity: error
      - name: License present and recognized
        severity: error
      - name: At least one keyword
        severity: error            # institution raises this to blocking
      - name: Description length
        severity: warning
        config:
          min_chars: 150           # institution-specific threshold
      - name: Access rights stated
        severity: error

    # Optional: extra rules from an institution's own module
    extra_rules:
      - module: myorg_rules.snomed
        class: SnomedCtKeyword
        severity: error

    targets:
      - zenodo

Institutions that want stricter defaults copy this file into their
.github/policies/ directory and commit it; the reusable GHA workflow
picks it up automatically.
"""

from __future__ import annotations
import importlib
import importlib.util
import sys
from pathlib import Path
from dataclasses import dataclass, field

import yaml

from .rules import Rule, DEFAULT_RULES, DescriptionLength


@dataclass
class PolicyConfig:
    rules: list[Rule]
    targets: list[str] = field(default_factory=lambda: ["zenodo"])


def _load_builtin(name: str, severity: str, config: dict) -> Rule | None:
    for rule in DEFAULT_RULES:
        if rule.name == name:
            # Apply config overrides via shallow copy trick
            instance = rule.__class__()
            object.__setattr__(instance, "severity", severity) if hasattr(instance, "__dataclass_fields__") else None
            instance.severity = severity
            if config and hasattr(instance, "__dict__"):
                for k, v in config.items():
                    setattr(instance, k, v)
            return instance
    return None


def _load_external(module_path: str, class_name: str, severity: str, config: dict) -> Rule:
    # Support both dotted module names and file paths
    p = Path(module_path)
    if p.exists():
        spec = importlib.util.spec_from_file_location("_ext_rule", p)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    else:
        mod = importlib.import_module(module_path)
    cls = getattr(mod, class_name)
    instance = cls()
    instance.severity = severity
    for k, v in (config or {}).items():
        setattr(instance, k, v)
    return instance


def load(policy_path: Path) -> PolicyConfig:
    if not policy_path.exists():
        return PolicyConfig(rules=list(DEFAULT_RULES))

    with policy_path.open() as f:
        data = yaml.safe_load(f) or {}

    rules: list[Rule] = []
    for entry in data.get("rules", []):
        name = entry["name"]
        severity = entry.get("severity", "error")
        config = entry.get("config", {})
        rule = _load_builtin(name, severity, config)
        if rule is None:
            raise ValueError(f"Unknown built-in rule: '{name}'. "
                             "Use extra_rules for custom modules.")
        rules.append(rule)

    for entry in data.get("extra_rules", []):
        rule = _load_external(
            entry["module"], entry["class"],
            entry.get("severity", "error"),
            entry.get("config", {}),
        )
        rules.append(rule)

    if not rules:
        rules = list(DEFAULT_RULES)

    return PolicyConfig(
        rules=rules,
        targets=data.get("targets", ["zenodo"]),
    )
