# RSV evidence-map automated surveillance and quality controls

## Scope

This workflow maintains an RSV evidence map focused on immunization, protection, immune correlates, booster studies, safety, policy and implementation. The original 48 curated seed records are preserved and are not automatically reclassified or removed.

The dashboard labels all automatically ingested records as **not manually verified**.

## Quality audit displayed on the dashboard

The visible data-quality statement describes the supplied pre-fix audit snapshot:

- 48 curated seed records: clean
- 288 auto-added records audited
- at least 4 off-topic records
- approximately 31 duplicated records
- more than 20 miscategorized records

These counts quantify the limitations of the pre-fix automated coding. They are retained as an audit snapshot; the current active and quarantined counts are calculated by the workflow after each run.

## Automated sources

- PubMed / NCBI E-utilities
- medRxiv API
- ClinicalTrials.gov API v2
- Crossref REST API
- selected official CDC/ACIP, FDA, WHO, EMA, PHAC/NACI and UKHSA/JCVI pages

## PubMed ingestion gate

A PubMed record is eligible for automatic matrix inclusion only when PubMed metadata contains at least one RSV-specific MeSH heading:

- `Respiratory Syncytial Virus Infections`
- `Respiratory Syncytial Viruses`
- `Respiratory Syncytial Virus, Human`
- `Respiratory Syncytial Virus Vaccines`

The recurring PubMed search uses the following strict RSV component:

```text
("Respiratory Syncytial Virus Infections"[Mesh]
 OR "Respiratory Syncytial Virus, Human"[Mesh]
 OR "Respiratory Syncytial Virus Vaccines"[Mesh])
```

It is combined with prevention, immunization, product, safety, effectiveness, correlates, booster, revaccination, waning and durability terms in titles/abstracts.

Records that cannot be confirmed against an RSV-specific MeSH heading are not admitted to the active matrix. Existing automated PubMed records are rechecked on every run.

## Abbreviation disambiguation

For non-PubMed sources, the workflow requires either:

1. a full RSV/respiratory syncytial virus expression or a named RSV prevention product; or
2. standalone `RSV` together with respiratory, viral, vaccine, immunization, maternal, infant, antibody, prophylaxis or related context.

A record is excluded when its only apparent RSV signal is the abbreviation for **resveratrol**, including contexts such as polyphenol, stilbene, SIRT1, antioxidant, anticancer, cardioprotective or neuroprotective research, without respiratory syncytial virus context.

## Identifier normalization and de-duplication

Before a record reaches the matrix, the workflow normalizes and compares:

- PMID
- DOI
- ClinicalTrials.gov NCT identifier
- canonical source URL
- exact normalized title for sufficiently long titles

DOIs discovered through Crossref are cross-walked to PubMed by searching the DOI as an Article Identifier. If a matching PMID is found, PubMed metadata becomes the preferred record and the DOI and PMID are stored together.

Duplicate precedence is:

1. curated seed record
2. PubMed record
3. medRxiv record
4. ClinicalTrials.gov record
5. official guidance/regulatory record
6. Crossref-only metadata record

The rejected copy is written to `updates/quality_quarantine.json` with the retained record ID and matched identifier(s).

## Study-design validation

For PubMed records, `study_design` and the evidence-type matrix column are derived from PubMed `PublicationType` metadata, supplemented by `Humans` and `Animals` MeSH descriptors. Title words such as “trial” or “randomized” do not independently establish the study design.

Examples:

- `Randomized Controlled Trial`, `Controlled Clinical Trial` or indexed clinical-trial publication types → Human RCT / controlled trial
- `Observational Study`, `Comparative Study`, `Evaluation Study` or `Validation Study` → Observational / real-world
- `Systematic Review`, `Meta-Analysis`, `Review`, `Guideline` or `Practice Guideline` → Evidence synthesis / guidance
- animal-only MeSH indexing → Translational / preclinical, even when the title contains trial-like language
- `Case Reports` → Safety signal / surveillance

When PubMed supplies only a generic article type, the record is not labelled as an RCT; it receives a generic design statement and a quality flag.

ClinicalTrials.gov records use structured registry fields such as study type, phase and allocation. medRxiv and Crossref-only records cannot be PublicationType-validated and are explicitly labelled accordingly.

## Future-date quarantine

Any automated record whose source/update date is later than the workflow run date is excluded from the active matrix and written to `updates/quality_quarantine.json` under the `future_dated` category.

Quarantined records remain auditable but are not displayed in the matrix or active record list.

## Outputs

The workflow writes or updates:

```text
data/rsv_evidence_map_data_v0_2.json
data/rsv_evidence_map_records_v0_2.csv
updates/quality_quarantine.json
updates/quality_control_report.json
updates/surveillance_status.json
updates/*_candidates.json
```

The dashboard reads the JSON data file at page load. The workflow validates the dashboard and active data before deployment. Validation fails if it detects active future-dated records, duplicate PMIDs/DOIs, unvalidated automated PubMed records, resveratrol-only records, missing matrix rows/columns, or loss of curated seed records.

## Update schedule

The GitHub Actions workflow runs every Monday at 13:17 UTC and can also be run manually from the repository's **Actions** tab.
