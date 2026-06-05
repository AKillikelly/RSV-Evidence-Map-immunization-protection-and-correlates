# RSV Evidence Map

This repository hosts a public RSV evidence map using GitHub Pages.

## Architecture

The site is intentionally simple:

- `index.html` is the dashboard shell.
- `data/rsv_evidence_map_data_v0_2.json` is the source of truth for map records.
- `data/rsv_evidence_map_records_v0_2.csv` is the tabular export.
- `scripts/update_sources.py` performs weekly automated surveillance.
- `scripts/validate_site.py` prevents deployment if the dashboard or data are broken.
- `.github/workflows/weekly-surveillance.yml` runs the update and deploys the site.

The workflow searches PubMed, medRxiv, ClinicalTrials.gov, Crossref, and selected official guidance/regulatory pages. Records may be automatically added and are not manually verified.

## GitHub Pages setup

Use:

- Settings → Pages → Source: GitHub Actions
- Settings → Actions → General → Workflow permissions: Read and write permissions

Then run the workflow manually from Actions.
