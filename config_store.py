"""
config_store.py — Named configuration management.

Configs are stored as YAML files in the configs/ directory.
Each config has a name, metadata, and the full parameter set.

Status lifecycle: draft → validated → paper → ready → live → archived

Usage:
    from config_store import list_configs, save_config, load_config_by_name
"""

from __future__ import annotations

import copy
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

BASE_DIR    = Path(__file__).parent
CONFIGS_DIR = BASE_DIR / "configs"

VALID_STATUSES = ("draft", "validated", "paper", "ready", "live", "archived")


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


def _slugify(name: str) -> str:
    """Convert a config name to a filesystem-safe slug."""
    slug = name.lower()
    slug = re.sub(r"[^a-z0-9]+", "-", slug)
    slug = slug.strip("-")
    return slug or "config"


def _read_meta(yaml_file: Path) -> dict:
    """Read _meta block from a config YAML file."""
    try:
        data = yaml.safe_load(yaml_file.read_text()) or {}
        return data.get("_meta", {})
    except Exception:
        return {}


def _config_summary_from_data(data: dict, yaml_file: Path) -> dict:
    """Build the list-view summary dict from a loaded config data dict."""
    meta     = data.get("_meta", {})
    strategy = data.get("strategy", {})
    sizing   = data.get("sizing", {})
    backtest = data.get("backtest", {})
    params = {
        k: v for k, v in {
            "iv_rank_threshold":        strategy.get("iv_rank_threshold"),
            "target_delta_min":         strategy.get("target_delta_min"),
            "target_delta_max":         strategy.get("target_delta_max"),
            "min_dte":                  strategy.get("min_dte"),
            "max_dte":                  strategy.get("max_dte"),
            "max_equity_per_leg":       sizing.get("max_equity_per_leg"),
            "min_free_equity_fraction": sizing.get("min_free_equity_fraction"),
            "premium_fraction_of_spot": backtest.get("premium_fraction_of_spot"),
            "approx_otm_offset":        backtest.get("approx_otm_offset"),
            "starting_equity":          backtest.get("starting_equity"),
        }.items() if v is not None
    }
    return {
        "name":            meta.get("name", yaml_file.stem),
        "slug":            yaml_file.stem,
        "status":          meta.get("status", "draft"),
        "created_at":      meta.get("created_at"),
        "source":          meta.get("source", "manual"),
        "fitness":         meta.get("fitness"),
        "goal":            meta.get("goal"),
        "notes":           meta.get("notes", ""),
        "total_return_pct": meta.get("total_return_pct"),
        "sharpe":          meta.get("sharpe"),
        "params":          params,
    }


# ── Public API ────────────────────────────────────────────────────────────────


def get_config_yaml_path(name: str) -> str:
    """Return absolute path to a named config's YAML file."""
    return str(_config_path(name))


def list_configs(include_archived: bool = False) -> list[dict]:
    """
    Return all saved configs with metadata.
    By default, archived configs are excluded.
    """
    _ensure_configs_dir()
    configs: list[dict] = []
    for yaml_file in sorted(CONFIGS_DIR.glob("*.yaml")):
        try:
            data = yaml.safe_load(yaml_file.read_text()) or {}
            summary = _config_summary_from_data(data, yaml_file)
            if not include_archived and summary.get("status") == "archived":
                continue
            configs.append(summary)
        except Exception:
            continue
    return configs


def save_config(
    name: str,
    params: dict,
    source: str = "manual",
    metadata: dict | None = None,
    status: str = "draft",
) -> dict:
    """
    Save a named config. Merges params over the master config.yaml as base.
    Returns the saved config dict.

    params should be a nested dict matching config.yaml sections, e.g.:
        {"strategy": {"iv_rank_threshold": 0.55}, "sizing": {"max_equity_per_leg": 0.08}}
    """
    _ensure_configs_dir()
    metadata = metadata or {}

    if status not in VALID_STATUSES:
        raise ValueError(f"Invalid status {status!r}. Must be one of: {VALID_STATUSES}")

    # Build _meta block
    meta: dict[str, Any] = {
        "name":       name,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source":     source,
        "status":     status,
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


def set_status(name: str, status: str) -> dict:
    """Update a config's status. Valid: draft, validated, paper, ready, live, archived"""
    if status not in VALID_STATUSES:
        raise ValueError(f"Invalid status {status!r}. Must be one of: {VALID_STATUSES}")

    path = _config_path(name)
    if not path.exists():
        raise FileNotFoundError(f"Named config not found: {name!r}")

    data = yaml.safe_load(path.read_text()) or {}
    data.setdefault("_meta", {})["status"] = status

    with open(path, "w") as f:
        yaml.dump(data, f, default_flow_style=False, allow_unicode=True)

    return _config_summary_from_data(data, path)


def rename_config(old_name: str, new_name: str) -> dict:
    """Rename a config file and update _meta.name inside it."""
    old_path = _config_path(old_name)
    if not old_path.exists():
        raise FileNotFoundError(f"Named config not found: {old_name!r}")

    new_path = _config_path(new_name)
    if new_path.exists():
        raise ValueError(f"Config {new_name!r} already exists")

    data = yaml.safe_load(old_path.read_text()) or {}
    data.setdefault("_meta", {})["name"] = new_name

    with open(new_path, "w") as f:
        yaml.dump(data, f, default_flow_style=False, allow_unicode=True)

    old_path.unlink()
    return _config_summary_from_data(data, new_path)


def update_config_notes(name: str, notes: str) -> dict:
    """Update the notes field in _meta."""
    path = _config_path(name)
    if not path.exists():
        raise FileNotFoundError(f"Named config not found: {name!r}")

    data = yaml.safe_load(path.read_text()) or {}
    data.setdefault("_meta", {})["notes"] = notes

    with open(path, "w") as f:
        yaml.dump(data, f, default_flow_style=False, allow_unicode=True)

    return _config_summary_from_data(data, path)


def update_config_params(name: str, params: dict) -> dict:
    """Update the strategy/sizing/risk params of a config (merges, doesn't replace)."""
    path = _config_path(name)
    if not path.exists():
        raise FileNotFoundError(f"Named config not found: {name!r}")

    data = yaml.safe_load(path.read_text()) or {}
    meta = data.pop("_meta", {})

    # Deep merge params into existing data
    merged_params = _deep_merge(data, params)
    merged_params["_meta"] = meta

    with open(path, "w") as f:
        yaml.dump(merged_params, f, default_flow_style=False, allow_unicode=True)

    return _config_summary_from_data(merged_params, path)


def duplicate_config(name: str, new_name: str) -> dict:
    """Create a copy of a config with a new name, status reset to draft."""
    path = _config_path(name)
    if not path.exists():
        raise FileNotFoundError(f"Named config not found: {name!r}")

    new_path = _config_path(new_name)
    if new_path.exists():
        raise ValueError(f"Config {new_name!r} already exists")

    data = yaml.safe_load(path.read_text()) or {}
    data.setdefault("_meta", {}).update({
        "name":       new_name,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source":     "duplicated",
        "status":     "draft",
    })

    with open(new_path, "w") as f:
        yaml.dump(data, f, default_flow_style=False, allow_unicode=True)

    return _config_summary_from_data(data, new_path)


def archive_config(name: str) -> dict:
    """Shortcut: set_status(name, 'archived')"""
    return set_status(name, "archived")


def delete_config(name: str) -> bool:
    """Delete a config file. Refuses if status is 'live'."""
    path = _config_path(name)
    if not path.exists():
        return False

    # Read status before deleting
    try:
        data = yaml.safe_load(path.read_text()) or {}
        status = data.get("_meta", {}).get("status", "draft")
        if status == "live":
            raise ValueError(f"Cannot delete config {name!r} while status is 'live'. Archive it first.")
    except ValueError:
        raise
    except Exception:
        pass

    path.unlink()
    return True


def get_paper_configs() -> list[dict]:
    """Return all configs with status == 'paper' — used by farm supervisor."""
    _ensure_configs_dir()
    results: list[dict] = []
    for yaml_file in sorted(CONFIGS_DIR.glob("*.yaml")):
        try:
            data = yaml.safe_load(yaml_file.read_text()) or {}
            meta = data.get("_meta", {})
            if meta.get("status") == "paper":
                summary = _config_summary_from_data(data, yaml_file)
                summary["config_path"] = str(yaml_file)
                summary["slug"] = _slugify(meta.get("name", yaml_file.stem))
                results.append(summary)
        except Exception:
            continue
    return results


def promote_to_live(name: str, starting_equity: float) -> dict:
    """
    Copy a named config to config.yaml (the live bot config).
    - Forces testnet=false (live trading only)
    - Sets starting_equity to the provided value
    - Backs up current config.yaml to config.yaml.bak
    - Sets the promoted config's status to 'live'
    - Sets any previously 'live' config to 'archived'
    - Logs the promotion event
    Returns a dict with the new live config and a _promotion_log entry.
    """
    import json as _json

    path = _config_path(name)
    if not path.exists():
        raise FileNotFoundError(f"Named config not found: {name!r}")

    live_path = BASE_DIR / "config.yaml"
    bak_path  = BASE_DIR / "config.yaml.bak"

    # Backup current live config
    if live_path.exists():
        shutil.copy2(live_path, bak_path)
    bak_path_str = str(bak_path) if live_path.exists() else None

    # Load named config (strip _meta), merge over live
    named_raw = yaml.safe_load(path.read_text()) or {}
    named_raw.pop("_meta", None)

    current_live = yaml.safe_load(live_path.read_text()) if live_path.exists() else {}
    new_live = _deep_merge(current_live or {}, named_raw)

    # Force mainnet — never allow testnet in live
    new_live.setdefault("deribit", {})["testnet"] = False

    # Set starting equity from the provided value
    new_live.setdefault("backtest", {})["starting_equity"] = starting_equity

    with open(live_path, "w") as f:
        yaml.dump(new_live, f, default_flow_style=False, allow_unicode=True)

    # Archive any previously live configs
    for yaml_file in sorted(CONFIGS_DIR.glob("*.yaml")):
        if yaml_file == path:
            continue
        try:
            d = yaml.safe_load(yaml_file.read_text()) or {}
            if d.get("_meta", {}).get("status") == "live":
                d["_meta"]["status"] = "archived"
                with open(yaml_file, "w") as f:
                    yaml.dump(d, f, default_flow_style=False, allow_unicode=True)
        except Exception:
            pass

    # Mark the named config as promoted + live
    full_named = yaml.safe_load(path.read_text()) or {}
    if "_meta" in full_named:
        full_named["_meta"]["source"] = "promoted"
        full_named["_meta"]["status"] = "live"
        full_named["_meta"]["promoted_at"] = datetime.now(timezone.utc).isoformat()
    with open(path, "w") as f:
        yaml.dump(full_named, f, default_flow_style=False, allow_unicode=True)

    # Write promotion log entry
    log_entry = {
        "timestamp":               datetime.now(timezone.utc).isoformat(),
        "config_name":             name,
        "starting_equity":         starting_equity,
        "previous_config_backup_path": bak_path_str,
    }
    log_path = BASE_DIR / "data" / "promotion_log.json"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    existing_log: list = []
    if log_path.exists():
        try:
            existing_log = _json.loads(log_path.read_text())
        except Exception:
            pass
    existing_log.insert(0, log_entry)
    log_path.write_text(_json.dumps(existing_log, indent=2))

    return {"config": new_live, "promotion_log": log_entry}


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
