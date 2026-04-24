"""
build_reconstruction_guide.py

Builds a fully self-contained Jupyter notebook for the BTC Wheel Bot.
Every source file is embedded inline using base64 — no shutil.copy() calls.
Run from the btc-wheel-bot project root:

    python3 build_reconstruction_guide.py
"""

import base64
import json
import os
import pathlib
import sys

import nbformat
from nbformat.v4 import new_code_cell, new_markdown_cell, new_notebook

BASE = pathlib.Path(__file__).parent

# ── Helper: read file and return base64 string ────────────────────────────────

def b64(rel_path: str) -> str:
    """Read a file relative to BASE and return its base64-encoded content."""
    p = BASE / rel_path
    if not p.exists():
        print(f"  WARNING: {rel_path} not found — skipping")
        return ""
    raw = p.read_bytes()
    return base64.b64encode(raw).decode("ascii")


def make_file_cell(filename: str, rel_path: str, description: str = "") -> nbformat.NotebookNode:
    """
    Return a code cell that writes `filename` using base64-decoded content.
    Uses pathlib.Path.write_text so the file is created in the CWD when the
    notebook is run.
    """
    encoded = b64(rel_path)
    if not encoded:
        # File missing — return a stub cell
        src = f'# WARNING: {rel_path} was not found at build time — skipping\nprint("SKIPPED: {filename}")\n'
        return new_code_cell(source=src)

    lines = len((BASE / rel_path).read_text(errors="replace").splitlines())
    desc_comment = f"# {description}\n" if description else ""

    src = f"""{desc_comment}import pathlib, base64
_content_b64 = (
    "{encoded}"
)
_content = base64.b64decode(_content_b64).decode("utf-8")
pathlib.Path("{filename}").parent.mkdir(parents=True, exist_ok=True)
pathlib.Path("{filename}").write_text(_content, encoding="utf-8")
print(f"Written: {filename} ({lines} lines)")
"""
    return new_code_cell(source=src)


def make_multi_file_cell(files: list[tuple[str, str]], description: str = "") -> nbformat.NotebookNode:
    """
    Write multiple small files in a single cell.
    files: list of (dest_filename, rel_src_path)
    """
    desc_comment = f"# {description}\n" if description else ""
    parts = [f"{desc_comment}import pathlib, base64\n"]
    for dest, rel_src in files:
        encoded = b64(rel_src)
        if not encoded:
            parts.append(f'print("SKIPPED: {dest} (not found)")\n')
            continue
        lines = len((BASE / rel_src).read_text(errors="replace").splitlines())
        parts.append(f"""
_b64_{dest.replace("/","_").replace(".","_").replace("-","_")} = "{encoded}"
pathlib.Path("{dest}").parent.mkdir(parents=True, exist_ok=True)
pathlib.Path("{dest}").write_text(base64.b64decode(_b64_{dest.replace("/","_").replace(".","_").replace("-","_")}).decode("utf-8"), encoding="utf-8")
print(f"Written: {dest} ({lines} lines)")
""")
    return new_code_cell(source="".join(parts))


# ── Read the original notebook for markdown cells ─────────────────────────────

orig_nb_path = BASE / "btc_wheel_bot_reconstruction_guide.ipynb"
orig_nb = nbformat.read(str(orig_nb_path), as_version=4)

# Extract markdown cells by index (preserving their exact content)
md_cells = {i: cell for i, cell in enumerate(orig_nb.cells) if cell.cell_type == "markdown"}
# Extract non-shutil code cells (keep them verbatim)
keep_code_cells = {
    i: cell for i, cell in enumerate(orig_nb.cells)
    if cell.cell_type == "code" and "shutil" not in cell.source
}

print(f"Original notebook: {len(orig_nb.cells)} cells")
print(f"  Markdown cells: {len(md_cells)}")
shutil_cells = [i for i, c in enumerate(orig_nb.cells) if c.cell_type == "code" and "shutil" in c.source]
print(f"  shutil.copy cells to replace: {len(shutil_cells)}")

# ── Build the new notebook cell list ─────────────────────────────────────────

cells = []

# We'll iterate the original cells and replace shutil ones with embedded versions.
# The embedded cells are keyed by what they write (matched by their index).

# Map: original cell index → replacement embedded cell
replacements = {}

# Cell 21: config.yaml
replacements[21] = make_file_cell(
    "config.yaml", "config.yaml",
    "Step 13 - Write config.yaml (embedded inline)"
)

# Cell 23: config.py
replacements[23] = make_file_cell(
    "config.py", "config.py",
    "Step 14 - Write config.py (embedded inline)"
)

# Cell 32: deribit_client.py
replacements[32] = make_file_cell(
    "deribit_client.py", "deribit_client.py",
    "Step 19 - Write deribit_client.py (embedded inline)"
)

# Cell 42: strategy.py
replacements[42] = make_file_cell(
    "strategy.py", "strategy.py",
    "Step 24 - Write strategy.py (embedded inline)"
)

# Cell 48: risk_manager.py
replacements[48] = make_file_cell(
    "risk_manager.py", "risk_manager.py",
    "Step 27 - Write risk_manager.py (embedded inline)"
)

# Cell 55: order_tracker.py (first shutil in that cell block)
replacements[55] = make_file_cell(
    "order_tracker.py", "order_tracker.py",
    "Step 30 - Write order_tracker.py (fill tracking + slippage, embedded inline)"
)

# Cell 56: ai_overseer.py
replacements[56] = make_file_cell(
    "ai_overseer.py", "ai_overseer.py",
    "Step 30 - Write ai_overseer.py (LLM safety layer, embedded inline)"
)

# Cell 58: notifier.py
replacements[58] = make_file_cell(
    "notifier.py", "notifier.py",
    "Step 31 - Write notifier.py (embedded inline)"
)

# Cell 60: hedge_manager.py
replacements[60] = make_file_cell(
    "hedge_manager.py", "hedge_manager.py",
    "Step 32 - Write hedge_manager.py (embedded inline)"
)

# Cell 62: bot.py
replacements[62] = make_file_cell(
    "bot.py", "bot.py",
    "Step 33 - Write bot.py (main async trading loop, embedded inline)"
)

# Cell 64: main.py
replacements[64] = make_file_cell(
    "main.py", "main.py",
    "Step 34 - Write main.py (CLI entry point, embedded inline)"
)

# Cell 70: backtester.py
replacements[70] = make_file_cell(
    "backtester.py", "backtester.py",
    "Step 37 - Write backtester.py (embedded inline)"
)

# Cell 78: optimizer.py
replacements[78] = make_file_cell(
    "optimizer.py", "optimizer.py",
    "Step 41 - Write optimizer.py (embedded inline)"
)

# Cell 86: config_store.py
replacements[86] = make_file_cell(
    "config_store.py", "config_store.py",
    "Step 45 - Write config_store.py (embedded inline)"
)

# Cell 92: readiness_validator.py
replacements[92] = make_file_cell(
    "readiness_validator.py", "readiness_validator.py",
    "Step 48 - Write readiness_validator.py (embedded inline)"
)

# Cell 94: bot_farm.py
replacements[94] = make_file_cell(
    "bot_farm.py", "bot_farm.py",
    "Step 49 - Write bot_farm.py (embedded inline)"
)

# Cell 96: farm_config.yaml
replacements[96] = make_file_cell(
    "farm_config.yaml", "farm_config.yaml",
    "Step 50 - Write farm_config.yaml (embedded inline)"
)

# Cell 102: api.py
replacements[102] = make_file_cell(
    "api.py", "api.py",
    "Step 53 - Write api.py (embedded inline)"
)

# Cell 113: vite.config.ts
replacements[113] = make_file_cell(
    "mobile-app/vite.config.ts", "mobile-app/vite.config.ts",
    "Step 59 - Write vite.config.ts (embedded inline)"
)

# Cell 115: api.ts
replacements[115] = make_file_cell(
    "mobile-app/src/api.ts", "mobile-app/src/api.ts",
    "Step 60 - Write mobile-app/src/api.ts (embedded inline)"
)

# Cell 117: App.tsx
replacements[117] = make_file_cell(
    "mobile-app/src/App.tsx", "mobile-app/src/App.tsx",
    "Step 61 - Write mobile-app/src/App.tsx (embedded inline)"
)

# Cell 119: component files (multi-file cell)
component_files = [
    ("mobile-app/src/components/Dashboard.tsx",   "mobile-app/src/components/Dashboard.tsx"),
    ("mobile-app/src/components/Pipeline.tsx",    "mobile-app/src/components/Pipeline.tsx"),
    ("mobile-app/src/components/Performance.tsx", "mobile-app/src/components/Performance.tsx"),
    ("mobile-app/src/components/Diagnostics.tsx", "mobile-app/src/components/Diagnostics.tsx"),
    ("mobile-app/src/components/Settings.tsx",    "mobile-app/src/components/Settings.tsx"),
    ("mobile-app/src/components/ConfigLibrary.tsx","mobile-app/src/components/ConfigLibrary.tsx"),
    ("mobile-app/src/components/ConfigSelector.tsx","mobile-app/src/components/ConfigSelector.tsx"),
    # Fix 1: previously missing frontend files
    ("mobile-app/src/components/InfoModal.tsx",   "mobile-app/src/components/InfoModal.tsx"),
    ("mobile-app/src/components/SetupScreen.tsx", "mobile-app/src/components/SetupScreen.tsx"),
    ("mobile-app/src/components/SystemGuide.tsx", "mobile-app/src/components/SystemGuide.tsx"),
    ("mobile-app/src/lib/glossary.ts",            "mobile-app/src/lib/glossary.ts"),
]
replacements[119] = make_multi_file_cell(
    component_files,
    "Steps 62-67 - Write all React component files (embedded inline)"
)

# Cell 121: remaining mobile-app files (copy glob)
# We handle this by listing all the remaining interesting files explicitly
remaining_mobile = [
    ("mobile-app/package.json", "mobile-app/package.json"),
]
# Check for other relevant files in mobile-app root that aren't already covered
for fname in ["tailwind.config.js", "postcss.config.js", "tsconfig.json",
              "tsconfig.node.json", "index.html", ".gitignore"]:
    rel = f"mobile-app/{fname}"
    if (BASE / rel).exists():
        remaining_mobile.append((rel, rel))

replacements[121] = make_multi_file_cell(
    remaining_mobile,
    "Step 68 - Write remaining mobile-app config files (embedded inline)"
)

# Cell 127: deploy-mobile.yml
replacements[127] = make_file_cell(
    ".github/workflows/deploy-mobile.yml", ".github/workflows/deploy-mobile.yml",
    "Step 70 - Write GitHub Actions deploy workflow (embedded inline)"
)

# Fix 2: Risk manager test — use $150k equity so it passes the $80k strike check
replacements[50] = new_code_cell(source="""\
import sys, os
sys.path.insert(0, os.getcwd())
for m in list(sys.modules.keys()):
    if m in ('config', 'risk_manager'):
        del sys.modules[m]

from risk_manager import RiskManager, Position

rm = RiskManager()
equity = 150_000.0  # $150k account (sufficient for $80k strike)
strike = 80_000.0   # $80k BTC put
btc    = 85_000.0   # current BTC price

# Test contract sizing
contracts = rm.calculate_contracts(equity, strike)
print(f"Contracts (equity=${equity:,.0f}, strike=${strike:,.0f}): {contracts}")
assert contracts >= 0.1, "Should size at least 0.1 contracts"

# Test ladder sizing (splits equity fraction evenly)
ladder_fraction = 0.0828 / 2   # 2-leg ladder
ladder_contracts = rm.calculate_contracts(equity, strike, equity_fraction=ladder_fraction)
print(f"Ladder contracts (half fraction): {ladder_contracts}")

# Test pre-trade with no positions
ok = rm.full_pre_trade_check([], equity, strike, btc)
print(f"Pre-trade (empty): {'PASS' if ok else 'FAIL'}")
assert ok

# Test drawdown checks
assert rm.check_drawdown([50000, 49000, 48000]) == True   # 4% DD < 10% limit
assert rm.check_drawdown([50000, 40000]) == False          # 20% DD > 10% limit
print("Drawdown checks: PASS")

# Test delta breach
pos = Position("BTC-25APR25-80000-P", 80000, "put", 0.01, 85000, 1.0, 0.5, 0.03, 50000)
should_roll, reason = rm.should_roll(pos)
print(f"Delta breach (0.5 > 0.4): roll={should_roll}, reason={reason}")
assert should_roll and reason == "delta_breach"

print("Phase 4 CHECKPOINT PASSED")
""")

# Fix 4: Vite scaffold — check directory existence, not just package.json
replacements[109] = new_code_cell(source="""\
import subprocess, sys, os

# Create the mobile-app with Vite — check directory first to avoid scaffold conflict
if not os.path.isdir("mobile-app"):
    print("Creating Vite + React + TypeScript project...")
    result = subprocess.run(
        ["npm", "create", "vite@latest", "mobile-app", "--", "--template", "react-ts"],
        capture_output=True, text=True, input="y\\n"
    )
    print(result.stdout[-2000:])
    if result.returncode != 0:
        print("Error:", result.stderr[-500:])
else:
    print("mobile-app/ directory already exists - skipping Vite scaffold")
    if os.path.exists("mobile-app/package.json"):
        import json
        pkg = json.load(open("mobile-app/package.json"))
        print(f"Found: {pkg.get('name')} v{pkg.get('version')}")
    else:
        print("(no package.json yet — files will be written by subsequent cells)")
""")

# Fix 5: Cell 74 uses bt/results from cell 72 — make it self-contained
replacements[74] = new_code_cell(source="""\
import sys, os
sys.path.insert(0, os.getcwd())
for m in list(sys.modules.keys()):
    if any(m.startswith(k) for k in ('config', 'deribit', 'strategy', 'risk', 'backtester')):
        del sys.modules[m]

from config import load_config
from backtester import Backtester

cfg = load_config()
bt = Backtester(cfg)
print("Re-running backtest to produce summary/CSV/chart outputs...")
results = bt.run()

bt.print_summary(results)
bt.save_csv(results)
bt.save_plot(results)
print(f"Results saved to: {cfg.backtest.results_csv}")
print(f"Chart saved to: {cfg.backtest.results_image}")
""")

# ── Assemble the final notebook ───────────────────────────────────────────────

print("\nAssembling new notebook...")

for i, cell in enumerate(orig_nb.cells):
    if i in replacements:
        new_cell = replacements[i]
        cells.append(new_cell)
        print(f"  Cell {i:3d}: REPLACED (shutil → embedded)")
    else:
        cells.append(cell)
        flag = "[MD]" if cell.cell_type == "markdown" else "[CODE]"
        print(f"  Cell {i:3d}: KEPT {flag}")

# ── Create the new notebook ───────────────────────────────────────────────────

nb = new_notebook(cells=cells)
nb.metadata = orig_nb.metadata  # preserve kernel, language info etc.

out_path = BASE / "btc_wheel_bot_reconstruction_guide.ipynb"
nbformat.write(nb, str(out_path))

print(f"\nNotebook written: {out_path}")

# ── Verify ────────────────────────────────────────────────────────────────────

nb_check = nbformat.read(str(out_path), as_version=4)
shutil_remaining = [i for i, c in enumerate(nb_check.cells) if "shutil.copy" in c.source]
total_code = sum(1 for c in nb_check.cells if c.cell_type == "code")
total_md   = sum(1 for c in nb_check.cells if c.cell_type == "markdown")
size_mb    = out_path.stat().st_size / 1024 / 1024

print(f"\n{'='*60}")
print(f"VERIFICATION RESULTS")
print(f"{'='*60}")
print(f"  Total cells:          {len(nb_check.cells)} ({total_code} code, {total_md} markdown)")
print(f"  shutil.copy remaining: {len(shutil_remaining)}")
print(f"  Notebook size:        {size_mb:.1f} MB")

if shutil_remaining:
    print(f"\n  WARNING: shutil.copy still present in cells: {shutil_remaining}")
    for ci in shutil_remaining:
        print(f"    Cell {ci}: {nb_check.cells[ci].source[:80]}")
else:
    print(f"\n  All shutil.copy references eliminated.")
    print(f"  Notebook is fully self-contained.")
