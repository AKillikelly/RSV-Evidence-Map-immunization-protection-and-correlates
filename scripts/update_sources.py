#!/usr/bin/env python3
"""Quality-controlled automated surveillance for the RSV evidence map.

The source of truth is data/rsv_evidence_map_data_v0_2.json.

Safeguards implemented here:
* PubMed auto-inclusion requires an RSV-specific MeSH heading.
* Abbreviation-only "RSV" records are rejected when the context is resveratrol.
* PMID and DOI identifiers are cross-walked before records enter the matrix.
* PubMed study design is derived from PublicationType metadata, not title keywords.
* Future-dated records are quarantined outside the active matrix.
* Existing automated records are re-audited and de-duplicated on every run.

Curated seed records are never automatically removed or reclassified.
"""

from __future__ import annotations

import csv
import hashlib
import json
import os
import re
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Iterable

DATA_PATH = Path("data/rsv_evidence_map_data_v0_2.json")
CSV_PATH = Path("data/rsv_evidence_map_records_v0_2.csv")
UPDATES_DIR = Path("updates")
QUARANTINE_PATH = UPDATES_DIR / "quality_quarantine.json"
QC_REPORT_PATH = UPDATES_DIR / "quality_control_report.json"

LOOKBACK_DAYS = int(os.getenv("LOOKBACK_DAYS", "14"))
TODAY = date.today()
START = TODAY - timedelta(days=LOOKBACK_DAYS)
QC_VERSION = "2026-06-qc1"
EXPECTED_CURATED_SEED_RECORDS = 48

ROWS = [
    "Immune correlates & antigen design",
    "Adult active vaccination",
    "Booster studies",
    "Maternal active vaccination",
    "Infant passive immunization",
    "Pediatric active vaccine safety/development",
    "Policy, implementation & real-world evidence",
]

COLS = [
    "Evidence synthesis / guidance",
    "Human RCT / controlled trial",
    "Observational / real-world",
    "Translational / preclinical",
    "Safety signal / surveillance",
]

FIELDS = [
    "id",
    "matrix_row",
    "matrix_column",
    "year",
    "citation",
    "title",
    "authors_source",
    "study_design",
    "population",
    "product_platform",
    "outcome_domain",
    "key_finding",
    "evidence_signal",
    "actionability",
    "source_url",
    "tags",
    "evidence_update_date",
    "evidence_update_precision",
    "evidence_update_basis",
    "map_record_updated",
    "provenance",
    "pmid",
    "doi",
    "article_types",
    "mesh_terms",
    "species",
    "quality_status",
    "quality_flags",
    "quality_control_version",
]

# PubMed is deliberately strict: abbreviation-only RSV hits are not sufficient.
PUBMED_QUERY = (
    '("Respiratory Syncytial Virus Infections"[Mesh] '
    'OR "Respiratory Syncytial Virus, Human"[Mesh] '
    'OR "Respiratory Syncytial Virus Vaccines"[Mesh]) '
    "AND (vaccin*[tiab] OR immunization[tiab] OR immunisation[tiab] "
    "OR nirsevimab[tiab] OR clesrovimab[tiab] OR palivizumab[tiab] "
    'OR monoclonal[tiab] OR "prefusion F"[tiab] OR maternal[tiab] '
    "OR pregnan*[tiab] OR adult*[tiab] OR infant*[tiab] OR safety[tiab] "
    "OR effectiveness[tiab] OR efficacy[tiab] OR correlate*[tiab] "
    "OR booster*[tiab] OR revaccin*[tiab] OR waning[tiab] OR durability[tiab])"
)

RSV_MESH_HEADINGS = {
    "respiratory syncytial virus infections",
    "respiratory syncytial viruses",
    "respiratory syncytial virus, human",
    "respiratory syncytial virus vaccines",
}

PREVENTION_TERMS = re.compile(
    r"\b(vaccin\w*|immuni[sz]\w*|nirsevimab|clesrovimab|palivizumab|monoclonal|"
    r"prefusion|maternal|pregnan\w*|safety|efficacy|effectiveness|correlat\w*|"
    r"antibod(?:y|ies)|prophylaxis|prevention|booster\w*|revaccin\w*|repeat[- ]dose|"
    r"additional dose|waning|durability)\b",
    re.I,
)
FULL_RSV_TERMS = re.compile(
    r"\b(respiratory syncytial virus(?:es| infection| infections)?|human rsv|hrsv|"
    r"rsv vaccine|rsv vaccines|rsv infection|rsv infections|arexvy|abrysvo|mresvia|"
    r"nirsevimab|clesrovimab|palivizumab|beyfortus|rsvpref3|mrna-1345)\b",
    re.I,
)
STANDALONE_RSV = re.compile(r"\bRSV\b", re.I)
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

BOOSTER_ROW = "Booster studies"
EXPLICIT_BOOSTER_TERMS = re.compile(
    r"\b(booster(?:s| dose| doses| vaccination| vaccinations)?|"
    r"revaccin(?:ate|ated|ating|ation|ations)|"
    r"re-vaccin(?:ate|ated|ating|ation|ations))\b",
    re.I,
)
REPEAT_DOSE_TERMS = re.compile(
    r"\b(repeat(?:ed)?[- ]dose|additional dose|subsequent dose|annual revaccination|"
    r"annual booster|redosing|re-dosing|re-dose)\b",
    re.I,
)
ACTIVE_VACCINE_TERMS = re.compile(
    r"\b(vaccin\w*|immuni[sz]\w*|arexvy|abrysvo|mresvia|rsvpref3|mrna-1345|prefusion[- ]?f)\b",
    re.I,
)
PASSIVE_PRODUCT_TERMS = re.compile(
    r"\b(nirsevimab|clesrovimab|palivizumab|monoclonal|beyfortus)\b", re.I
)

ROW_ALIASES = {
    "Maternal vaccination & infant protection": "Maternal active vaccination",
    "Infant monoclonal antibodies": "Infant passive immunization",
    "Pediatric active-vaccine development & safety": "Pediatric active vaccine safety/development",
}

GUIDANCE_PAGES = [
    {
        "name": "CDC adult RSV vaccine clinical guidance",
        "url": "https://www.cdc.gov/rsv/hcp/vaccine-clinical-guidance/adults.html",
        "source_group": "CDC / ACIP",
    },
    {
        "name": "CDC RSV immunization guidance for infants and young children",
        "url": "https://www.cdc.gov/rsv/hcp/vaccine-clinical-guidance/infants-young-children.html",
        "source_group": "CDC / ACIP",
    },
    {
        "name": "CDC RSV vaccine guidance during pregnancy",
        "url": "https://www.cdc.gov/rsv/hcp/vaccine-clinical-guidance/pregnant-people.html",
        "source_group": "CDC / ACIP",
    },
    {
        "name": "CDC ACIP RSV recommendations",
        "url": "https://www.cdc.gov/acip/vaccine-recommendations/index.html",
        "source_group": "CDC / ACIP",
    },
    {
        "name": "FDA respiratory syncytial virus vaccines",
        "url": "https://www.fda.gov/vaccines-blood-biologics/vaccines/respiratory-syncytial-virus-rsv",
        "source_group": "FDA",
    },
    {
        "name": "WHO respiratory syncytial virus fact sheet",
        "url": "https://www.who.int/news-room/fact-sheets/detail/respiratory-syncytial-virus-(rsv)",
        "source_group": "WHO",
    },
    {
        "name": "WHO RSV infant immunization position paper",
        "url": "https://www.who.int/publications/i/item/who-wer10024-277-300",
        "source_group": "WHO",
    },
    {
        "name": "EMA Arexvy",
        "url": "https://www.ema.europa.eu/en/medicines/human/EPAR/arexvy",
        "source_group": "EMA",
    },
    {
        "name": "EMA Abrysvo",
        "url": "https://www.ema.europa.eu/en/medicines/human/EPAR/abrysvo",
        "source_group": "EMA",
    },
    {
        "name": "EMA Beyfortus",
        "url": "https://www.ema.europa.eu/en/medicines/human/EPAR/beyfortus",
        "source_group": "EMA",
    },
    {
        "name": "PHAC RSV vaccines and monoclonal antibodies",
        "url": "https://www.canada.ca/en/public-health/services/immunization-vaccines/vaccination-respiratory-syncytial-virus.html",
        "source_group": "PHAC / NACI",
    },
    {
        "name": "NACI RSV statement collection",
        "url": "https://www.canada.ca/en/public-health/services/immunization/national-advisory-committee-on-immunization-naci.html",
        "source_group": "PHAC / NACI",
    },
    {
        "name": "UKHSA RSV vaccination programme",
        "url": "https://www.gov.uk/government/collections/respiratory-syncytial-virus-rsv-vaccination-programme",
        "source_group": "UKHSA / JCVI",
    },
    {
        "name": "JCVI statements and advice",
        "url": "https://www.gov.uk/government/groups/joint-committee-on-vaccination-and-immunisation",
        "source_group": "UKHSA / JCVI",
    },
]

MONTHS = {
    "jan": 1,
    "feb": 2,
    "mar": 3,
    "apr": 4,
    "may": 5,
    "jun": 6,
    "jul": 7,
    "aug": 8,
    "sep": 9,
    "oct": 10,
    "nov": 11,
    "dec": 12,
}


def fetch(url: str, timeout: int = 35) -> bytes:
    email = os.getenv("NCBI_EMAIL", "").strip()
    user_agent = "rsv-evidence-map/2.0"
    if email:
        user_agent += f" ({email})"
    req = urllib.request.Request(url, headers={"User-Agent": user_agent})
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return response.read()


def safe_fetch(url: str, timeout: int = 35) -> bytes | None:
    try:
        return fetch(url, timeout=timeout)
    except Exception as exc:  # network failures must not corrupt the map
        print(f"WARN: fetch failed: {url}: {exc}")
        return None


def text_el(element: ET.Element | None) -> str:
    return "".join(element.itertext()).strip() if element is not None else ""


def list_text(value: Any) -> str:
    if isinstance(value, list):
        return "; ".join(str(x) for x in value if str(x).strip())
    return str(value or "")


def normalize_doi(value: str) -> str:
    value = str(value or "").strip().lower()
    value = re.sub(r"^https?://(?:dx\.)?doi\.org/", "", value)
    value = re.sub(r"^doi:\s*", "", value)
    return value.rstrip(".,;:)]}")


def extract_dois(text: str) -> list[str]:
    found = re.findall(r"10\.\d{4,9}/[^\s\"<>;,)\]}]+", str(text or ""), flags=re.I)
    return sorted({normalize_doi(item) for item in found if normalize_doi(item)})


def extract_pmid(record: dict[str, Any]) -> str:
    explicit = str(record.get("pmid", "")).strip()
    if explicit.isdigit():
        return explicit
    text = " ".join(
        str(record.get(key, ""))
        for key in ("id", "source_url", "tags", "citation")
    )
    patterns = [
        r"AUTO-PUBMED-(\d+)",
        r"pubmed\.ncbi\.nlm\.nih\.gov/(\d+)",
        r"\bPMID\s*[: ]\s*(\d+)\b",
        r"\bPubMed\s+(\d+)\b",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.I)
        if match:
            return match.group(1)
    return ""


def record_doi(record: dict[str, Any]) -> str:
    explicit = normalize_doi(str(record.get("doi", "")))
    if explicit:
        return explicit
    text = " ".join(
        str(record.get(key, ""))
        for key in ("source_url", "tags", "citation", "title")
    )
    dois = extract_dois(text)
    return dois[0] if dois else ""


def normalize_title(title: str) -> str:
    title = re.sub(r"<[^>]+>", " ", str(title or ""))
    title = re.sub(r"[^a-z0-9]+", " ", title.lower())
    return " ".join(title.split())


def normalize_date(value: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    iso_match = re.match(r"^(\d{4})-(\d{1,2})-(\d{1,2})", raw)
    if iso_match:
        year, month, day = map(int, iso_match.groups())
        try:
            return date(year, month, day).isoformat()
        except ValueError:
            return ""
    ym_match = re.match(r"^(\d{4})-(\d{1,2})$", raw)
    if ym_match:
        year, month = map(int, ym_match.groups())
        try:
            return date(year, month, 1).isoformat()
        except ValueError:
            return ""
    year_match = re.search(r"\b(19|20)\d{2}\b", raw)
    if year_match:
        return f"{year_match.group(0)}-01-01"
    return ""


def is_future_date(value: str) -> bool:
    normalized = normalize_date(value)
    if not normalized:
        return False
    return date.fromisoformat(normalized) > TODAY


def record_source_date(record: dict[str, Any]) -> str:
    """Return the best available source date, including a year-only fallback."""
    normalized = normalize_date(str(record.get("evidence_update_date", "")))
    if normalized:
        return normalized
    year = str(record.get("year", "")).strip()
    if re.fullmatch(r"(?:19|20)\d{2}", year):
        return f"{year}-01-01"
    return ""


def merge_semicolon_values(*values: Any) -> str:
    output: list[str] = []
    seen: set[str] = set()
    for value in values:
        items = value if isinstance(value, list) else re.split(r"[;|]", str(value or ""))
        for item in items:
            item = str(item).strip()
            if item and item.lower() not in seen:
                seen.add(item.lower())
                output.append(item)
    return "; ".join(output)


def is_auto_record(record: dict[str, Any]) -> bool:
    record_id = str(record.get("id", ""))
    text = " ".join(
        str(record.get(key, ""))
        for key in ("tags", "evidence_signal", "provenance")
    )
    return record_id.upper().startswith("AUTO-") or bool(
        re.search(r"auto[- ]added|automated", text, flags=re.I)
    )


def source_group(record: dict[str, Any]) -> str:
    text = " ".join(
        str(record.get(key, ""))
        for key in ("id", "tags", "citation", "source_url", "authors_source")
    ).lower()
    if "pubmed" in text:
        return "PubMed"
    if "medrxiv" in text:
        return "medRxiv"
    if "clinicaltrials" in text or "auto-ctgov" in text or re.search(r"\bNCT\d+", text, re.I):
        return "ClinicalTrials.gov"
    if "crossref" in text:
        return "Crossref"
    if "guidance" in text or any(x in text for x in ("cdc", "fda", "who", "ema", "naci", "phac", "jcvi", "ukhsa")):
        return "Guidance/regulatory"
    return "Other automated source" if is_auto_record(record) else "Curated seed"


def source_priority(record: dict[str, Any]) -> int:
    if not is_auto_record(record):
        return 100
    return {
        "PubMed": 90,
        "medRxiv": 80,
        "ClinicalTrials.gov": 70,
        "Guidance/regulatory": 60,
        "Crossref": 50,
        "Other automated source": 40,
    }.get(source_group(record), 40)


def record_keys(record: dict[str, Any]) -> set[str]:
    keys: set[str] = set()
    pmid = extract_pmid(record)
    doi = record_doi(record)
    if pmid:
        keys.add(f"pmid:{pmid}")
    if doi:
        keys.add(f"doi:{doi}")
    record_id = str(record.get("id", "")).strip().lower()
    if record_id:
        keys.add(f"id:{record_id}")
    url = str(record.get("source_url", "")).strip().lower().rstrip("/")
    if url:
        keys.add(f"url:{url}")
    nct_match = re.search(r"\bNCT\d{8}\b", " ".join(map(str, record.values())), flags=re.I)
    if nct_match:
        keys.add(f"nct:{nct_match.group(0).upper()}")
    title = normalize_title(str(record.get("title", "")))
    if len(title) >= 35:
        keys.add(f"title:{title}")
    return keys


def is_rsv_mesh_valid(mesh_terms: Iterable[str]) -> bool:
    normalized = {str(term).strip().lower() for term in mesh_terms}
    return bool(normalized & RSV_MESH_HEADINGS)


def is_rsv_relevant(text: str, mesh_terms: Iterable[str] = ()) -> bool:
    text = str(text or "")
    if is_rsv_mesh_valid(mesh_terms):
        return True
    if FULL_RSV_TERMS.search(text):
        return True
    if STANDALONE_RSV.search(text):
        if RESVERATROL_CONTEXT.search(text) and not RESPIRATORY_CONTEXT.search(text):
            return False
        return bool(RESPIRATORY_CONTEXT.search(text))
    return False


def is_resveratrol_abbreviation_only(text: str, mesh_terms: Iterable[str] = ()) -> bool:
    text = str(text or "")
    return bool(
        STANDALONE_RSV.search(text)
        and RESVERATROL_CONTEXT.search(text)
        and not FULL_RSV_TERMS.search(text)
        and not is_rsv_mesh_valid(mesh_terms)
    )


def is_booster_text(text: str) -> bool:
    if PASSIVE_PRODUCT_TERMS.search(text):
        return False
    if EXPLICIT_BOOSTER_TERMS.search(text):
        return True
    return bool(REPEAT_DOSE_TERMS.search(text) and ACTIVE_VACCINE_TERMS.search(text))


def classify_domain(title: str, abstract: str = "", extra: str = "") -> tuple[str, str, str]:
    text = f"{title} {abstract} {extra}".lower()
    if is_booster_text(text):
        return BOOSTER_ROW, "Previously immunized populations", "RSV booster / revaccination strategy"
    if re.search(r"maternal|pregnan|antenatal", text) and re.search(r"vaccin|immuni[sz]|abrysvo|rsvpre", text):
        return "Maternal active vaccination", "Pregnant people / infants", "Maternal RSV vaccine"
    if PASSIVE_PRODUCT_TERMS.search(text):
        return "Infant passive immunization", "Infants / young children", "Long-acting monoclonal antibody"
    if re.search(r"older adult|adult|aged|elderly|arexvy|abrysvo|mresvia|mrna-1345|rsvpref3", text):
        return "Adult active vaccination", "Adults / older adults", "Adult RSV vaccine"
    if re.search(r"pediatric|paediatric|children|child|toddler", text) and re.search(r"vaccine|trial|safety|enhanced", text):
        return (
            "Pediatric active vaccine safety/development",
            "Children / pediatric populations",
            "Pediatric RSV vaccine candidate",
        )
    if re.search(r"correlate|antigen|prefusion|neutraliz|immunogenic|antibody|epitope", text):
        return (
            "Immune correlates & antigen design",
            "Not population-specific / immunologic evidence",
            "RSV F antigen / immune correlate",
        )
    return (
        "Policy, implementation & real-world evidence",
        "Mixed / policy-relevant populations",
        "RSV prevention product/platform not auto-specified",
    )


def classify_nonpubmed_column(text: str) -> str:
    text = text.lower()
    if re.search(r"guideline|recommendation|position paper|statement|systematic review|meta-analysis|review|policy|advisory|regulatory|official guidance", text):
        return "Evidence synthesis / guidance"
    if re.search(r"randomi[sz]|phase\s*[1234]|controlled trial|clinical trial|efficacy trial", text):
        return "Human RCT / controlled trial"
    if re.search(r"case report|adverse event|safety signal|guillain|pharmacovigilance", text):
        return "Safety signal / surveillance"
    if re.search(r"animal|mouse|mice|rat|rats|hamster|cotton rat|in vitro|preclinical", text):
        return "Translational / preclinical"
    return "Observational / real-world"


def pubmed_design(article_types: list[str], mesh_terms: list[str], title: str, abstract: str) -> tuple[str, str, str, list[str]]:
    types = {item.lower() for item in article_types}
    mesh = {item.lower() for item in mesh_terms}
    flags: list[str] = []

    humans = "humans" in mesh
    animals = "animals" in mesh
    species = "Human"
    if animals and humans:
        species = "Human and animal"
    elif animals:
        species = "Animal"
    elif not humans:
        species = "Not specified"

    review_types = {
        "systematic review",
        "meta-analysis",
        "review",
        "practice guideline",
        "guideline",
        "consensus development conference",
        "consensus development conference, nih",
    }
    trial_types = {
        "randomized controlled trial",
        "controlled clinical trial",
        "clinical trial",
        "clinical trial, phase i",
        "clinical trial, phase ii",
        "clinical trial, phase iii",
        "clinical trial, phase iv",
        "pragmatic clinical trial",
        "adaptive clinical trial",
    }
    observational_types = {
        "observational study",
        "comparative study",
        "evaluation study",
        "validation study",
        "multicenter study",
    }
    safety_types = {"case reports"}

    if animals and not humans:
        column = "Translational / preclinical"
        design = "Animal study"
    elif types & review_types:
        column = "Evidence synthesis / guidance"
        design = sorted(types & review_types)[0].title()
    elif types & trial_types:
        column = "Human RCT / controlled trial"
        design = sorted(types & trial_types)[0].title()
    elif types & safety_types:
        column = "Safety signal / surveillance"
        design = "Case report(s)"
    elif types & observational_types:
        column = "Observational / real-world"
        design = sorted(types & observational_types)[0].title()
    elif humans:
        column = "Observational / real-world"
        design = "Human journal article; specific design not indexed as a PubMed publication type"
        flags.append("generic_pubmed_article_type")
    else:
        lab_mesh = any(
            phrase in " ".join(mesh)
            for phrase in ("cell line", "in vitro techniques", "antigens", "antibodies", "molecular")
        )
        if lab_mesh:
            column = "Translational / preclinical"
            design = "Translational/laboratory article; specific design not indexed as a PubMed publication type"
        else:
            column = "Observational / real-world"
            design = "Journal article; specific design not indexed as a PubMed publication type"
        flags.append("generic_pubmed_article_type")

    indexed = "; ".join(article_types) if article_types else "No PublicationType supplied"
    study_design = f"{design}. PubMed article type(s): {indexed}"
    return column, study_design, species, flags


def pubmed_date(article: ET.Element) -> str:
    pub_date = article.find(".//JournalIssue/PubDate")
    year = pub_date.findtext("Year") if pub_date is not None else ""
    month_raw = pub_date.findtext("Month") if pub_date is not None else ""
    day_raw = pub_date.findtext("Day") if pub_date is not None else ""
    medline = pub_date.findtext("MedlineDate") if pub_date is not None else ""
    if not year:
        match = re.search(r"\b(19|20)\d{2}\b", medline or "")
        year = match.group(0) if match else str(TODAY.year)
    month = 1
    if month_raw:
        if month_raw.isdigit():
            month = max(1, min(12, int(month_raw)))
        else:
            month = MONTHS.get(month_raw[:3].lower(), 1)
    day = int(day_raw) if str(day_raw).isdigit() else 1
    try:
        return date(int(year), month, day).isoformat()
    except ValueError:
        return f"{year}-01-01"


def parse_pubmed_article(article: ET.Element) -> dict[str, Any]:
    pmid = article.findtext(".//PMID") or ""
    title = text_el(article.find(".//ArticleTitle"))
    abstract = " ".join(
        text_el(item) for item in article.findall(".//AbstractText") if text_el(item)
    )
    authors: list[str] = []
    for author in article.findall(".//Author")[:12]:
        collective = author.findtext("CollectiveName")
        name = collective or " ".join(
            filter(None, [author.findtext("ForeName"), author.findtext("LastName")])
        )
        if name:
            authors.append(name)
    article_types = [
        text_el(item)
        for item in article.findall(".//PublicationTypeList/PublicationType")
        if text_el(item)
    ]
    mesh_terms = [
        text_el(item)
        for item in article.findall(".//MeshHeadingList/MeshHeading/DescriptorName")
        if text_el(item)
    ]
    doi = ""
    for identifier in article.findall(".//PubmedData/ArticleIdList/ArticleId"):
        if str(identifier.attrib.get("IdType", "")).lower() == "doi":
            doi = normalize_doi(text_el(identifier))
            break
    return {
        "pmid": pmid,
        "doi": doi,
        "title": title,
        "abstract": abstract,
        "authors": authors,
        "date": pubmed_date(article),
        "article_types": article_types,
        "mesh_terms": mesh_terms,
    }


def ncbi_url(endpoint: str, params: dict[str, Any]) -> str:
    params = dict(params)
    params.setdefault("tool", "rsv_evidence_map")
    email = os.getenv("NCBI_EMAIL", "").strip()
    api_key = os.getenv("NCBI_API_KEY", "").strip()
    if email:
        params["email"] = email
    if api_key:
        params["api_key"] = api_key
    return f"https://eutils.ncbi.nlm.nih.gov/entrez/eutils/{endpoint}?{urllib.parse.urlencode(params)}"


def fetch_pubmed_metadata(pmids: Iterable[str]) -> dict[str, dict[str, Any]]:
    clean = sorted({str(pmid) for pmid in pmids if str(pmid).isdigit()})
    output: dict[str, dict[str, Any]] = {}
    for start in range(0, len(clean), 100):
        batch = clean[start : start + 100]
        payload = safe_fetch(
            ncbi_url(
                "efetch.fcgi",
                {"db": "pubmed", "id": ",".join(batch), "retmode": "xml"},
            )
        )
        if not payload:
            continue
        root = ET.fromstring(payload)
        for article in root.findall(".//PubmedArticle"):
            parsed = parse_pubmed_article(article)
            if parsed["pmid"]:
                output[parsed["pmid"]] = parsed
        if not os.getenv("NCBI_API_KEY"):
            time.sleep(0.35)
    return output


def search_pubmed_pmids() -> list[str]:
    payload = safe_fetch(
        ncbi_url(
            "esearch.fcgi",
            {
                "db": "pubmed",
                "term": PUBMED_QUERY,
                "retmode": "json",
                "retmax": "200",
                "sort": "pub_date",
                "datetype": "mdat",
                "mindate": START.isoformat(),
                "maxdate": TODAY.isoformat(),
            },
        )
    )
    if not payload:
        return []
    if not os.getenv("NCBI_API_KEY"):
        time.sleep(0.35)
    return json.loads(payload.decode("utf-8")).get("esearchresult", {}).get("idlist", [])


def crosswalk_dois_to_pubmed(dois: Iterable[str]) -> dict[str, str]:
    clean = sorted({normalize_doi(doi) for doi in dois if normalize_doi(doi)})
    output: dict[str, str] = {}
    for start in range(0, len(clean), 15):
        batch = clean[start : start + 15]
        term = " OR ".join(f'"{doi}"[AID]' for doi in batch)
        payload = safe_fetch(
            ncbi_url(
                "esearch.fcgi",
                {"db": "pubmed", "term": term, "retmode": "json", "retmax": "100"},
            )
        )
        if not payload:
            continue
        if not os.getenv("NCBI_API_KEY"):
            time.sleep(0.35)
        pmids = json.loads(payload.decode("utf-8")).get("esearchresult", {}).get("idlist", [])
        metadata = fetch_pubmed_metadata(pmids)
        for pmid, item in metadata.items():
            doi = normalize_doi(item.get("doi", ""))
            if doi:
                output[doi] = pmid
    return output


class Quarantine:
    def __init__(self, path: Path):
        self.path = path
        self.entries: list[dict[str, Any]] = []
        if path.exists():
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                self.entries = list(payload.get("entries", []))
            except Exception:
                self.entries = []
        self._keys = {
            str(entry.get("quarantine_key", "")): index
            for index, entry in enumerate(self.entries)
            if entry.get("quarantine_key")
        }
        self.added_this_run = 0

    def add(
        self,
        record: dict[str, Any],
        category: str,
        reason: str,
        duplicate_of: str = "",
        details: dict[str, Any] | None = None,
    ) -> None:
        record_id = str(record.get("id", "unknown"))
        key = f"{category}|{record_id}|{duplicate_of}".lower()
        entry = {
            "quarantine_key": key,
            "category": category,
            "reason": reason,
            "duplicate_of": duplicate_of,
            "first_quarantined_date": TODAY.isoformat(),
            "last_seen_date": TODAY.isoformat(),
            "record": record,
            "details": details or {},
        }
        if key in self._keys:
            old = self.entries[self._keys[key]]
            entry["first_quarantined_date"] = old.get(
                "first_quarantined_date", TODAY.isoformat()
            )
            self.entries[self._keys[key]] = entry
        else:
            self._keys[key] = len(self.entries)
            self.entries.append(entry)
            self.added_this_run += 1

    def counts(self) -> dict[str, int]:
        result: dict[str, int] = {}
        for entry in self.entries:
            category = str(entry.get("category", "other"))
            result[category] = result.get(category, 0) + 1
        return result

    def write(self) -> None:
        payload = {
            "quality_control_version": QC_VERSION,
            "updated_date": TODAY.isoformat(),
            "entry_count": len(self.entries),
            "category_counts": self.counts(),
            "entries": self.entries,
        }
        self.path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
        )


def ensure_rows(data: dict[str, Any]) -> bool:
    changed = False
    current = data.get("rows") if isinstance(data.get("rows"), list) else []
    canonical: list[str] = []
    for row in current:
        row_value = ROW_ALIASES.get(str(row), str(row))
        if row_value and row_value not in canonical:
            canonical.append(row_value)
    ordered = list(ROWS)
    for row in canonical:
        if row not in ordered:
            ordered.append(row)
    if current != ordered:
        data["rows"] = ordered
        changed = True
    for record in data.get("records", []):
        old = str(record.get("matrix_row", ""))
        new = ROW_ALIASES.get(old, old)
        if old != new:
            record["matrix_row"] = new
            changed = True
    data.setdefault("columns", COLS)
    return changed


def load_data() -> dict[str, Any]:
    if not DATA_PATH.exists():
        raise SystemExit(f"Missing {DATA_PATH}")
    return json.loads(DATA_PATH.read_text(encoding="utf-8"))


def csv_value(value: Any) -> Any:
    if isinstance(value, list):
        return "; ".join(str(item) for item in value)
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False)
    return value


def save_data(data: dict[str, Any]) -> None:
    DATA_PATH.write_text(
        json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    with CSV_PATH.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS, extrasaction="ignore")
        writer.writeheader()
        for record in data.get("records", []):
            writer.writerow({field: csv_value(record.get(field, "")) for field in FIELDS})


def apply_pubmed_metadata(record: dict[str, Any], meta: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    before = json.dumps(record, sort_keys=True, ensure_ascii=False)
    title = meta.get("title") or record.get("title", "")
    abstract = meta.get("abstract") or record.get("key_finding", "")
    article_types = list(meta.get("article_types", []))
    mesh_terms = list(meta.get("mesh_terms", []))
    column, study_design, species, flags = pubmed_design(
        article_types, mesh_terms, title, abstract
    )
    row, population, platform = classify_domain(
        title, abstract, " ".join(mesh_terms)
    )
    pmid = str(meta.get("pmid", ""))
    doi = normalize_doi(str(meta.get("doi", "")))

    record.update(
        {
            "title": title,
            "matrix_row": row,
            "matrix_column": column,
            "study_design": study_design,
            "population": population,
            "product_platform": platform,
            "pmid": pmid,
            "doi": doi,
            "article_types": article_types,
            "mesh_terms": mesh_terms,
            "species": species,
            "provenance": "Automated",
            "quality_status": "RSV MeSH and PubMed article-type validated",
            "quality_flags": flags,
            "quality_control_version": QC_VERSION,
            "evidence_update_basis": "PubMed metadata; RSV-specific MeSH validated; study design derived from PublicationType",
        }
    )
    if meta.get("date"):
        record["evidence_update_date"] = meta["date"]
        record["year"] = int(meta["date"][:4])
    tags = merge_semicolon_values(
        record.get("tags", ""),
        "auto-added",
        "PubMed",
        f"PMID {pmid}" if pmid else "",
        f"DOI {doi}" if doi else "",
        f"QC {QC_VERSION}",
    )
    record["tags"] = tags
    after = json.dumps(record, sort_keys=True, ensure_ascii=False)
    changed = before != after
    if changed:
        record["map_record_updated"] = TODAY.isoformat()
    return record, changed


def repair_existing_records(
    data: dict[str, Any], quarantine: Quarantine
) -> dict[str, int]:
    records = list(data.get("records", []))
    automated_records = [record for record in records if is_auto_record(record)]

    # Enrich every automated DOI with its PubMed counterpart where one exists.
    # This allows Crossref-discovered records and older auto-added records to be
    # compared on both DOI and PMID before de-duplication.
    auto_dois = [record_doi(record) for record in automated_records if record_doi(record)]
    doi_to_pmid = crosswalk_dois_to_pubmed(auto_dois)
    auto_pubmed_pmids = {
        extract_pmid(record)
        for record in automated_records
        if extract_pmid(record)
    }
    auto_pubmed_pmids.update(doi_to_pmid.values())
    pubmed_meta = fetch_pubmed_metadata(auto_pubmed_pmids)

    retained: list[dict[str, Any]] = []
    stats = {
        "curated_preserved": 0,
        "pubmed_reclassified": 0,
        "doi_pmid_crosswalked": 0,
        "off_topic_quarantined": 0,
        "future_dated_quarantined": 0,
        "missing_rsv_mesh_quarantined": 0,
        "duplicates_quarantined": 0,
        "pending_pubmed_metadata": 0,
    }

    for record in records:
        record = dict(record)
        if not is_auto_record(record):
            record.setdefault("provenance", "Curated seed")
            retained.append(record)
            stats["curated_preserved"] += 1
            continue

        record["provenance"] = "Automated"
        all_text = " ".join(
            list_text(record.get(field, ""))
            for field in (
                "title",
                "citation",
                "key_finding",
                "tags",
                "product_platform",
                "outcome_domain",
            )
        )
        mesh_terms = record.get("mesh_terms", [])
        if isinstance(mesh_terms, str):
            mesh_terms = [item.strip() for item in mesh_terms.split(";") if item.strip()]

        if is_future_date(record_source_date(record)):
            quarantine.add(
                record,
                "future_dated",
                "Evidence source date/year is later than the workflow run date.",
            )
            stats["future_dated_quarantined"] += 1
            continue

        if is_resveratrol_abbreviation_only(all_text, mesh_terms):
            quarantine.add(
                record,
                "off_topic_resveratrol",
                'The only relevant-looking "RSV" hit is the resveratrol abbreviation, not respiratory syncytial virus.',
            )
            stats["off_topic_quarantined"] += 1
            continue

        original_group = source_group(record)
        doi = record_doi(record)
        explicit_pmid = extract_pmid(record)
        crosswalk_pmid = doi_to_pmid.get(doi, "") if doi else ""
        pmid = explicit_pmid or crosswalk_pmid
        meta = pubmed_meta.get(pmid) if pmid else None

        if meta and (original_group == "PubMed" or crosswalk_pmid):
            if not is_rsv_mesh_valid(meta.get("mesh_terms", [])):
                quarantine.add(
                    record,
                    "missing_rsv_mesh",
                    "PubMed-linked record does not contain an RSV-specific MeSH heading.",
                    details={"pmid": pmid, "doi": doi, "mesh_terms": meta.get("mesh_terms", [])},
                )
                stats["missing_rsv_mesh_quarantined"] += 1
                continue
            if is_future_date(str(meta.get("date", ""))):
                quarantine.add(
                    record,
                    "future_dated",
                    "PubMed publication date is later than the workflow run date.",
                    details={"pmid": pmid, "doi": doi, "date": meta.get("date")},
                )
                stats["future_dated_quarantined"] += 1
                continue

            if original_group != "PubMed" and crosswalk_pmid:
                original_id = str(record.get("id", ""))
                original_tags = record.get("tags", "")
                record = create_pubmed_record(meta)
                record["tags"] = merge_semicolon_values(
                    record.get("tags", ""),
                    original_tags,
                    f"PMID↔DOI crosswalk from {original_group}",
                    f"supersedes {original_id}" if original_id else "",
                )
                record["evidence_update_basis"] += f"; upgraded from {original_group} through PMID↔DOI crosswalk"
                stats["doi_pmid_crosswalked"] += 1
                stats["pubmed_reclassified"] += 1
            else:
                record, changed = apply_pubmed_metadata(record, meta)
                if changed:
                    stats["pubmed_reclassified"] += 1
        elif original_group == "PubMed":
            # Do not silently guess a design when PubMed validation is unavailable.
            # Retain the record in the working copy so validation stops deployment
            # rather than deleting a large block of evidence after a transient API error.
            record["quality_status"] = "Pending PubMed metadata validation"
            record["quality_flags"] = merge_semicolon_values(
                record.get("quality_flags", ""), "pubmed_metadata_unavailable"
            )
            stats["pending_pubmed_metadata"] += 1
        else:
            record.setdefault("quality_control_version", QC_VERSION)
            record.setdefault("quality_status", "Automated record; source-specific validation only")

        retained.append(record)

    # De-duplicate after PubMed DOI enrichment. Curated records always win.
    curated = [record for record in retained if not is_auto_record(record)]
    automated = [record for record in retained if is_auto_record(record)]
    kept: list[dict[str, Any]] = list(curated)
    key_to_index: dict[str, int] = {}
    for index, record in enumerate(kept):
        for key in record_keys(record):
            key_to_index.setdefault(key, index)

    # Higher-quality automated sources and newer versions are evaluated first.
    automated.sort(
        key=lambda record: (
            source_priority(record),
            normalize_date(str(record.get("evidence_update_date", ""))),
        ),
        reverse=True,
    )
    for record in automated:
        keys = record_keys(record)
        matches = sorted({key_to_index[key] for key in keys if key in key_to_index})
        if matches:
            canonical = kept[matches[0]]
            quarantine.add(
                record,
                "duplicate",
                "Duplicate identifier/title detected after PMID↔DOI normalization.",
                duplicate_of=str(canonical.get("id", "")),
                details={"matched_keys": sorted(keys & record_keys(canonical))},
            )
            stats["duplicates_quarantined"] += 1
            continue
        index = len(kept)
        kept.append(record)
        for key in keys:
            key_to_index.setdefault(key, index)

    data["records"] = kept
    return stats


def create_pubmed_record(meta: dict[str, Any]) -> dict[str, Any]:
    pmid = str(meta.get("pmid", ""))
    doi = normalize_doi(str(meta.get("doi", "")))
    title = str(meta.get("title", ""))
    abstract = str(meta.get("abstract", ""))
    article_types = list(meta.get("article_types", []))
    mesh_terms = list(meta.get("mesh_terms", []))
    column, study_design, species, flags = pubmed_design(
        article_types, mesh_terms, title, abstract
    )
    row, population, platform = classify_domain(title, abstract, " ".join(mesh_terms))
    date_s = normalize_date(str(meta.get("date", ""))) or TODAY.isoformat()
    authors = "; ".join(meta.get("authors", []))
    finding = abstract.strip() or "Automated PubMed record."
    if len(finding) > 620:
        finding = finding[:600].rstrip() + " …"
    return {
        "id": f"AUTO-PUBMED-{pmid}",
        "matrix_row": row,
        "matrix_column": column,
        "year": int(date_s[:4]),
        "citation": f"PubMed {pmid}; auto-added",
        "title": title,
        "authors_source": authors or "PubMed",
        "study_design": study_design,
        "population": population,
        "product_platform": platform,
        "outcome_domain": "RSV prevention, immunization, protection, safety, policy, or implementation signal",
        "key_finding": finding,
        "evidence_signal": "Automated PubMed inclusion after RSV MeSH and article-type validation; not manually verified.",
        "actionability": "Automated evidence signal. Inspect the source before clinical, policy, or research decisions.",
        "source_url": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
        "tags": merge_semicolon_values(
            "auto-added",
            "PubMed",
            f"PMID {pmid}",
            f"DOI {doi}" if doi else "",
            f"QC {QC_VERSION}",
        ),
        "evidence_update_date": date_s,
        "evidence_update_precision": "day",
        "evidence_update_basis": "PubMed Entrez date; RSV-specific MeSH validated; design derived from PublicationType",
        "map_record_updated": TODAY.isoformat(),
        "provenance": "Automated",
        "pmid": pmid,
        "doi": doi,
        "article_types": article_types,
        "mesh_terms": mesh_terms,
        "species": species,
        "quality_status": "RSV MeSH and PubMed article-type validated",
        "quality_flags": flags,
        "quality_control_version": QC_VERSION,
    }


def create_generic_record(
    *,
    source_key: str,
    title: str,
    source_url: str,
    citation: str,
    date_s: str,
    abstract: str,
    authors: str,
    tags: str,
    source_basis: str,
    source_label: str,
    study_design: str,
    matrix_column: str | None = None,
    doi: str = "",
    pmid: str = "",
    quality_flags: list[str] | None = None,
) -> dict[str, Any]:
    date_s = normalize_date(date_s) or TODAY.isoformat()
    row, population, platform = classify_domain(title, abstract, source_label)
    column = matrix_column or classify_nonpubmed_column(
        f"{title} {abstract} {source_label} {study_design}"
    )
    finding = abstract.strip() or f"Automated {source_label} record."
    if len(finding) > 620:
        finding = finding[:600].rstrip() + " …"
    return {
        "id": source_key,
        "matrix_row": row,
        "matrix_column": column,
        "year": int(date_s[:4]),
        "citation": citation,
        "title": title or "(untitled automated record)",
        "authors_source": authors or source_label,
        "study_design": study_design,
        "population": population,
        "product_platform": platform,
        "outcome_domain": "RSV prevention, immunization, protection, safety, policy, or implementation signal",
        "key_finding": finding,
        "evidence_signal": f"Automated {source_label} inclusion; not manually verified.",
        "actionability": "Automated evidence signal. Inspect the source before clinical, policy, or research decisions.",
        "source_url": source_url,
        "tags": merge_semicolon_values(tags, f"QC {QC_VERSION}"),
        "evidence_update_date": date_s,
        "evidence_update_precision": "day",
        "evidence_update_basis": source_basis,
        "map_record_updated": TODAY.isoformat(),
        "provenance": "Automated",
        "pmid": pmid,
        "doi": normalize_doi(doi),
        "article_types": [],
        "mesh_terms": [],
        "species": "Not specified",
        "quality_status": "Source-specific automated validation; not manually verified",
        "quality_flags": quality_flags or [],
        "quality_control_version": QC_VERSION,
    }


def add_or_quarantine(
    data: dict[str, Any], record: dict[str, Any], quarantine: Quarantine
) -> tuple[bool, str]:
    if is_future_date(record_source_date(record)):
        quarantine.add(
            record,
            "future_dated",
            "Evidence source date/year is later than the workflow run date.",
        )
        return False, "future_dated"

    existing_key_map: dict[str, dict[str, Any]] = {}
    for existing in data.get("records", []):
        for key in record_keys(existing):
            existing_key_map.setdefault(key, existing)
    matches = [existing_key_map[key] for key in record_keys(record) if key in existing_key_map]
    if matches:
        canonical = max(matches, key=source_priority)
        quarantine.add(
            record,
            "duplicate",
            "Duplicate identifier/title detected before matrix insertion.",
            duplicate_of=str(canonical.get("id", "")),
            details={
                "matched_keys": sorted(record_keys(record) & record_keys(canonical))
            },
        )
        return False, "duplicate"
    data.setdefault("records", []).append(record)
    return True, "added"


def pubmed_source(data: dict[str, Any], quarantine: Quarantine) -> dict[str, Any]:
    pmids = search_pubmed_pmids()
    metadata = fetch_pubmed_metadata(pmids)
    candidates: list[dict[str, Any]] = []
    added = 0
    rejected_mesh = 0
    quarantined_future = 0
    for pmid in pmids:
        item = metadata.get(pmid)
        if not item:
            continue
        if not is_rsv_mesh_valid(item.get("mesh_terms", [])):
            placeholder = {
                "id": f"AUTO-PUBMED-{pmid}",
                "title": item.get("title", ""),
                "pmid": pmid,
                "doi": item.get("doi", ""),
                "source_url": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
                "evidence_update_date": item.get("date", ""),
            }
            quarantine.add(
                placeholder,
                "missing_rsv_mesh",
                "PubMed candidate did not contain an RSV-specific MeSH heading.",
                details={"mesh_terms": item.get("mesh_terms", [])},
            )
            rejected_mesh += 1
            continue
        record = create_pubmed_record(item)
        candidates.append(record)
        was_added, reason = add_or_quarantine(data, record, quarantine)
        added += int(was_added)
        quarantined_future += int(reason == "future_dated")
    (UPDATES_DIR / "pubmed_candidates.json").write_text(
        json.dumps(
            {
                "run_date": TODAY.isoformat(),
                "query": PUBMED_QUERY,
                "candidate_count": len(candidates),
                "added": added,
                "rejected_missing_rsv_mesh": rejected_mesh,
                "future_dated_quarantined": quarantined_future,
                "candidates": candidates,
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return {
        "source": "PubMed",
        "candidates": len(candidates),
        "added": added,
        "rejected_missing_rsv_mesh": rejected_mesh,
        "future_dated_quarantined": quarantined_future,
    }


def medrxiv_source(data: dict[str, Any], quarantine: Quarantine) -> dict[str, Any]:
    cursor = 0
    items: list[dict[str, Any]] = []
    while cursor < 1000:
        payload = safe_fetch(
            f"https://api.biorxiv.org/details/medrxiv/{START.isoformat()}/{TODAY.isoformat()}/{cursor}"
        )
        if not payload:
            break
        collection = json.loads(payload.decode("utf-8")).get("collection", [])
        if not collection:
            break
        items.extend(collection)
        if len(collection) < 100:
            break
        cursor += 100
        time.sleep(0.2)

    candidates: list[dict[str, Any]] = []
    added = 0
    off_topic = 0
    future = 0
    for item in items:
        title = str(item.get("title", ""))
        abstract = str(item.get("abstract", ""))
        text = f"{title} {abstract}"
        if not PREVENTION_TERMS.search(text):
            continue
        if not is_rsv_relevant(text):
            if is_resveratrol_abbreviation_only(text):
                off_topic += 1
            continue
        doi = normalize_doi(str(item.get("doi", "")))
        date_s = str(item.get("date") or TODAY.isoformat())
        record = create_generic_record(
            source_key=f"AUTO-MEDRXIV-{re.sub(r'[^A-Za-z0-9]+', '-', doi or title).strip('-')[:80]}",
            title=title,
            source_url=str(item.get("url") or (f"https://www.medrxiv.org/content/{doi}" if doi else "")),
            citation=f"{item.get('authors', 'medRxiv')}, medRxiv {date_s}; auto-added",
            date_s=date_s,
            abstract=abstract,
            authors=str(item.get("authors", "medRxiv")),
            tags=merge_semicolon_values("auto-added", "medRxiv", "preprint", f"DOI {doi}" if doi else ""),
            source_basis="medRxiv API preprint date within automated lookback window",
            source_label="medRxiv preprint",
            study_design="Preprint; study design algorithmically inferred from title/abstract and not independently validated",
            doi=doi,
            quality_flags=["preprint", "design_not_article_type_validated"],
        )
        candidates.append(record)
        was_added, reason = add_or_quarantine(data, record, quarantine)
        added += int(was_added)
        future += int(reason == "future_dated")
    (UPDATES_DIR / "medrxiv_candidates.json").write_text(
        json.dumps(
            {
                "run_date": TODAY.isoformat(),
                "candidate_count": len(candidates),
                "added": added,
                "off_topic_rejected": off_topic,
                "future_dated_quarantined": future,
                "candidates": candidates,
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return {
        "source": "medRxiv",
        "candidates": len(candidates),
        "added": added,
        "off_topic_rejected": off_topic,
        "future_dated_quarantined": future,
    }


def clinicaltrials_source(data: dict[str, Any], quarantine: Quarantine) -> dict[str, Any]:
    term = (
        "respiratory syncytial virus vaccine OR RSV vaccine OR nirsevimab OR "
        "clesrovimab OR palivizumab OR RSV booster OR RSV revaccination"
    )
    payload = safe_fetch(
        "https://clinicaltrials.gov/api/v2/studies?"
        + urllib.parse.urlencode({"query.term": term, "format": "json", "pageSize": "100"})
    )
    candidates: list[dict[str, Any]] = []
    added = 0
    future = 0
    if payload:
        for study in json.loads(payload.decode("utf-8")).get("studies", []):
            protocol = study.get("protocolSection", {})
            ident = protocol.get("identificationModule", {})
            status = protocol.get("statusModule", {})
            description = protocol.get("descriptionModule", {})
            conditions = protocol.get("conditionsModule", {})
            arms = protocol.get("armsInterventionsModule", {})
            design = protocol.get("designModule", {})
            nct = str(ident.get("nctId", ""))
            title = str(ident.get("briefTitle") or ident.get("officialTitle") or nct)
            last = str(
                status.get("lastUpdatePostDateStruct", {}).get("date")
                or status.get("studyFirstPostDateStruct", {}).get("date")
                or TODAY.isoformat()
            )[:10]
            text = " ".join(
                [
                    title,
                    str(description.get("briefSummary", "")),
                    " ".join(conditions.get("conditions", [])),
                    json.dumps(arms)[:4000],
                ]
            )
            if not PREVENTION_TERMS.search(text) or not is_rsv_relevant(text):
                continue
            if normalize_date(last) and normalize_date(last) < START.isoformat():
                continue
            study_type = str(design.get("studyType", "Not specified"))
            phases = ", ".join(design.get("phases", []))
            design_info = design.get("designInfo", {})
            allocation = str(design_info.get("allocation", ""))
            structured_design = "; ".join(
                part
                for part in [
                    f"ClinicalTrials.gov study type: {study_type}",
                    f"phase: {phases}" if phases else "",
                    f"allocation: {allocation}" if allocation else "",
                ]
                if part
            )
            if study_type.upper() == "INTERVENTIONAL":
                matrix_column = "Human RCT / controlled trial"
            elif study_type.upper() == "OBSERVATIONAL":
                matrix_column = "Observational / real-world"
            else:
                matrix_column = "Observational / real-world"
            record = create_generic_record(
                source_key=f"AUTO-CTGOV-{nct}",
                title=title,
                source_url=f"https://clinicaltrials.gov/study/{nct}",
                citation=f"{nct}; ClinicalTrials.gov auto-added",
                date_s=last,
                abstract=str(description.get("briefSummary", "")),
                authors="ClinicalTrials.gov",
                tags=merge_semicolon_values("auto-added", "ClinicalTrials.gov", nct),
                source_basis="ClinicalTrials.gov last-update/posted date within automated lookback window",
                source_label="ClinicalTrials.gov",
                study_design=structured_design,
                matrix_column=matrix_column,
            )
            candidates.append(record)
            was_added, reason = add_or_quarantine(data, record, quarantine)
            added += int(was_added)
            future += int(reason == "future_dated")
    (UPDATES_DIR / "clinicaltrials_candidates.json").write_text(
        json.dumps(
            {
                "run_date": TODAY.isoformat(),
                "candidate_count": len(candidates),
                "added": added,
                "future_dated_quarantined": future,
                "candidates": candidates,
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return {
        "source": "ClinicalTrials.gov",
        "candidates": len(candidates),
        "added": added,
        "future_dated_quarantined": future,
    }


def crossref_source(data: dict[str, Any], quarantine: Quarantine) -> dict[str, Any]:
    mail = os.getenv("CROSSREF_MAILTO", "").strip()
    params: dict[str, str] = {
        "query.title": "respiratory syncytial virus vaccine booster revaccination nirsevimab maternal",
        "filter": f"from-created-date:{START.isoformat()},until-created-date:{TODAY.isoformat()}",
        "rows": "75",
        "select": "DOI,title,author,published-print,published-online,created,URL,container-title,abstract",
    }
    if mail:
        params["mailto"] = mail
    payload = safe_fetch("https://api.crossref.org/works?" + urllib.parse.urlencode(params))
    raw_items = json.loads(payload.decode("utf-8")).get("message", {}).get("items", []) if payload else []

    eligible: list[dict[str, Any]] = []
    off_topic = 0
    for item in raw_items:
        title = " ".join(item.get("title") or [])
        abstract = re.sub(r"<[^<]+?>", " ", str(item.get("abstract", "") or ""))
        text = f"{title} {abstract}"
        if not PREVENTION_TERMS.search(text) or not is_rsv_relevant(text):
            if is_resveratrol_abbreviation_only(text):
                off_topic += 1
            continue
        eligible.append(item)

    doi_to_pmid = crosswalk_dois_to_pubmed(
        item.get("DOI", "") for item in eligible if item.get("DOI")
    )
    crosswalk_meta = fetch_pubmed_metadata(doi_to_pmid.values())
    candidates: list[dict[str, Any]] = []
    added = 0
    future = 0
    crosswalked = 0

    for item in eligible:
        title = " ".join(item.get("title") or [])
        abstract = re.sub(r"<[^<]+?>", " ", str(item.get("abstract", "") or ""))
        doi = normalize_doi(str(item.get("DOI", "")))
        pmid = doi_to_pmid.get(doi, "")
        if pmid and pmid in crosswalk_meta:
            meta = crosswalk_meta[pmid]
            if not is_rsv_mesh_valid(meta.get("mesh_terms", [])):
                continue
            record = create_pubmed_record(meta)
            record["tags"] = merge_semicolon_values(record.get("tags", ""), "Crossref DOI crosswalk")
            record["evidence_update_basis"] += "; PMID↔DOI crosswalk from Crossref discovery"
            crosswalked += 1
        else:
            date_s = TODAY.isoformat()
            for key in ("published-online", "published-print", "created"):
                parts = item.get(key, {}).get("date-parts")
                if parts and parts[0]:
                    year = parts[0][0]
                    month = parts[0][1] if len(parts[0]) > 1 else 1
                    day = parts[0][2] if len(parts[0]) > 2 else 1
                    try:
                        date_s = date(year, month, day).isoformat()
                    except ValueError:
                        date_s = f"{year:04d}-01-01"
                    break
            authors = "; ".join(
                " ".join(filter(None, [author.get("given", ""), author.get("family", "")]))
                for author in item.get("author", [])[:12]
            )
            record = create_generic_record(
                source_key=f"AUTO-CROSSREF-{re.sub(r'[^A-Za-z0-9]+', '-', doi or title).strip('-')[:80]}",
                title=title,
                source_url=str(item.get("URL") or (f"https://doi.org/{doi}" if doi else "")),
                citation=f"Crossref DOI {doi}; auto-added",
                date_s=date_s,
                abstract=abstract,
                authors=authors or "Crossref",
                tags=merge_semicolon_values("auto-added", "Crossref", f"DOI {doi}" if doi else ""),
                source_basis="Crossref metadata created date within automated lookback window; PMID crosswalk checked",
                source_label="Crossref metadata",
                study_design="Crossref metadata record; study design not independently validated",
                doi=doi,
                quality_flags=["design_not_article_type_validated"],
            )
        candidates.append(record)
        was_added, reason = add_or_quarantine(data, record, quarantine)
        added += int(was_added)
        future += int(reason == "future_dated")

    (UPDATES_DIR / "crossref_candidates.json").write_text(
        json.dumps(
            {
                "run_date": TODAY.isoformat(),
                "candidate_count": len(candidates),
                "added": added,
                "pmid_doi_crosswalked": crosswalked,
                "off_topic_rejected": off_topic,
                "future_dated_quarantined": future,
                "candidates": candidates,
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return {
        "source": "Crossref",
        "candidates": len(candidates),
        "added": added,
        "pmid_doi_crosswalked": crosswalked,
        "off_topic_rejected": off_topic,
        "future_dated_quarantined": future,
    }


def guidance_source(data: dict[str, Any], quarantine: Quarantine) -> dict[str, Any]:
    watch_path = UPDATES_DIR / "guidance_page_watchlist.json"
    old: dict[str, Any] = {}
    if watch_path.exists():
        try:
            old = json.loads(watch_path.read_text(encoding="utf-8")).get("pages", {})
        except Exception:
            old = {}
    pages: dict[str, Any] = {}
    changes: list[dict[str, Any]] = []
    added = 0
    for page in GUIDANCE_PAGES:
        payload = safe_fetch(page["url"])
        digest = hashlib.sha256(payload or b"").hexdigest() if payload else ""
        previous = old.get(page["url"], {}).get("sha256") if isinstance(old.get(page["url"]), dict) else None
        pages[page["url"]] = {
            "name": page["name"],
            "source_group": page["source_group"],
            "sha256": digest,
            "checked_date": TODAY.isoformat(),
            "status": "fetched" if payload else "fetch_failed",
        }
        if previous and digest and previous != digest:
            record = create_generic_record(
                source_key="AUTO-GUIDANCE-"
                + hashlib.sha1(
                    f"{page['source_group']}|{page['url']}|{TODAY.isoformat()}".encode()
                ).hexdigest()[:16],
                title=f"Official RSV guidance/regulatory page changed: {page['name']}",
                source_url=page["url"],
                citation=f"{page['source_group']} page-change signal; auto-added",
                date_s=TODAY.isoformat(),
                abstract="Automated page monitoring detected a content-hash change on an official RSV guidance/regulatory page. Inspect the page to interpret the substantive change.",
                authors=page["source_group"],
                tags=merge_semicolon_values("auto-added", "guidance-change", page["source_group"]),
                source_basis="Official-page content hash changed since previous automated check",
                source_label=f"{page['source_group']} guidance",
                study_design="Official guidance/regulatory page-change signal",
                matrix_column="Evidence synthesis / guidance",
            )
            changes.append(record)
            was_added, _ = add_or_quarantine(data, record, quarantine)
            added += int(was_added)
    watch_path.write_text(
        json.dumps(
            {"run_date": TODAY.isoformat(), "pages": pages},
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return {
        "source": "Guidance/regulatory watchlist",
        "candidates": len(GUIDANCE_PAGES),
        "changes": len(changes),
        "added": added,
    }


def update_method_sources(data: dict[str, Any]) -> None:
    sources = data.get("method_sources")
    if not isinstance(sources, list):
        return
    for source in sources:
        name = str(source.get("name", "")).lower()
        if "pubmed" in name:
            source["automation"] = (
                "Weekly; direct inclusion only after RSV-specific MeSH validation, PMID↔DOI de-duplication, "
                "PublicationType-based study-design coding, and future-date screening."
            )
        elif "crossref" in name:
            source["automation"] = (
                "Weekly discovery; DOI is cross-walked to PMID before matrix insertion and duplicate identifiers are rejected."
            )
        elif "medrxiv" in name:
            source["automation"] = (
                "Weekly; abbreviation-only resveratrol hits are excluded, DOI duplicates are rejected, and future dates are quarantined."
            )
        elif "clinicaltrials" in name:
            source["automation"] = (
                "Weekly; study design is taken from structured registry fields, with NCT de-duplication and future-date quarantine."
            )


def main() -> None:
    UPDATES_DIR.mkdir(exist_ok=True)
    data = load_data()
    data.setdefault("records", [])
    rows_changed = ensure_rows(data)
    quarantine = Quarantine(QUARANTINE_PATH)

    pre_count = len(data.get("records", []))
    repair_stats = repair_existing_records(data, quarantine)
    post_repair_count = len(data.get("records", []))

    results: list[dict[str, Any]] = []
    for source_function in (
        pubmed_source,
        medrxiv_source,
        clinicaltrials_source,
        crossref_source,
        guidance_source,
    ):
        try:
            result = source_function(data, quarantine)
        except Exception as exc:
            print(f"ERROR: {source_function.__name__}: {exc}")
            result = {
                "source": source_function.__name__,
                "error": str(exc),
                "added": 0,
                "candidates": 0,
            }
        results.append(result)

    quarantine.write()
    update_method_sources(data)

    added_total = sum(int(result.get("added", 0)) for result in results)
    removed_total = pre_count - post_repair_count
    changed_total = (
        added_total
        + max(removed_total, 0)
        + repair_stats["pubmed_reclassified"]
        + int(rows_changed)
    )

    records = data.get("records", [])
    curated_count = sum(1 for record in records if not is_auto_record(record))
    auto_count = len(records) - curated_count
    quality_counts = quarantine.counts()

    metadata = data.setdefault("metadata", {})
    metadata["generated_date"] = TODAY.isoformat()
    metadata["as_of_date"] = TODAY.isoformat()
    metadata["last_surveillance_run"] = TODAY.isoformat()
    metadata["last_auto_inclusion_run"] = TODAY.isoformat()
    metadata["records_auto_added_last_run"] = added_total
    metadata["candidate_records_found_last_run"] = sum(
        int(result.get("candidates", 0))
        for result in results
        if str(result.get("candidates", 0)).isdigit()
    )
    if changed_total:
        metadata["last_included_record_update"] = TODAY.isoformat()
    metadata["surveillance_cadence"] = (
        "Weekly automated surveillance using GitHub Actions across PubMed, medRxiv, "
        "ClinicalTrials.gov, Crossref, and selected official guidance/regulatory pages."
    )
    metadata["status_note"] = (
        "Auto-added records remain not manually verified. The pipeline applies RSV-specific relevance checks, "
        "PMID↔DOI de-duplication, PubMed PublicationType-based study-design coding, and future-date quarantine."
    )
    metadata["public_summary"] = (
        "Rapid RSV evidence map: records are coded into a matrix by RSV evidence domain and evidence type. "
        "The seed map is source-backed; newly detected PubMed, medRxiv, ClinicalTrials.gov, CDC/ACIP, FDA, "
        "WHO, EMA, PHAC/NACI and UKHSA/JCVI records may be added automatically and labelled as not manually verified."
    )
    metadata["quality_control"] = {
        "version": QC_VERSION,
        "last_run": TODAY.isoformat(),
        "audit_snapshot": {
            "curated_seed_records": 48,
            "auto_added_records": 288,
            "off_topic_records_minimum": 4,
            "duplicated_records_approximate": 31,
            "miscategorized_records_minimum": 20,
            "note": "Counts describe the pre-fix audit snapshot supplied for this dashboard update.",
        },
        "active_curated_records": curated_count,
        "active_auto_records": auto_count,
        "quarantined_records_total": sum(quality_counts.values()),
        "quarantine_category_counts": quality_counts,
        "repairs_this_run": repair_stats,
        "safeguards": [
            "RSV-specific PubMed MeSH validation",
            "resveratrol abbreviation-only exclusion",
            "PMID↔DOI crosswalk and duplicate rejection",
            "PubMed PublicationType-based study-design coding",
            "future-date quarantine",
        ],
    }

    save_data(data)

    report = {
        "quality_control_version": QC_VERSION,
        "run_date": TODAY.isoformat(),
        "pre_run_active_records": pre_count,
        "post_repair_active_records": post_repair_count,
        "final_active_records": len(records),
        "active_curated_records": curated_count,
        "active_auto_records": auto_count,
        "rows_changed": rows_changed,
        "repair_stats": repair_stats,
        "quarantine_category_counts": quality_counts,
        "new_records_added": added_total,
        "source_results": results,
    }
    QC_REPORT_PATH.write_text(
        json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    (UPDATES_DIR / "surveillance_status.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(json.dumps(report, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
