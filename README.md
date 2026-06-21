# Directories Links

This repository stores structured friend-link data for directory sites and authoritative documentation/resource sites.

## Files

- `link.json`: Main data source.
- `assets/logos/`: Local SVG logos for footer navigation sites.
- `scripts/update_domain_rating.py`: Updates Ahrefs Domain Rating values.
- `.github/workflows/update-domain-rating.yml`: Daily GitHub Actions workflow that updates 3 sites per run.
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

## Domain Rating

DR means Ahrefs Domain Rating.

The updater uses the free public Ahrefs endpoint:

```text
https://api.ahrefs.com/v3/public/domain-rating-free?target={domain}&output=json
```

API docs:

```text
https://docs.ahrefs.com/en/api/reference/public/get-domain-rating-free
```

Attribution required when showing DR:

```text
Domain Rating by Ahrefs
```

The daily workflow updates 3 sites per run in JSON order. Failed DR updates are retried first with backoff, then the normal rotation continues.

Manual dry run:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 scripts/update_domain_rating.py --file link.json --limit 3 --dry-run
```

Update all sites locally:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 scripts/update_domain_rating.py --file link.json --all
```

## Preview

Run a local static server:

```bash
python3 -m http.server 4173 --bind 127.0.0.1
```

Open:

```text
http://127.0.0.1:4173/preview.html
```

## Validation

Basic checks:

```bash
python3 -m json.tool link.json >/dev/null
PYTHONDONTWRITEBYTECODE=1 python3 -m py_compile scripts/update_domain_rating.py
```
