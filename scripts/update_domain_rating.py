#!/usr/bin/env python3
"""Update Domain Rating (DR) values in link.json.

DR is fetched from a free public Domain Rating endpoint (no API key required).
Only the DR value and bookkeeping fields are written to link.json — no
third-party branding, attribution or license text is stored in the data.

The script updates the semantic collections first, then refreshes the legacy
compatibility arrays so older renderers keep working.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


COLLECTION_KEYS = ("footer_navigation_sites", "authority_documentation_sites")
# Free, key-less public Domain Rating endpoint used only to fetch the number.
DR_ENDPOINT = "https://api.ahrefs.com/v3/public/domain-rating-free"
DEFAULT_DAILY_LIMIT = 6
MAX_RETRY_BACKOFF_DAYS = 7
# After this many consecutive failures a site is auto-flagged status=unreachable
# (and restored to active on the next successful fetch).
UNREACHABLE_AFTER = 5
# Legacy third-party DR metadata that must never be written back into the data.
LEGACY_DR_FIELDS = (
    "dr_source",
    "dr_source_url",
    "dr_api_endpoint",
    "dr_license_url",
    "dr_attribution",
)
Target = tuple[str, int, dict[str, Any]]
SelectedTarget = tuple[int, str, int, dict[str, Any]]
FETCH_ERRORS = (
    urllib.error.URLError,
    TimeoutError,
    ValueError,
    json.JSONDecodeError,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Update Domain Rating values in link.json.")
    parser.add_argument("--file", default="link.json", help="Path to link.json.")
    parser.add_argument("--limit", type=int, default=DEFAULT_DAILY_LIMIT, help="Number of sites to update.")
    parser.add_argument("--all", action="store_true", help="Update every enabled site and reset the cursor.")
    parser.add_argument("--dry-run", action="store_true", help="Fetch values but do not write changes.")
    parser.add_argument("--sleep", type=float, default=0.4, help="Delay between requests in seconds.")
    return parser.parse_args()


def load_json(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as file:
        return json.load(file)


def save_json(path: Path, data: dict[str, Any]) -> None:
    with path.open("w", encoding="utf-8") as file:
        json.dump(data, file, ensure_ascii=False, indent=2)
        file.write("\n")


def current_date() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def current_timestamp() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def normalize_rating(value: Any) -> int | float:
    rating = float(value)
    return int(rating) if rating.is_integer() else round(rating, 1)


def strip_legacy_fields(item: dict[str, Any]) -> None:
    for field in LEGACY_DR_FIELDS:
        item.pop(field, None)


def should_retry_error(item: dict[str, Any], today: str) -> bool:
    if item.get("dr_status") != "error":
        return False
    retry_after = item.get("dr_retry_after")
    return not retry_after or str(retry_after) <= today


def retry_after_date(consecutive_errors: int, today: str) -> str:
    base_date = datetime.fromisoformat(today).date()
    delay_days = min(2 ** max(consecutive_errors - 1, 0), MAX_RETRY_BACKOFF_DAYS)
    return base_date.fromordinal(base_date.toordinal() + delay_days).isoformat()


def enabled_targets(data: dict[str, Any]) -> list[Target]:
    targets = []
    for collection_key in COLLECTION_KEYS:
        for index, item in enumerate(data.get(collection_key, [])):
            if item.get("dr_update_enabled") is False:
                continue
            if not item.get("domain"):
                continue
            targets.append((collection_key, index, item))
    return targets


def select_targets(
    targets: list[Target],
    start_index: int,
    limit: int,
    today: str,
) -> tuple[list[SelectedTarget], int]:
    if not targets:
        return [], 0

    selected = []
    selected_indices: set[int] = set()
    target_count = len(targets)
    count = min(limit, target_count)

    for target_index, (collection_key, item_index, item) in enumerate(targets):
        if not should_retry_error(item, today):
            continue
        selected.append((target_index, collection_key, item_index, item))
        selected_indices.add(target_index)
        if len(selected) == count:
            return selected, start_index % target_count

    cursor = start_index % len(targets)
    scanned = 0

    while len(selected) < count and scanned < target_count:
        target_index = (cursor + scanned) % target_count
        scanned += 1
        if target_index in selected_indices:
            continue
        collection_key, item_index, item = targets[target_index]
        selected_indices.add(target_index)
        selected.append((target_index, collection_key, item_index, item))

    next_index = (cursor + scanned) % target_count
    return selected, next_index


def fetch_domain_rating(domain: str) -> int | float:
    query = urllib.parse.urlencode({"target": domain, "output": "json"})
    request = urllib.request.Request(
        f"{DR_ENDPOINT}?{query}",
        headers={
            "Accept": "application/json",
            "User-Agent": "github-actions-link-dr-updater/1.0",
        },
    )

    with urllib.request.urlopen(request, timeout=30) as response:
        payload = json.loads(response.read().decode("utf-8"))

    domain_rating = payload.get("domain_rating")
    if not isinstance(domain_rating, dict) or "domain_rating" not in domain_rating:
        raise ValueError(f"Unexpected DR response for {domain}: {payload!r}")

    return normalize_rating(domain_rating["domain_rating"])


def update_item_success(item: dict[str, Any], rating: int | float, checked_at: str) -> None:
    strip_legacy_fields(item)
    item["dr"] = rating
    item["dr_last_checked_at"] = checked_at
    item["dr_update_enabled"] = item.get("dr_update_enabled", True)
    item["dr_status"] = "ok"
    item["dr_consecutive_errors"] = 0
    # Recover a site that had been auto-flagged unreachable.
    if item.get("status") == "unreachable":
        item["status"] = "active"
    item.pop("dr_error", None)
    item.pop("dr_last_error_at", None)
    item.pop("dr_retry_after", None)


def update_item_error(item: dict[str, Any], error: Exception, checked_at: str) -> None:
    strip_legacy_fields(item)
    consecutive_errors = int(item.get("dr_consecutive_errors", 0) or 0) + 1
    item["dr_last_checked_at"] = checked_at
    item["dr_update_enabled"] = item.get("dr_update_enabled", True)
    item["dr_status"] = "error"
    item["dr_error"] = str(error)[:300]
    item["dr_last_error_at"] = checked_at
    item["dr_consecutive_errors"] = consecutive_errors
    item["dr_retry_after"] = retry_after_date(consecutive_errors, checked_at)
    # Auto-flag persistently failing sites so the front end can de-emphasize them.
    if consecutive_errors >= UNREACHABLE_AFTER and item.get("status") == "active":
        item["status"] = "unreachable"


def compatibility_badge(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": item.get("id"),
        "name": item.get("name"),
        "domain": item.get("domain"),
        "image": item.get("logo_svg"),
        "logo_svg": item.get("logo_svg"),
        "logo_source_url": item.get("logo_source_url"),
        "url": item.get("url"),
        "category": item.get("category"),
        "placement": item.get("placement"),
        "description": item.get("description"),
        "description_i18n": item.get("description_i18n"),
        "status": item.get("status"),
        "link_rel": item.get("link_rel"),
        "dofollow": item.get("dofollow"),
        "footer_display_modes": item.get("footer_display_modes"),
        "default_footer_display_mode": item.get("default_footer_display_mode"),
        "links_page_included": item.get("links_page_included"),
        "links_page_category": item.get("links_page_category"),
        "dr": item.get("dr"),
        "dr_last_checked_at": item.get("dr_last_checked_at"),
        "dr_update_enabled": item.get("dr_update_enabled"),
        "dr_status": item.get("dr_status"),
        "dr_consecutive_errors": item.get("dr_consecutive_errors"),
        "dr_last_error_at": item.get("dr_last_error_at"),
        "dr_retry_after": item.get("dr_retry_after"),
        **({"dr_error": item["dr_error"]} if item.get("dr_error") else {}),
    }


def compatibility_friend(item: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "id",
        "name",
        "domain",
        "url",
        "category",
        "placement",
        "description",
        "description_i18n",
        "status",
        "link_rel",
        "dofollow",
        "dns_target",
        "status_note",
        "links_page_included",
        "dr",
        "dr_last_checked_at",
        "dr_update_enabled",
        "dr_status",
        "dr_error",
        "dr_consecutive_errors",
        "dr_last_error_at",
        "dr_retry_after",
    )
    return {key: item.get(key) for key in keys if key in item}


def all_friend_link_entry(item: dict[str, Any], source_collection: str, placement: str) -> dict[str, Any]:
    result = dict(item)
    result["source_collection"] = source_collection
    result["placement"] = placement
    return result


def sync_compatibility_arrays(data: dict[str, Any]) -> None:
    footer = data.get("footer_navigation_sites", [])
    authority = data.get("authority_documentation_sites", [])
    data["badges"] = [compatibility_badge(item) for item in footer]
    data["friend_links"] = [compatibility_friend(item) for item in authority]
    data["all_friend_links"] = [
        all_friend_link_entry(item, "footer_navigation_sites", "footer_and_links_page") for item in footer
    ]
    data["all_friend_links"].extend(
        all_friend_link_entry(item, "authority_documentation_sites", "links_page") for item in authority
    )


def report_health(data: dict[str, Any]) -> None:
    """Emit GitHub Actions annotations + a step summary for failing sites."""
    errors = []
    unreachable = []
    for key in COLLECTION_KEYS:
        for item in data.get(key, []):
            if item.get("dr_status") == "error":
                errors.append(item)
            if item.get("status") == "unreachable":
                unreachable.append(item)

    for item in errors:
        ident = item.get("id") or item.get("domain")
        n = item.get("dr_consecutive_errors", 0)
        print(f"::warning title=DR update failing::{ident} has failed {n} time(s): {item.get('dr_error', '')}")
    for item in unreachable:
        ident = item.get("id") or item.get("domain")
        print(f"::warning title=Site unreachable::{ident} auto-flagged status=unreachable")

    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        with open(summary_path, "a", encoding="utf-8") as handle:
            handle.write("### Domain Rating update\n")
            handle.write(f"- Sites with DR errors: **{len(errors)}**\n")
            handle.write(f"- Sites flagged unreachable: **{len(unreachable)}**\n")
            if unreachable:
                handle.write("  - " + ", ".join(i.get("id") or i.get("domain") for i in unreachable) + "\n")


def main() -> int:
    args = parse_args()
    if args.limit < 1:
        raise SystemExit("--limit must be at least 1")

    path = Path(args.file)
    data = load_json(path)
    targets = enabled_targets(data)
    if not targets:
        print("No DR-enabled targets found.")
        return 0

    meta = data.setdefault("_dr_update_meta", {})
    # Drop any legacy third-party metadata so it never reappears in the data.
    for legacy in ("source", "api_docs", "api_endpoint", "attribution", "license_url"):
        meta.pop(legacy, None)
    start_index = int(meta.get("next_index", 0) or 0)

    if args.all:
        selected = [
            (target_index, collection_key, item_index, item)
            for target_index, (collection_key, item_index, item) in enumerate(targets)
        ]
        next_index = 0
    else:
        selected, next_index = select_targets(targets, start_index, args.limit, current_date())

    checked_at = current_date()
    updated_ids: list[str] = []

    for sequence, (_, collection_key, item_index, item) in enumerate(selected, start=1):
        domain = item["domain"]
        label = f"{item.get('id', domain)} ({domain})"
        try:
            rating = fetch_domain_rating(domain)
            update_item_success(item, rating, checked_at)
            print(f"[{sequence}/{len(selected)}] {collection_key}[{item_index}] {label}: DR {rating}")
        except FETCH_ERRORS as error:
            update_item_error(item, error, checked_at)
            print(f"[{sequence}/{len(selected)}] {collection_key}[{item_index}] {label}: ERROR {error}")
        updated_ids.append(str(item.get("id", domain)))
        if sequence < len(selected) and args.sleep > 0:
            time.sleep(args.sleep)

    meta["daily_limit"] = args.limit
    meta["retry_failed_first"] = True
    meta["retry_backoff_max_days"] = MAX_RETRY_BACKOFF_DAYS
    meta["unreachable_after"] = UNREACHABLE_AFTER
    meta["rotation_scope"] = list(COLLECTION_KEYS)
    meta["next_index"] = next_index
    meta["last_run_at"] = current_timestamp()
    meta["last_updated_ids"] = updated_ids

    sync_compatibility_arrays(data)
    report_health(data)

    if args.dry_run:
        print("Dry run complete; no files written.")
        return 0

    save_json(path, data)
    return 0


if __name__ == "__main__":
    sys.exit(main())
