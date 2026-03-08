#!/usr/bin/env python3
"""
Writes the .env file to your topstepx-agent directory.

Usage:
  python3 update_env.py                     # uses env_values.json in same dir
  python3 update_env.py /path/to/folder     # target a specific folder
"""
import json
import os
import shutil
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
VALUES_FILE = SCRIPT_DIR / "env_values.json"

TEMPLATE = """\
# ── AI API Keys ───────────────────────────────────────────────────────────────
GROQ_API_KEY={GROQ_API_KEY}
OPENAI_API_KEY={OPENAI_API_KEY}

# ── TradersPost Webhooks ─────────────────────────────────────────────────────
TRADERSPOST_WEBHOOK={TRADERSPOST_WEBHOOK}

# ── ProjectX / TopStepX Credentials ──────────────────────────────────────────
PROJECTX_USERNAME={PROJECTX_USERNAME}
PROJECTX_PASSWORD={PROJECTX_PASSWORD}
PROJECTX_API_KEY={PROJECTX_API_KEY}
PROJECTX_ACCOUNT_ID={PROJECTX_ACCOUNT_ID}

# ── TopStepX Account ─────────────────────────────────────────────────────────
TOPSTEPX_USERNAME={TOPSTEPX_USERNAME}
TOPSTEPX_PASSWORD={TOPSTEPX_PASSWORD}

# ── Local Ollama Configuration ───────────────────────────────────────────────
OLLAMA_HOST={OLLAMA_HOST}
OLLAMA_MODEL={OLLAMA_MODEL}

# ── Database ─────────────────────────────────────────────────────────────────
DATABASE_PATH={DATABASE_PATH}
"""

SEARCH_DIRS = [
    Path.home() / "Documents" / "topstepx-agent",
    Path.home() / "topstepx-agent",
    Path.home() / "Desktop" / "topstepx-agent",
    Path.home() / "Downloads" / "topstepx-agent",
    Path.cwd(),
]


def find_target_dir():
    for d in SEARCH_DIRS:
        if d.is_dir():
            return d
    return None


def load_values():
    if not VALUES_FILE.exists():
        print(f"ERROR: {VALUES_FILE} not found.")
        print()
        print("Create it with your credentials, e.g.:")
        print(f'  cp env_values.example.json env_values.json')
        print("  # then fill in your actual keys")
        sys.exit(1)

    with open(VALUES_FILE) as f:
        return json.load(f)


def main():
    if len(sys.argv) > 1:
        target_dir = Path(sys.argv[1])
        if not target_dir.is_dir():
            sys.exit(f"Directory not found: {target_dir}")
    else:
        target_dir = find_target_dir()

    if target_dir is None:
        print("Could not auto-detect your topstepx-agent folder.")
        print("Searched:")
        for d in SEARCH_DIRS:
            print(f"  {d}")
        print()
        custom = input("Enter the full path to your topstepx-agent folder: ").strip()
        if not custom:
            sys.exit("Aborted.")
        target_dir = Path(custom)
        if not target_dir.is_dir():
            sys.exit(f"Directory not found: {target_dir}")

    values = load_values()
    env_content = TEMPLATE.format(**values)
    env_path = target_dir / ".env"

    if env_path.exists():
        backup = target_dir / ".env.backup"
        shutil.copy2(env_path, backup)
        print(f"Backed up existing .env -> {backup}")

    env_path.write_text(env_content)
    print(f"Wrote .env to {env_path}")
    print()

    print("Variables set:")
    for line in env_content.splitlines():
        if line and not line.startswith("#") and "=" in line:
            key, _, val = line.partition("=")
            masked = val[:4] + "****" if len(val) > 8 else val
            print(f"  {key} = {masked}")

    print()
    print("Done.")


if __name__ == "__main__":
    main()
