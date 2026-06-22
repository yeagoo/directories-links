# Directories Links

This repository stores structured friend-link data for directory sites and authoritative documentation/resource sites.

## Files

- `link.json`: Main data source.
- `schema.json`: JSON Schema for `link.json` (consumers can validate against it).
- `assets/logos/`: Local SVG logos for footer navigation sites.
- `scripts/update_domain_rating.py`: Updates Domain Rating (DR) values.
- `scripts/validate_link.py`: Validates `link.json` against `schema.json` plus cross-field invariants.
- `.github/workflows/update-domain-rating.yml`: Daily GitHub Actions workflow that refreshes DR for 6 sites per run.
- `.github/workflows/validate.yml`: Validates `link.json` on every push / pull request.
- `preview.html`: Local preview page for checking how both link groups render.

## Data Groups

`footer_navigation_sites` contains navigation/directory partners. These links are intended for the site footer.

`authority_documentation_sites` contains authoritative documentation and resource sites. These links are intended for the dedicated friend-links page.

`all_friend_links` combines both groups for the dedicated friend-links page, so the inner page includes navigation sites and documentation/resource sites together.

`badges` and `friend_links` are compatibility mirrors for older renderers. New code should prefer the semantic fields above.

## Rendering Rules

- Footer navigation sites should be rendered in the footer.
- The footer can choose either `logo_only` or `logo_with_name` from `footer_display_modes`, depending on available space.
- The dedicated friend-links page should render `all_friend_links`.
- All outbound links are dofollow links. Do not add `nofollow`, `ugc`, or `sponsored`.
- Use `logo_svg` for local logo rendering and keep `logo_source_url` as the original upstream source.
- Hide entries with `status: "archived"`; you may de-emphasize `pending_dns` / `unreachable`.

## Localized descriptions

Each entry carries `description_i18n` with at least `en-US` and `zh-CN`. Render
`description_i18n[locale]` and fall back to `en-US` (or the legacy `description`)
when a locale is missing.

## Domain Rating

`dr` is a Domain Rating (domain authority) number maintained automatically by
`scripts/update_domain_rating.py`; treat it as opaque data and read it straight
from the `dr` field. Per-entry bookkeeping (`dr_status`, `dr_last_checked_at`,
`dr_consecutive_errors`, …) lets a renderer show freshness if desired.

The daily workflow refreshes 6 sites per run in JSON order. Failed updates are
retried first with exponential backoff (capped at `retry_backoff_max_days`);
after `unreachable_after` (5) consecutive failures a site is auto-flagged
`status: "unreachable"` and restored to `active` on the next success. Failures
surface as GitHub Actions warnings + a job summary.

Manual dry run:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 scripts/update_domain_rating.py --file link.json --limit 6 --dry-run
```

Update all sites locally:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 scripts/update_domain_rating.py --file link.json --all
```

## Validation

```bash
pip install jsonschema
python3 scripts/validate_link.py
```

This validates `link.json` against `schema.json` and checks: compatibility
mirrors stay in sync, every entry has `description_i18n` (en-US + zh-CN), URLs
are http(s), `logo_svg` paths are safe, and no third-party DR branding leaks
into the data.

## Preview

Run a local static server:

```bash
python3 -m http.server 4173 --bind 127.0.0.1
```

Open:

```text
http://127.0.0.1:4173/preview.html
```
