#!/usr/bin/env python3
from __future__ import annotations
import json, sys
from pathlib import Path

index = Path('index.html')
data_path = Path('data/rsv_evidence_map_data_v0_2.json')
if not index.exists(): raise SystemExit('index.html is missing')
html = index.read_text(encoding='utf-8', errors='ignore')
checks = ['<!doctype html>', 'DATA_URL', 'Evidence matrix', 'Evidence records', 'Sources searched']
missing = [x for x in checks if x not in html]
if missing: raise SystemExit('index.html failed validation; missing: ' + ', '.join(missing))
if html.lstrip().startswith('#'):
    raise SystemExit('index.html is Markdown/plain text, not the dashboard HTML')
if not data_path.exists(): raise SystemExit(str(data_path) + ' is missing')
data = json.loads(data_path.read_text(encoding='utf-8'))
records = data.get('records')
if not isinstance(records, list) or len(records) == 0:
    raise SystemExit('data file has no records')
for key in ['rows','columns','method_sources']:
    if key not in data: raise SystemExit(f'data file missing {key}')
if 'Booster studies' not in data.get('rows', []):
    raise SystemExit('data file is missing the Booster studies evidence-domain row')
print(f'Site validation passed: {len(records)} evidence records available.')
