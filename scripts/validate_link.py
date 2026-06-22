#!/usr/bin/env python3
"""Validate link.json against schema.json plus a few cross-field invariants.

Run locally:  python3 scripts/validate_link.py
CI runs this on every push / pull request.
"""

from __future__ import annotations

import copy
import importlib.util
import json
import sys
from pathlib import Path

try:
    from jsonschema import Draft202012Validator
except ImportError:
    print("jsonschema is required: pip install jsonschema", file=sys.stderr)
    raise


def _load_updater(root: Path):
    spec = importlib.util.spec_from_file_location("updater", root / "scripts" / "update_domain_rating.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main() -> int:
    root = Path(__file__).resolve().parent.parent
    data = json.loads((root / "link.json").read_text(encoding="utf-8"))
    schema = json.loads((root / "schema.json").read_text(encoding="utf-8"))

    errors: list[str] = []

    # format_checker so `format: "uri"` is actually enforced.
    validator = Draft202012Validator(schema, format_checker=Draft202012Validator.FORMAT_CHECKER)
    for err in sorted(validator.iter_errors(data), key=lambda e: list(e.path)):
        location = "/".join(str(p) for p in err.path) or "(root)"
        errors.append(f"schema: {location}: {err.message}")

    footer = data.get("footer_navigation_sites", [])
    authority = data.get("authority_documentation_sites", [])

    # Compatibility mirrors must be byte-for-byte what the updater would
    # regenerate from the semantic collections — catches stale field VALUES,
    # not just id drift.
    updater = _load_updater(root)
    expected = copy.deepcopy(data)
    updater.sync_compatibility_arrays(expected)
    for key in ("badges", "friend_links", "all_friend_links"):
        if data.get(key) != expected.get(key):
            errors.append(f"parity: {key} is out of sync with the semantic collections (re-run the updater)")

    # No third-party DR branding should ever be committed to the data.
    raw = json.dumps(data, ensure_ascii=False).lower()
    if "ahrefs" in raw:
        errors.append("policy: 'ahrefs' must not appear anywhere in link.json")

    # Every semantic entry needs both base languages of its description.
    for entry in footer + authority:
        i18n = entry.get("description_i18n") or {}
        if not i18n.get("en-US") or not i18n.get("zh-CN"):
            errors.append(f"i18n: {entry.get('id')} missing description_i18n en-US/zh-CN")

    if errors:
        print(f"link.json validation FAILED ({len(errors)} issue(s)):")
        for e in errors:
            print(f"  - {e}")
        return 1

    total = len(data.get("all_friend_links", []))
    print(f"link.json valid: {len(footer)} footer + {len(authority)} authority = {total} entries.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
