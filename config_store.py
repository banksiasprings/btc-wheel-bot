"""
config_store.py — Named configuration management.

Configs are stored as YAML files in the configs/ directory.
Each config has a name, metadata, and the full parameter set.

Usage:
    from config_store import list_configs, save_config, load_config_by_name
"""

from __future__ import annotations

import copy
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

BASE_DIR    = Path(__file__).parent
CONFIGS_DIR = BASE_DIR / "configs"


def _ensure_configs_dir() -> Path:
    CONFIGS_DIR.mkdir(parents=True, exist_ok=True)
    return CONFIGS_DIR


def _master_config() -> dict:
    """Return the master config.yaml as a dict."""
    try:
        return yaml.safe_load((BASE_DIR / "config.yaml").read_text()) or {}
    except Exception:
        return {}


def _deep_merge(base: dict, overrides: dict) -> dict:
    """Recursively merge overrides into a copy of base."""
    import copy as _copy
    result = _copy.deepcopy(base)
    for k, v in overrides.items():
        if isinstance(v, dict) and isinstance(result.get(k), dict):
            result[k] = _deep_merge(result[k], v)
        else:
            result[k] = v
    return result


def _config_path(name: str) -> Path:
    return CONFIGS_DIR / f"{name}.yaml"


# ── Public API ────────────────────────────────────────────────────────────────


def get_config_yaml_path(name: str) -> str:
    """Return absolute path to a named config's YAML file."""
    return str(_config_path(name))


def list_configs() -> list[dict]:
    """Return all saved configs with metadata: name, created_at, source, fitness, notes."""
    _ensure_configs_dir()
    configs: list[dict] = []
    for yaml_file in sorted(CONFIGS_DIR.glob("*.yaml")):
        try:
            data = yaml.safe_load(yaml_file.read_text()) or {}
            meta = data.get("_meta", {})
            configs.append({
                "name":       meta.get("name", yaml_file.stem),
                "slug":       yaml_file.stem,
                "created_at": meta.get("created_at"),
                "source":     meta.get("source", "manual"),
                "fitness":    meta.get("fitness"),
                "goal":       meta.get("goal"),
                "notes":      meta.get("notes", ""),
            })
        except Exception:
            continue
    return configs


def save_config(
    name: str,
    params: dict,
    source: str = "manual",
    metadata: dict | None = None,
) -> dict:
    """
    Save a named config. Merges params over the master config.yaml as base.
    Returns the saved config dict.

    params should be a nested dict matching config.yaml sections, e.g.:
        {"strategy": {"iv_rank_threshold": 0.55}, "sizing": {"max_equity_per_leg": 0.08}}
    """
    _ensure_configs_dir()
    metadata = metadata or {}

    # Build _meta block
    meta: dict[str, Any] = {
        "name":       name,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source":     source,
        "fitness":    metadata.get("fitness"),
        "goal":       metadata.get("goal"),
        "notes":      metadata.get("notes", ""),
    }
    # Include any extra metadata keys
    for k, v in metadata.items():
        if k not in meta:
            meta[k] = v

    # Start from master, merge params on top so unspecified keys fall back
    merged = _deep_merge(_master_config(), params)
    merged["_meta"] = meta

    path = _config_path(name)
    with open(path, "w") as f:
        yaml.dump(merged, f, default_flow_style=False, allow_unicode=True)

    return merged


def load_config_by_name(name: str) -> dict:
    """
    Load a named config as a dict, merged over the master config.yaml.
    Raises FileNotFoundError if not found.
    """
    path = _config_path(name)
    if not path.exists():
        raise FileNotFoundError(f"Named config not found: {name!r} (looked in {path})")

    named_raw = yaml.safe_load(path.read_text()) or {}

    # Strip _meta before merging so it doesn't pollute config structure
    meta = named_raw.pop("_meta", {})

    # Merge named config OVER master so only overridden fields differ
    merged = _deep_merge(_master_config(), named_raw)
    merged["_meta"] = meta   # put meta back at top level

    return merged


def delete_config(name: str) -> bool:
    """Delete a named config. Returns True if deleted, False if not found."""
    path = _config_path(name)
    if not path.exists():
        return False
    path.unlink()
    return True


def promote_to_live(name: str) -> dict:
    """
    Copy a named config's params over config.yaml (the live/master config).
    Creates a backup of the current config.yaml first (config.yaml.bak).
    Returns the new live config dict.
    """
    path = _config_path(name)
    if not path.exists():
        raise FileNotFoundError(f"Named config not found: {name!r}")

    live_path = BASE_DIR / "config.yaml"
    bak_path  = BASE_DIR / "config.yaml.bak"

    # Backup current live config
    if live_path.exists():
        shutil.copy2(live_path, bak_path)

    # Load named config (strip _meta), merge over live
    named_raw = yaml.safe_load(path.read_text()) or {}
    named_raw.pop("_meta", None)

    current_live = yaml.safe_load(live_path.read_text()) if live_path.exists() else {}
    new_live = _deep_merge(current_live or {}, named_raw)

    with open(live_path, "w") as f:
        yaml.dump(new_live, f, default_flow_style=False, allow_unicode=True)

    # Mark the named config as promoted
    full_named = yaml.safe_load(path.read_text()) or {}
    if "_meta" in full_named:
        full_named["_meta"]["source"] = "promoted"
        full_named["_meta"]["promoted_at"] = datetime.now(timezone.utc).isoformat()
    with open(path, "w") as f:
        yaml.dump(full_named, f, default_flow_style=False, allow_unicode=True)

    return new_live


# ── Genome → config params helper ────────────────────────────────────────────

# Maps genome field names to (config_section, config_key)
GENOME_TO_CONFIG: dict[str, tuple[str, str]] = {
    "iv_rank_threshold":        ("strategy", "iv_rank_threshold"),
    "target_delta_min":         ("strategy", "target_delta_min"),
    "target_delta_max":         ("strategy", "target_delta_max"),
    "max_dte":                  ("strategy", "max_dte"),
    "min_dte":                  ("strategy", "min_dte"),
    "max_equity_per_leg":       ("sizing",   "max_equity_per_leg"),
    "min_free_equity_fraction": ("sizing",   "min_free_equity_fraction"),
    "premium_fraction_of_spot": ("backtest", "premium_fraction_of_spot"),
    "approx_otm_offset":        ("backtest", "approx_otm_offset"),
    "starting_equity":          ("backtest", "starting_equity"),
}


def genome_to_params(genome: dict) -> dict:
    """Convert a flat genome dict to a nested config params dict."""
    params: dict = {}
    for genome_key, (section, config_key) in GENOME_TO_CONFIG.items():
        if genome_key in genome:
            params.setdefault(section, {})[config_key] = genome[genome_key]
    return params
