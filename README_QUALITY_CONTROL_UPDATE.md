# RSV evidence-map quality-control update

This package updates the dashboard and automated ingestion pipeline without replacing the current evidence-data files.

## Files to replace

```text
index.html
scripts/update_sources.py
scripts/validate_site.py
.github/workflows/weekly-surveillance.yml
methods/search_strategy.md
```

Do not upload old JSON or CSV data files from another package. The first workflow run will audit and clean the data already in your repository.

## What the first run does

1. Preserves the 48 curated seed records.
2. Rechecks existing auto-added PubMed records against current PubMed metadata.
3. Quarantines off-topic resveratrol-abbreviation records.
4. Enriches PMID records with DOI, MeSH and PublicationType metadata.
5. Reclassifies PubMed study design from PublicationType metadata.
6. De-duplicates active records using PMID, DOI, NCT, URL and normalized title.
7. Quarantines future-dated records.
8. Writes a quality-control report and quarantine log.
9. Validates and deploys the dashboard.

## Optional repository variables

These are optional but useful:

```text
NCBI_EMAIL       your contact email for NCBI E-utilities
CROSSREF_MAILTO  your contact email for Crossref
```

An optional `NCBI_API_KEY` can be added as an Actions secret.

## Expected new output files

```text
updates/quality_quarantine.json
updates/quality_control_report.json
```

The quarantine file is an audit trail. Quarantined records are excluded from the active matrix but are not deleted without trace.
