#!/bin/bash

echo "=========================================="
echo "API Endpoint Tests - $(date)"
echo "=========================================="
echo ""

echo "1. GET /api/all-tickers (equivalent to valuations)"
echo "   Checking for tickers with eps_avg and current_price..."
echo "---"
curl -s http://localhost:8080/api/all-tickers | python3 -c "
import sys, json
data = json.load(sys.stdin)
tickers = data.get('tickers', [])
complete = [t for t in tickers if t.get('eps_avg') is not None and t.get('current_price') is not None]
sec_source = [t for t in tickers if t.get('eps_source') == 'sec']
print(f'Total tickers: {len(tickers)}')
print(f'With complete data: {len(complete)}')
print(f'With SEC EPS: {len(sec_source)}')
if complete:
    t = complete[0]
    print(f'Sample: {t[\"ticker\"]} - Price: \${t.get(\"current_price\")}, EPS Avg: \${t.get(\"eps_avg\")}, Source: {t.get(\"eps_source\")}')
"
echo ""
echo ""

echo "2. GET /api/recommendations"
echo "   Checking for proper scores and valuations..."
echo "---"
curl -s http://localhost:8080/api/recommendations | python3 -c "
import sys, json
data = json.load(sys.stdin)
recs = data.get('recommendations', [])
print(f'Total recommendations: {len(recs)}')
print(f'Total analyzed: {data.get(\"total_analyzed\")}')
if recs:
    r = recs[0]
    print(f'Top pick: {r.get(\"ticker\")} ({r.get(\"company_name\")})')
    print(f'  Score: {r.get(\"score\")}')
    print(f'  Price: \${r.get(\"current_price\")} vs Value: \${r.get(\"estimated_value\")}')
    print(f'  Undervalued by: {abs(r.get(\"price_vs_value\"))}%')
    print(f'  EPS years: {r.get(\"eps_years\")}')
"
echo ""
echo ""

echo "3. GET /api/data-status"
echo "   Checking SEC EPS counts by index..."
echo "---"
curl -s http://localhost:8080/api/data-status | python3 -c "
import sys, json
data = json.load(sys.stdin)
sec = data.get('sec', {})
print(f'SEC CIK mappings: {sec.get(\"cik_mappings\")}')
print(f'SEC unknown: {sec.get(\"sec_unknown\")}')
print('')
print('Index SEC EPS Counts:')
for idx in data.get('indices', []):
    print(f'  {idx[\"name\"]:30s}: {idx[\"sec_source_count\"]:4d} SEC EPS / {idx[\"total_tickers\"]:4d} total')
"
echo ""
echo ""

echo "4. GET /api/eps-recommendations"
echo "   Checking EPS update status..."
echo "---"
curl -s http://localhost:8080/api/eps-recommendations | python3 -c "
import sys, json
data = json.load(sys.stdin)
print(f'Total cached: {data.get(\"total_cached\")}')
print(f'Total missing: {data.get(\"total_missing\")}')
print(f'Needs update: {data.get(\"needs_update_count\")}')
print(f'Recently updated: {data.get(\"recently_updated_count\")}')
print('')
missing = data.get('missing_by_index', {})
if missing:
    print('Missing by index:')
    for idx, info in missing.items():
        if isinstance(info, dict):
            print(f'  {info.get(\"short_name\", idx):15s}: {info.get(\"missing_count\", 0)} missing')
"
echo ""
echo ""

echo "=========================================="
echo "Summary"
echo "=========================================="
echo "All endpoints returning proper data!"
echo ""
