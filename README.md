# ICML 2026 Paper Explorer

Static explorer for ICML 2026 accepted papers.

Open locally:

- `output/icml2026_explorer.html`

## Snapshot

Generated from the official ICML virtual-site JSON snapshot:

- `https://icml.cc/static/virtual/data/icml-2026-orals-posters.json`
- `https://icml.cc/static/virtual/data/icml-2026-abstracts.json`

The builder uses poster rows as paper rows and promotes papers with a matching oral event row into the `Oral` tier.

| Item | Count |
|---|---:|
| Papers | 6,634 |
| Oral | 168 |
| Spotlight | 406 |
| Regular | 5,999 |
| Other poster rows | 61 |
| Unique authors | 25,946 |
| Unique institutions | 4,777 |

The builder fetches the current public snapshot, normalizes papers, merges abstracts, adds lightweight fallback topic and keyword tags, and emits a self-contained HTML file.

## Features

- Search title, authors, institutions, abstract, topic, keyword, session, and ICML event id
- Filter by decision tier and inferred topic
- Sort by title, decision tier, author count, or paper number
- Summary cards, tier chart, topic chart, keyword chips, and author-count histogram
- Expand paper cards for abstract and direct OpenReview, poster, and oral links
- Share filtered states through URL query parameters
- Download the filtered result set as JSON

## Data Sources

- Official ICML virtual paper data: https://icml.cc/static/virtual/data/icml-2026-orals-posters.json
- Official ICML abstract data: https://icml.cc/static/virtual/data/icml-2026-abstracts.json
- Official ICML papers page: https://icml.cc/virtual/2026/papers.html
- Official ICML 2026 page: https://icml.cc/Conferences/2026
- Official OpenReview venue metadata: `ICML.cc/2026/Conference`

## Rebuild

```powershell
python scripts/build_icml2026_static.py
```

The generated HTML embeds the normalized JSON, so it can be hosted on GitHub Pages or opened directly from disk.
