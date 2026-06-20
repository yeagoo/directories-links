#!/usr/bin/env python3
"""Update Domain Rating values in link.json.

The Ahrefs public endpoint is free and does not require an API key. This script
updates the semantic collections first, then refreshes the legacy compatibility
arrays so older renderers keep working.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


COLLECTION_KEYS = ("footer_navigation_sites", "authority_documentation_sites")
API_ENDPOINT = "https://api.ahrefs.com/v3/public/domain-rating-free"
API_DOCS = "https://docs.ahrefs.com/en/api/reference/public/get-domain-rating-free"
LICENSE_URL = "http://ahrefs.com/legal/domain-rating-license"
DR_SOURCE = "Ahrefs Domain Rating"
DR_ATTRIBUTION = "Domain Rating by Ahrefs"
DEFAULT_DAILY_LIMIT = 3
MAX_RETRY_BACKOFF_DAYS = 7
Target = tuple[str, int, dict[str, Any]]
SelectedTarget = tuple[int, str, int, dict[str, Any]]
FETCH_ERRORS = (
    urllib.error.URLError,
    TimeoutError,
    ValueError,
    json.JSONDecodeError,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Update Ahrefs Domain Rating values in link.json.",
    )
    parser.add_argument("--file", default="link.json", help="Path to link.json.")
    parser.add_argument(
        "--limit",
        type=int,
        default=DEFAULT_DAILY_LIMIT,
        help="Number of sites to update.",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Update every enabled site and reset the cursor.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Fetch values but do not write changes.",
    )
    parser.add_argument(
        "--sleep",
        type=float,
        default=0.4,
        help="Delay between API requests in seconds.",
    )
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
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def normalize_rating(value: Any) -> int | float:
    rating = float(value)
    return int(rating) if rating.is_integer() else round(rating, 1)


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


def fetch_domain_rating(domain: str) -> tuple[int | float, str | None]:
    query = urllib.parse.urlencode({"target": domain, "output": "json"})
    request = urllib.request.Request(
        f"{API_ENDPOINT}?{query}",
        headers={
            "Accept": "application/json",
            "User-Agent": "github-actions-link-dr-updater/1.0",
        },
    )

    with urllib.request.urlopen(request, timeout=30) as response:
        payload = json.loads(response.read().decode("utf-8"))

    domain_rating = payload.get("domain_rating")
    if not isinstance(domain_rating, dict) or "domain_rating" not in domain_rating:
        raise ValueError(f"Unexpected Ahrefs response for {domain}: {payload!r}")

    return normalize_rating(domain_rating["domain_rating"]), domain_rating.get("license")


def update_item_success(
    item: dict[str, Any],
    rating: int | float,
    license_url: str | None,
    checked_at: str,
) -> None:
    item["dr"] = rating
    item["dr_source"] = DR_SOURCE
    item["dr_source_url"] = API_DOCS
    item["dr_api_endpoint"] = API_ENDPOINT
    item["dr_license_url"] = license_url or item.get("dr_license_url") or LICENSE_URL
    item["dr_attribution"] = DR_ATTRIBUTION
    item["dr_last_checked_at"] = checked_at
    item["dr_update_enabled"] = item.get("dr_update_enabled", True)
    item["dr_status"] = "ok"
    item["dr_consecutive_errors"] = 0
    item.pop("dr_error", None)
    item.pop("dr_last_error_at", None)
    item.pop("dr_retry_after", None)


def update_item_error(item: dict[str, Any], error: Exception, checked_at: str) -> None:
    consecutive_errors = int(item.get("dr_consecutive_errors", 0) or 0) + 1
    item["dr_source"] = DR_SOURCE
    item["dr_source_url"] = API_DOCS
    item["dr_api_endpoint"] = API_ENDPOINT
    item["dr_attribution"] = DR_ATTRIBUTION
    item["dr_last_checked_at"] = checked_at
    item["dr_update_enabled"] = item.get("dr_update_enabled", True)
    item["dr_status"] = "error"
    item["dr_error"] = str(error)[:300]
    item["dr_last_error_at"] = checked_at
    item["dr_consecutive_errors"] = consecutive_errors
    item["dr_retry_after"] = retry_after_date(consecutive_errors, checked_at)


def compatibility_badge(item: dict[str, Any]) -> dict[str, Any]:
    result = {
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
        "status": item.get("status"),
        "link_rel": item.get("link_rel"),
        "dofollow": item.get("dofollow"),
        "footer_display_modes": item.get("footer_display_modes"),
        "default_footer_display_mode": item.get("default_footer_display_mode"),
        "links_page_included": item.get("links_page_included"),
        "links_page_category": item.get("links_page_category"),
        "dr": item.get("dr"),
        "dr_source": item.get("dr_source"),
        "dr_source_url": item.get("dr_source_url"),
        "dr_api_endpoint": item.get("dr_api_endpoint"),
        "dr_license_url": item.get("dr_license_url"),
        "dr_attribution": item.get("dr_attribution"),
        "dr_last_checked_at": item.get("dr_last_checked_at"),
        "dr_update_enabled": item.get("dr_update_enabled"),
        "dr_status": item.get("dr_status"),
        "dr_consecutive_errors": item.get("dr_consecutive_errors"),
        "dr_last_error_at": item.get("dr_last_error_at"),
        "dr_retry_after": item.get("dr_retry_after"),
    }
    if item.get("dr_error"):
        result["dr_error"] = item.get("dr_error")
    return result


def compatibility_friend(item: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "id",
        "name",
        "domain",
        "url",
        "category",
        "placement",
        "description",
        "status",
        "link_rel",
        "dofollow",
        "dns_target",
        "status_note",
        "links_page_included",
        "dr",
        "dr_source",
        "dr_source_url",
        "dr_api_endpoint",
        "dr_license_url",
        "dr_attribution",
        "dr_last_checked_at",
        "dr_update_enabled",
        "dr_status",
        "dr_error",
        "dr_consecutive_errors",
        "dr_last_error_at",
        "dr_retry_after",
    )
    return {key: item.get(key) for key in keys if key in item}


def all_friend_link_entry(
    item: dict[str, Any],
    source_collection: str,
    placement: str,
) -> dict[str, Any]:
    result = dict(item)
    result["source_collection"] = source_collection
    result["placement"] = placement
    return result


def sync_compatibility_arrays(data: dict[str, Any]) -> None:
    data["badges"] = [compatibility_badge(item) for item in data.get("footer_navigation_sites", [])]
    data["friend_links"] = [
        compatibility_friend(item) for item in data.get("authority_documentation_sites", [])
    ]
    data["all_friend_links"] = [
        all_friend_link_entry(item, "footer_navigation_sites", "footer_and_links_page")
        for item in data.get("footer_navigation_sites", [])
    ]
    data["all_friend_links"].extend(
        all_friend_link_entry(item, "authority_documentation_sites", "links_page")
        for item in data.get("authority_documentation_sites", [])
    )


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
    start_index = int(meta.get("next_index", 0) or 0)

    if args.all:
        selected = [
            (target_index, collection_key, item_index, item)
            for target_index, (collection_key, item_index, item) in enumerate(targets)
        ]
        next_index = 0
    else:
        selected, next_index = select_targets(
            targets,
            start_index,
            args.limit,
            current_date(),
        )

    checked_at = current_date()
    updated_ids: list[str] = []

    for sequence, (_, collection_key, item_index, item) in enumerate(selected, start=1):
        domain = item["domain"]
        label = f"{item.get('id', domain)} ({domain})"
        try:
            rating, license_url = fetch_domain_rating(domain)
            update_item_success(item, rating, license_url, checked_at)
            message = (
                f"[{sequence}/{len(selected)}] "
                f"{collection_key}[{item_index}] {label}: DR {rating}"
            )
            print(message)
        except FETCH_ERRORS as error:
            update_item_error(item, error, checked_at)
            message = (
                f"[{sequence}/{len(selected)}] "
                f"{collection_key}[{item_index}] {label}: ERROR {error}"
            )
            print(message)
        updated_ids.append(str(item.get("id", domain)))
        if sequence < len(selected) and args.sleep > 0:
            time.sleep(args.sleep)

    meta["source"] = DR_SOURCE
    meta["api_docs"] = API_DOCS
    meta["api_endpoint"] = API_ENDPOINT
    meta["attribution"] = DR_ATTRIBUTION
    meta["daily_limit"] = args.limit
    meta["retry_failed_first"] = True
    meta["retry_backoff_max_days"] = MAX_RETRY_BACKOFF_DAYS
    meta["rotation_scope"] = list(COLLECTION_KEYS)
    meta["next_index"] = next_index
    meta["last_run_at"] = current_timestamp()
    meta["last_updated_ids"] = updated_ids

    sync_compatibility_arrays(data)

    if args.dry_run:
        print("Dry run complete; no files written.")
        return 0

    save_json(path, data)
    return 0


if __name__ == "__main__":
    sys.exit(main())
