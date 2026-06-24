#!/usr/bin/env python3
"""Validate the RSV dashboard and quality-controlled evidence data before deploy."""

from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path
from typing import Any

INDEX_PATH = Path("index.html")
DATA_PATH = Path("data/rsv_evidence_map_data_v0_2.json")
QUARANTINE_PATH = Path("updates/quality_quarantine.json")
EXPECTED_CURATED_SEED_RECORDS = 48
RSV_MESH_HEADINGS = {
    "respiratory syncytial virus infections",
    "respiratory syncytial viruses",
    "respiratory syncytial virus, human",
    "respiratory syncytial virus vaccines",
}
FULL_RSV_TERMS = re.compile(
    r"\b(respiratory syncytial virus(?:es| infection| infections)?|human rsv|hrsv|"
    r"rsv vaccine|rsv vaccines|rsv infection|rsv infections|arexvy|abrysvo|mresvia|"
    r"nirsevimab|clesrovimab|palivizumab|beyfortus|rsvpref3|mrna-1345)\b",
    re.I,
)
RESPIRATORY_CONTEXT = re.compile(
    r"\b(respiratory|virus|viral|bronchiolitis|pneumonia|infant\w*|maternal|pregnan\w*|"
    r"vaccin\w*|immuni[sz]\w*|monoclonal|antibod(?:y|ies)|prefusion|"
    r"syncytial|prophylaxis|season\w*|older adult\w*)\b",
    re.I,
)
RESVERATROL_CONTEXT = re.compile(
    r"\b(resveratrol|stilbene|polyphenol|sirtuin|sirt1|antioxidant|anticancer|"
    r"cardioprotective|neuroprotective)\b",
    re.I,
)


def is_auto(record: dict[str, Any]) -> bool:
    text = " ".join(
        str(record.get(key, ""))
        for key in ("id", "tags", "evidence_signal", "provenance")
    )
    return str(record.get("id", "")).upper().startswith("AUTO-") or bool(
        re.search(r"auto[- ]added|automated", text, re.I)
    )


def source_group(record: dict[str, Any]) -> str:
    text = " ".join(
        str(record.get(key, ""))
        for key in ("id", "tags", "citation", "source_url")
    ).lower()
    if "pubmed" in text:
        return "PubMed"
    if "medrxiv" in text:
        return "medRxiv"
    if "clinicaltrials" in text or "auto-ctgov" in text:
        return "ClinicalTrials.gov"
    if "crossref" in text:
        return "Crossref"
    return "Other"


def normalize_doi(value: str) -> str:
    value = str(value or "").strip().lower()
    value = re.sub(r"^https?://(?:dx\.)?doi\.org/", "", value)
    value = re.sub(r"^doi:\s*", "", value)
    return value.rstrip(".,;:)]}")


def record_doi(record: dict[str, Any]) -> str:
    explicit = normalize_doi(str(record.get("doi", "")))
    if explicit:
        return explicit
    text = " ".join(
        str(record.get(key, ""))
        for key in ("source_url", "tags", "citation")
    )
    match = re.search(r"10\.\d{4,9}/[^\s\"<>;,)\]}]+", text, re.I)
    return normalize_doi(match.group(0)) if match else ""


def record_pmid(record: dict[str, Any]) -> str:
    explicit = str(record.get("pmid", "")).strip()
    if explicit.isdigit():
        return explicit
    text = " ".join(
        str(record.get(key, ""))
        for key in ("id", "source_url", "tags", "citation")
    )
    for pattern in (
        r"AUTO-PUBMED-(\d+)",
        r"pubmed\.ncbi\.nlm\.nih\.gov/(\d+)",
        r"\bPMID\s*[: ]\s*(\d+)\b",
        r"\bPubMed\s+(\d+)\b",
    ):
        match = re.search(pattern, text, re.I)
        if match:
            return match.group(1)
    return ""


def parse_date(value: str) -> date | None:
    match = re.match(r"^(\d{4})-(\d{2})-(\d{2})", str(value or ""))
    if not match:
        return None
    try:
        return date(*map(int, match.groups()))
    except ValueError:
        return None


def record_source_date(record: dict[str, Any]) -> date | None:
    parsed = parse_date(str(record.get("evidence_update_date", "")))
    if parsed:
        return parsed
    year = str(record.get("year", "")).strip()
    if re.fullmatch(r"(?:19|20)\d{2}", year):
        return date(int(year), 1, 1)
    return None


def is_resveratrol_abbreviation_only(text: str) -> bool:
    return bool(
        re.search(r"\bRSV\b", text, re.I)
        and RESVERATROL_CONTEXT.search(text)
        and not FULL_RSV_TERMS.search(text)
        and not RESPIRATORY_CONTEXT.search(text)
    )


def list_values(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return [item.strip() for item in str(value or "").split(";") if item.strip()]


if not INDEX_PATH.exists():
    raise SystemExit("index.html is missing")
html = INDEX_PATH.read_text(encoding="utf-8", errors="ignore")
required_html = [
    "<!doctype html>",
    "DATA_URL",
    "Evidence matrix",
    "Evidence records",
    "Sources searched",
    "Automated-record data quality",
    "provenanceFilter",
]
missing_html = [item for item in required_html if item not in html]
if missing_html:
    raise SystemExit("index.html failed validation; missing: " + ", ".join(missing_html))
if html.lstrip().startswith("#"):
    raise SystemExit("index.html is Markdown/plain text, not dashboard HTML")

if not DATA_PATH.exists():
    raise SystemExit(f"{DATA_PATH} is missing")
data = json.loads(DATA_PATH.read_text(encoding="utf-8"))
records = data.get("records")
if not isinstance(records, list) or not records:
    raise SystemExit("data file has no records")
for key in ("rows", "columns", "method_sources", "metadata"):
    if key not in data:
        raise SystemExit(f"data file missing {key}")
if "Booster studies" not in data.get("rows", []):
    raise SystemExit("data file is missing the Booster studies evidence-domain row")

curated = [record for record in records if not is_auto(record)]
automated = [record for record in records if is_auto(record)]
if len(curated) < EXPECTED_CURATED_SEED_RECORDS:
    raise SystemExit(
        f"curated seed record count fell below {EXPECTED_CURATED_SEED_RECORDS}: {len(curated)}"
    )

allowed_rows = set(data.get("rows", []))
allowed_columns = set(data.get("columns", []))
invalid_cells = [
    str(record.get("id", ""))
    for record in records
    if record.get("matrix_row") not in allowed_rows
    or record.get("matrix_column") not in allowed_columns
]
if invalid_cells:
    raise SystemExit(
        "records use matrix rows/columns not declared in data: " + ", ".join(invalid_cells[:10])
    )

future_records = []
for record in automated:
    record_date = record_source_date(record)
    if record_date and record_date > date.today():
        future_records.append(str(record.get("id", "")))
if future_records:
    raise SystemExit(
        "future-dated automated records remain active: " + ", ".join(future_records[:10])
    )

doi_seen: dict[str, str] = {}
pmid_seen: dict[str, str] = {}
duplicate_messages: list[str] = []
for record in records:
    record_id = str(record.get("id", ""))
    doi = record_doi(record)
    pmid = record_pmid(record)
    if doi:
        if doi in doi_seen and doi_seen[doi] != record_id:
            duplicate_messages.append(f"DOI {doi}: {doi_seen[doi]} and {record_id}")
        doi_seen[doi] = record_id
    if pmid:
        if pmid in pmid_seen and pmid_seen[pmid] != record_id:
            duplicate_messages.append(f"PMID {pmid}: {pmid_seen[pmid]} and {record_id}")
        pmid_seen[pmid] = record_id
if duplicate_messages:
    raise SystemExit(
        "duplicate PMID/DOI identifiers remain in active records: "
        + " | ".join(duplicate_messages[:10])
    )

invalid_pubmed: list[str] = []
for record in automated:
    if source_group(record) != "PubMed":
        continue
    article_types = list_values(record.get("article_types", []))
    mesh_terms = {item.lower() for item in list_values(record.get("mesh_terms", []))}
    if not article_types or not (mesh_terms & RSV_MESH_HEADINGS):
        invalid_pubmed.append(str(record.get("id", "")))
if invalid_pubmed:
    raise SystemExit(
        "active automated PubMed records lack article-type or RSV-MeSH validation: "
        + ", ".join(invalid_pubmed[:10])
    )

resveratrol_records: list[str] = []
for record in automated:
    text = " ".join(str(value) for value in record.values())
    if is_resveratrol_abbreviation_only(text):
        resveratrol_records.append(str(record.get("id", "")))
if resveratrol_records:
    raise SystemExit(
        "resveratrol abbreviation-only records remain active: "
        + ", ".join(resveratrol_records[:10])
    )

quality_control = data.get("metadata", {}).get("quality_control", {})
for key in ("version", "audit_snapshot", "safeguards", "active_curated_records", "active_auto_records"):
    if key not in quality_control:
        raise SystemExit(f"metadata.quality_control missing {key}")

quarantined = 0
if QUARANTINE_PATH.exists():
    quarantine = json.loads(QUARANTINE_PATH.read_text(encoding="utf-8"))
    quarantined = int(quarantine.get("entry_count", 0))

print(
    "Site validation passed: "
    f"{len(records)} active records ({len(curated)} curated, {len(automated)} automated); "
    f"{quarantined} quarantined records."
)
