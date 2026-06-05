#!/usr/bin/env python3
"""Automated RSV evidence-map source surveillance.

Source of truth: data/rsv_evidence_map_data_v0_2.json
This script appends non-duplicate, rule-classified records from PubMed,
medRxiv, ClinicalTrials.gov, Crossref, and selected official guidance pages.
It never rewrites index.html.
"""
from __future__ import annotations
import csv, hashlib, json, os, re, sys, time, urllib.parse, urllib.request, xml.etree.ElementTree as ET
from datetime import date, timedelta
from pathlib import Path
from typing import Any

DATA_PATH = Path('data/rsv_evidence_map_data_v0_2.json')
CSV_PATH = Path('data/rsv_evidence_map_records_v0_2.csv')
UPDATES_DIR = Path('updates')
LOOKBACK_DAYS = int(os.getenv('LOOKBACK_DAYS', '14'))
TODAY = date.today()
START = TODAY - timedelta(days=LOOKBACK_DAYS)

FIELDS = ['id','matrix_row','matrix_column','year','citation','title','authors_source','study_design','population','product_platform','outcome_domain','key_finding','evidence_signal','actionability','source_url','tags','evidence_update_date','evidence_update_precision','evidence_update_basis','map_record_updated']
ROWS = ['Immune correlates & antigen design','Adult active vaccination','Maternal vaccination & infant protection','Infant monoclonal antibodies','Pediatric active-vaccine development & safety','Policy, implementation & real-world evidence']
COLS = ['Evidence synthesis / guidance','Human RCT / controlled trial','Observational / real-world','Translational / preclinical','Safety signal / surveillance']

PUBMED_QUERY = '("Respiratory Syncytial Virus Infections"[Mesh] OR "respiratory syncytial virus"[tiab] OR RSV[tiab]) AND (vaccin*[tiab] OR immunization[tiab] OR immunisation[tiab] OR nirsevimab[tiab] OR clesrovimab[tiab] OR palivizumab[tiab] OR monoclonal[tiab] OR "prefusion F"[tiab] OR maternal[tiab] OR pregnan*[tiab] OR adult*[tiab] OR infant*[tiab] OR safety[tiab] OR effectiveness[tiab] OR efficacy[tiab] OR correlate*[tiab])'
PREVENTION_TERMS = re.compile(r'\b(vaccin|immuni[sz]ation|nirsevimab|clesrovimab|palivizumab|monoclonal|prefusion|maternal|pregnan|adult|infant|safety|efficacy|effectiveness|correlate|antibody|prophylaxis|prevention)\b', re.I)
RSV_TERMS = re.compile(r'\b(respiratory syncytial virus|\brsv\b)\b', re.I)

GUIDANCE_PAGES = [
    {'name':'CDC adult RSV vaccine clinical guidance','url':'https://www.cdc.gov/rsv/hcp/vaccine-clinical-guidance/adults.html','source_group':'CDC / ACIP'},
    {'name':'CDC RSV immunization guidance for infants and young children','url':'https://www.cdc.gov/rsv/hcp/vaccine-clinical-guidance/infants-young-children.html','source_group':'CDC / ACIP'},
    {'name':'CDC RSV vaccine guidance during pregnancy','url':'https://www.cdc.gov/rsv/hcp/vaccine-clinical-guidance/pregnant-people.html','source_group':'CDC / ACIP'},
    {'name':'CDC ACIP RSV recommendations','url':'https://www.cdc.gov/acip/vaccine-recommendations/index.html','source_group':'CDC / ACIP'},
    {'name':'FDA respiratory syncytial virus vaccines','url':'https://www.fda.gov/vaccines-blood-biologics/vaccines/respiratory-syncytial-virus-rsv','source_group':'FDA'},
    {'name':'FDA approved RSV products search','url':'https://www.fda.gov/vaccines-blood-biologics/vaccines','source_group':'FDA'},
    {'name':'WHO respiratory syncytial virus fact sheet','url':'https://www.who.int/news-room/fact-sheets/detail/respiratory-syncytial-virus-(rsv)','source_group':'WHO'},
    {'name':'WHO RSV infant immunization position paper','url':'https://www.who.int/publications/i/item/who-wer10024-277-300','source_group':'WHO'},
    {'name':'EMA Arexvy','url':'https://www.ema.europa.eu/en/medicines/human/EPAR/arexvy','source_group':'EMA'},
    {'name':'EMA Abrysvo','url':'https://www.ema.europa.eu/en/medicines/human/EPAR/abrysvo','source_group':'EMA'},
    {'name':'EMA Beyfortus','url':'https://www.ema.europa.eu/en/medicines/human/EPAR/beyfortus','source_group':'EMA'},
    {'name':'PHAC RSV vaccines and monoclonal antibodies','url':'https://www.canada.ca/en/public-health/services/immunization-vaccines/vaccination-respiratory-syncytial-virus.html','source_group':'PHAC / NACI'},
    {'name':'NACI RSV statement collection','url':'https://www.canada.ca/en/public-health/services/immunization/national-advisory-committee-on-immunization-naci.html','source_group':'PHAC / NACI'},
    {'name':'UKHSA RSV vaccination programme','url':'https://www.gov.uk/government/collections/respiratory-syncytial-virus-rsv-vaccination-programme','source_group':'UKHSA / JCVI'},
    {'name':'JCVI statements and advice','url':'https://www.gov.uk/government/groups/joint-committee-on-vaccination-and-immunisation','source_group':'UKHSA / JCVI'},
]

def fetch(url: str, timeout: int = 25) -> bytes:
    req = urllib.request.Request(url, headers={'User-Agent':'rsv-evidence-map/1.0 (public GitHub Pages evidence map)'})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()

def safe_fetch(url: str) -> bytes | None:
    try:
        return fetch(url)
    except Exception as e:
        print(f'WARN: fetch failed: {url}: {e}')
        return None

def load_data() -> dict[str, Any]:
    if not DATA_PATH.exists():
        raise SystemExit(f'Missing {DATA_PATH}')
    return json.loads(DATA_PATH.read_text(encoding='utf-8'))

def save_data(data: dict[str, Any]) -> None:
    md = data.setdefault('metadata', {})
    md['generated_date'] = TODAY.isoformat()
    md['as_of_date'] = TODAY.isoformat()
    md['last_surveillance_run'] = TODAY.isoformat()
    md['surveillance_cadence'] = 'Weekly automated surveillance using GitHub Actions across PubMed, medRxiv, ClinicalTrials.gov, Crossref, and selected official guidance/regulatory pages.'
    md['status_note'] = 'Records may be automatically ingested and algorithmically classified; auto-added records are not manually verified.'
    DATA_PATH.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding='utf-8')
    with CSV_PATH.open('w', newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        for r in data.get('records', []):
            w.writerow({k: r.get(k, '') for k in FIELDS})

def normalize_id_piece(s: str) -> str:
    return re.sub(r'[^A-Za-z0-9]+','-',s).strip('-')[:80] or hashlib.sha1(s.encode()).hexdigest()[:12]

def existing_keys(data: dict[str, Any]) -> set[str]:
    keys = set()
    for r in data.get('records', []):
        for val in [r.get('id',''), r.get('source_url','')]:
            if val: keys.add(str(val).strip().lower())
        txt = ' '.join([str(r.get('source_url','')), str(r.get('tags','')), str(r.get('citation','')), str(r.get('title',''))])
        for m in re.findall(r'10\.\d{4,9}/[^\s;,)]+', txt, flags=re.I): keys.add(m.lower().rstrip('.'))
        for m in re.findall(r'pubmed[_: ]?(\d+)|ncbi\.nlm\.nih\.gov/(\d+)', txt, flags=re.I):
            pmid = next((x for x in m if x), '')
            if pmid: keys.add('pmid:'+pmid)
    return keys

def classify(title: str, abstract: str = '', source: str = '') -> tuple[str, str, str, str, str]:
    text = f'{title} {abstract} {source}'.lower()
    if re.search(r'maternal|pregnan|birth|antenatal', text): row='Maternal vaccination & infant protection'; pop='Pregnant people / infants'; platform='Maternal RSV vaccine / passive infant protection'
    elif re.search(r'nirsevimab|clesrovimab|palivizumab|monoclonal|beyfortus', text): row='Infant monoclonal antibodies'; pop='Infants / young children'; platform='Long-acting monoclonal antibody'
    elif re.search(r'older adult|adult|aged|elderly|arexvy|abrysvo|mresvia|mrna-1345|rsvpref3', text): row='Adult active vaccination'; pop='Adults / older adults'; platform='Adult RSV vaccine'
    elif re.search(r'pediatric|paediatric|children|child|toddler', text) and re.search(r'vaccine|trial|safety|enhanced', text): row='Pediatric active-vaccine development & safety'; pop='Children / pediatric populations'; platform='Pediatric RSV vaccine candidate'
    elif re.search(r'correlate|antigen|prefusion|neutraliz|immunogenic|antibody|epitope', text): row='Immune correlates & antigen design'; pop='Not population-specific / immunologic evidence'; platform='RSV F antigen / immune correlate'
    else: row='Policy, implementation & real-world evidence'; pop='Mixed / policy-relevant populations'; platform='RSV prevention product/platform not auto-specified'
    if re.search(r'guideline|recommendation|position paper|statement|systematic review|meta-analysis|review|policy|advisory|regulatory|crossref metadata|official guidance', text): col='Evidence synthesis / guidance'
    elif re.search(r'random|phase\s*[123]|trial|controlled|efficacy', text): col='Human RCT / controlled trial'
    elif re.search(r'real[- ]world|effectiveness|cohort|case-control|surveillance|registry|observational|uptake|implementation', text): col='Observational / real-world'
    elif re.search(r'preclinical|mouse|mice|animal|in vitro|antigen|immunogenic|correlate|neutraliz', text): col='Translational / preclinical'
    elif re.search(r'safety|adverse|signal|preterm|guillain|surveillance', text): col='Safety signal / surveillance'
    else: col='Evidence synthesis / guidance'
    outcome = 'RSV prevention, immunization, protection, safety, policy, or implementation signal'
    design = 'Automated source record; rule-classified'
    return row, col, pop, platform, outcome, design

def make_record(source_key: str, title: str, source_url: str, citation: str, date_s: str, abstract: str, authors: str, tags: str, source_basis: str, source_label: str) -> dict[str, Any]:
    row,col,pop,platform,outcome,design = classify(title, abstract, source_label)
    year = int(date_s[:4]) if re.match(r'\d{4}', date_s or '') else TODAY.year
    key_find = (abstract or f'Automated {source_label} record.').strip()
    if len(key_find) > 620: key_find = key_find[:600].rstrip() + ' …'
    return {
        'id': source_key,
        'matrix_row': row,
        'matrix_column': col,
        'year': year,
        'citation': citation or f'{source_label} auto-added, {year}',
        'title': title or '(untitled automated record)',
        'authors_source': authors or source_label,
        'study_design': design,
        'population': pop,
        'product_platform': platform,
        'outcome_domain': outcome,
        'key_finding': key_find,
        'evidence_signal': f'Automated {source_label} inclusion; not manually verified.',
        'actionability': 'Rule-classified automated evidence signal. Inspect the source record before clinical, policy, or research decisions.',
        'source_url': source_url,
        'tags': tags,
        'evidence_update_date': date_s or TODAY.isoformat(),
        'evidence_update_precision': 'day' if re.match(r'\d{4}-\d{2}-\d{2}', date_s or '') else 'unknown',
        'evidence_update_basis': source_basis,
        'map_record_updated': TODAY.isoformat(),
    }

def append_if_new(data, rec, keys) -> bool:
    candidates = [rec.get('id','').lower(), rec.get('source_url','').lower()]
    candidates += ['doi:'+m.lower().rstrip('.') for m in re.findall(r'10\.\d{4,9}/[^\s;,)]+', rec.get('source_url','')+' '+rec.get('tags',''), flags=re.I)]
    if any(c and c in keys for c in candidates): return False
    data.setdefault('records', []).append(rec)
    for c in candidates:
        if c: keys.add(c)
    return True

def text_el(el): return ''.join(el.itertext()).strip() if el is not None else ''

def pubmed(data, keys):
    out=[]; added=0
    params={'db':'pubmed','term':PUBMED_QUERY,'retmode':'json','retmax':'100','sort':'pub_date','datetype':'edat','mindate':START.isoformat(),'maxdate':TODAY.isoformat(),'tool':'rsv_evidence_map'}
    b=safe_fetch('https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi?'+urllib.parse.urlencode(params))
    if not b: return {'source':'PubMed','candidates':0,'added':0,'error':'search failed'}
    ids=json.loads(b.decode()).get('esearchresult',{}).get('idlist',[])
    if ids:
        b=safe_fetch('https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi?'+urllib.parse.urlencode({'db':'pubmed','id':','.join(ids),'retmode':'xml','tool':'rsv_evidence_map'}))
        if b:
            root=ET.fromstring(b)
            for art in root.findall('.//PubmedArticle'):
                pmid=art.findtext('.//PMID') or ''
                title=text_el(art.find('.//ArticleTitle'))
                abst=' '.join(text_el(x) for x in art.findall('.//AbstractText') if text_el(x))
                authors=[]
                for a in art.findall('.//Author')[:6]:
                    authors.append(a.findtext('CollectiveName') or ' '.join(filter(None,[a.findtext('ForeName'), a.findtext('LastName')])) )
                year=art.findtext('.//JournalIssue/PubDate/Year') or str(TODAY.year)
                month=art.findtext('.//JournalIssue/PubDate/Month') or '01'
                day=art.findtext('.//JournalIssue/PubDate/Day') or '01'
                date_s=f'{year}-{month[:2] if month.isdigit() else "01"}-{day.zfill(2)[:2]}'
                rec=make_record('AUTO-PUBMED-'+pmid, title, f'https://pubmed.ncbi.nlm.nih.gov/{pmid}/', f'PubMed {pmid}; auto-added', date_s, abst, '; '.join([x for x in authors if x]), f'auto-added; PubMed; PMID {pmid}', 'PubMed Entrez date within automated lookback window', 'PubMed')
                out.append(rec)
                if append_if_new(data, rec, keys): added+=1
    (UPDATES_DIR/'pubmed_candidates.json').write_text(json.dumps({'run_date':TODAY.isoformat(),'candidate_count':len(out),'candidates':out}, indent=2, ensure_ascii=False), encoding='utf-8')
    return {'source':'PubMed','candidates':len(out),'added':added}

def medrxiv(data, keys):
    cursor=0; all_items=[]; added=0
    while cursor < 1000:
        url=f'https://api.biorxiv.org/details/medrxiv/{START.isoformat()}/{TODAY.isoformat()}/{cursor}'
        b=safe_fetch(url)
        if not b: break
        js=json.loads(b.decode())
        coll=js.get('collection',[])
        if not coll: break
        all_items.extend(coll)
        if len(coll)<100: break
        cursor += 100
        time.sleep(0.2)
    candidates=[]
    for item in all_items:
        title=item.get('title',''); abst=item.get('abstract','')
        if not RSV_TERMS.search(title+' '+abst) or not PREVENTION_TERMS.search(title+' '+abst): continue
        doi=item.get('doi','')
        date_s=item.get('date') or TODAY.isoformat()
        rec=make_record('AUTO-MEDRXIV-'+normalize_id_piece(doi or title), title, item.get('url') or (f'https://www.medrxiv.org/content/{doi}v{item.get("version","1")}' if doi else ''), f'{item.get("authors","medRxiv")}, medRxiv {date_s}; auto-added', date_s, abst, item.get('authors','medRxiv'), f'auto-added; medRxiv; preprint; DOI {doi}', 'medRxiv API preprint date within automated lookback window', 'medRxiv preprint')
        candidates.append(rec)
        if append_if_new(data, rec, keys): added+=1
    (UPDATES_DIR/'medrxiv_candidates.json').write_text(json.dumps({'run_date':TODAY.isoformat(),'candidate_count':len(candidates),'candidates':candidates}, indent=2, ensure_ascii=False), encoding='utf-8')
    return {'source':'medRxiv','candidates':len(candidates),'added':added}

def clinicaltrials(data, keys):
    term='RSV OR respiratory syncytial virus vaccine OR nirsevimab OR clesrovimab OR palivizumab'
    url='https://clinicaltrials.gov/api/v2/studies?'+urllib.parse.urlencode({'query.term':term,'format':'json','pageSize':'100'})
    b=safe_fetch(url); candidates=[]; added=0
    if b:
        js=json.loads(b.decode())
        for st in js.get('studies',[]):
            proto=st.get('protocolSection',{}); ident=proto.get('identificationModule',{}); status=proto.get('statusModule',{}); desc=proto.get('descriptionModule',{}); cond=proto.get('conditionsModule',{}); arms=proto.get('armsInterventionsModule',{})
            nct=ident.get('nctId',''); title=ident.get('briefTitle') or ident.get('officialTitle') or nct
            last=status.get('lastUpdatePostDateStruct',{}).get('date') or status.get('studyFirstPostDateStruct',{}).get('date') or TODAY.isoformat()
            text=' '.join([title, desc.get('briefSummary',''), ' '.join(cond.get('conditions',[])), json.dumps(arms)[:2000]])
            if not RSV_TERMS.search(text) or not PREVENTION_TERMS.search(text): continue
            if last[:10] < START.isoformat(): continue
            rec=make_record('AUTO-CTGOV-'+nct, title, f'https://clinicaltrials.gov/study/{nct}', f'{nct}; ClinicalTrials.gov auto-added', last[:10], desc.get('briefSummary',''), 'ClinicalTrials.gov', f'auto-added; ClinicalTrials.gov; {nct}', 'ClinicalTrials.gov last-update/posted date within automated lookback window', 'ClinicalTrials.gov')
            candidates.append(rec)
            if append_if_new(data, rec, keys): added+=1
    (UPDATES_DIR/'clinicaltrials_candidates.json').write_text(json.dumps({'run_date':TODAY.isoformat(),'candidate_count':len(candidates),'candidates':candidates}, indent=2, ensure_ascii=False), encoding='utf-8')
    return {'source':'ClinicalTrials.gov','candidates':len(candidates),'added':added}

def crossref(data, keys):
    mail=os.getenv('CROSSREF_MAILTO','').strip()
    params={'query.title':'respiratory syncytial virus RSV vaccine nirsevimab maternal','filter':f'from-created-date:{START.isoformat()},until-created-date:{TODAY.isoformat()}','rows':'50','select':'DOI,title,author,published-print,published-online,created,URL,container-title,abstract'}
    if mail: params['mailto']=mail
    b=safe_fetch('https://api.crossref.org/works?'+urllib.parse.urlencode(params)); candidates=[]; added=0
    if b:
        js=json.loads(b.decode())
        for item in js.get('message',{}).get('items',[]):
            title=' '.join(item.get('title') or [])
            abst=re.sub('<[^<]+?>',' ',item.get('abstract','') or '')
            if not RSV_TERMS.search(title+' '+abst) or not PREVENTION_TERMS.search(title+' '+abst): continue
            doi=item.get('DOI','')
            date_s=TODAY.isoformat()
            for key in ['published-online','published-print','created']:
                parts=item.get(key,{}).get('date-parts')
                if parts and parts[0]:
                    y=parts[0][0]; m=parts[0][1] if len(parts[0])>1 else 1; d=parts[0][2] if len(parts[0])>2 else 1
                    date_s=f'{y:04d}-{m:02d}-{d:02d}'; break
            authors=[]
            for a in item.get('author',[])[:6]: authors.append(' '.join(filter(None,[a.get('given',''),a.get('family','')])) )
            rec=make_record('AUTO-CROSSREF-'+normalize_id_piece(doi or title), title, item.get('URL') or (f'https://doi.org/{doi}' if doi else ''), f'Crossref DOI {doi}; auto-added', date_s, abst, '; '.join([x for x in authors if x]), f'auto-added; Crossref; DOI {doi}', 'Crossref metadata created date within automated lookback window', 'Crossref metadata')
            candidates.append(rec)
            if append_if_new(data, rec, keys): added+=1
    (UPDATES_DIR/'crossref_candidates.json').write_text(json.dumps({'run_date':TODAY.isoformat(),'candidate_count':len(candidates),'candidates':candidates}, indent=2, ensure_ascii=False), encoding='utf-8')
    return {'source':'Crossref','candidates':len(candidates),'added':added}

def guidance(data, keys):
    watch_path=UPDATES_DIR/'guidance_page_watchlist.json'
    old={}
    if watch_path.exists():
        try: old=json.loads(watch_path.read_text(encoding='utf-8')).get('pages',{})
        except Exception: old={}
    pages={}; changes=[]; added=0
    for p in GUIDANCE_PAGES:
        b=safe_fetch(p['url'])
        h=hashlib.sha256(b or b'').hexdigest() if b else ''
        prev=old.get(p['url'],{}).get('sha256') if isinstance(old.get(p['url']),dict) else None
        pages[p['url']]={'name':p['name'],'source_group':p['source_group'],'sha256':h,'checked_date':TODAY.isoformat(),'status':'fetched' if b else 'fetch_failed'}
        if prev and h and prev != h:
            title=f'Official RSV guidance/regulatory page changed: {p["name"]}'
            rec=make_record('AUTO-GUIDANCE-'+normalize_id_piece(p['source_group']+' '+p['url']+' '+TODAY.isoformat()), title, p['url'], f'{p["source_group"]} page-change signal; auto-added', TODAY.isoformat(), 'Automated page-monitoring detected a content-hash change on an official RSV guidance/regulatory page. Inspect the page to interpret the substantive change.', p['source_group'], f'auto-added; guidance-change; {p["source_group"]}', 'Official-page content hash changed since previous automated check', p['source_group']+' guidance')
            changes.append(rec)
            if append_if_new(data, rec, keys): added+=1
    watch_path.write_text(json.dumps({'run_date':TODAY.isoformat(),'pages':pages}, indent=2, ensure_ascii=False), encoding='utf-8')
    return {'source':'Guidance/regulatory watchlist','candidates':len(GUIDANCE_PAGES),'changes':len(changes),'added':added}

def main():
    UPDATES_DIR.mkdir(exist_ok=True)
    data=load_data(); data.setdefault('rows', ROWS); data.setdefault('columns', COLS); data.setdefault('records', [])
    keys=existing_keys(data)
    results=[]
    for fn in [pubmed, medrxiv, clinicaltrials, crossref, guidance]:
        try:
            res=fn(data, keys)
        except Exception as e:
            res={'source':fn.__name__,'error':str(e),'added':0,'candidates':0}
            print('ERROR:', fn.__name__, e)
        results.append(res)
    added_total=sum(int(r.get('added',0)) for r in results)
    md=data.setdefault('metadata',{})
    md['last_auto_inclusion_run']=TODAY.isoformat(); md['records_auto_added_last_run']=added_total
    if added_total:
        md['last_included_record_update']=TODAY.isoformat()
    md['candidate_records_found_last_run']=sum(int(r.get('candidates',0)) for r in results if str(r.get('candidates',0)).isdigit())
    save_data(data)
    (UPDATES_DIR/'surveillance_status.json').write_text(json.dumps({'run_date':TODAY.isoformat(),'lookback_days':LOOKBACK_DAYS,'added_total':added_total,'source_results':results}, indent=2, ensure_ascii=False), encoding='utf-8')
    print(json.dumps({'added_total':added_total,'source_results':results}, indent=2))

if __name__ == '__main__':
    main()
