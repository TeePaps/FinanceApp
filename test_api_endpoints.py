#!/usr/bin/env python3
"""
API Endpoint Testing Script
Tests all requested API endpoints and reports on data completeness
"""

import requests
import json
from datetime import datetime

BASE_URL = "http://localhost:8080"

def test_endpoint(name, url, check_fn):
    """Test an endpoint and return results"""
    print(f"\n{'='*70}")
    print(f"Testing: {name}")
    print(f"URL: {url}")
    print(f"{'='*70}")

    try:
        response = requests.get(url, timeout=10)
        if response.status_code != 200:
            return {
                'endpoint': name,
                'url': url,
                'status': 'FAILED',
                'error': f'HTTP {response.status_code}',
                'data': None
            }

        data = response.json()
        result = check_fn(data)
        result.update({
            'endpoint': name,
            'url': url,
            'status': 'SUCCESS' if result.get('has_data', False) else 'INCOMPLETE',
        })
        return result

    except Exception as e:
        return {
            'endpoint': name,
            'url': url,
            'status': 'ERROR',
            'error': str(e),
            'data': None
        }

def check_all_tickers(data):
    """Check /api/all-tickers endpoint (closest to /api/valuations)"""
    tickers = data.get('tickers', [])
    total = len(tickers)

    with_eps_avg = sum(1 for t in tickers if t.get('eps_avg') is not None)
    with_price = sum(1 for t in tickers if t.get('current_price') is not None)
    with_both = sum(1 for t in tickers if t.get('eps_avg') is not None and t.get('current_price') is not None)
    sec_source = sum(1 for t in tickers if t.get('eps_source') == 'sec')

    sample = None
    complete = [t for t in tickers if t.get('eps_avg') is not None and t.get('current_price') is not None]
    if complete:
        sample = complete[0]

    print(f"Total tickers: {total}")
    print(f"Tickers with eps_avg: {with_eps_avg} ({with_eps_avg/total*100:.1f}%)")
    print(f"Tickers with current_price: {with_price} ({with_price/total*100:.1f}%)")
    print(f"Tickers with BOTH eps_avg and price: {with_both} ({with_both/total*100:.1f}%)")
    print(f"Tickers with SEC EPS source: {sec_source} ({sec_source/total*100:.1f}%)")

    if sample:
        print(f"\nSample complete ticker: {sample['ticker']}")
        print(f"  - Company: {sample.get('company_name')}")
        print(f"  - Price: ${sample.get('current_price')}")
        print(f"  - EPS Avg: ${sample.get('eps_avg')}")
        print(f"  - EPS Source: {sample.get('eps_source')}")
        print(f"  - EPS Years: {sample.get('eps_years')}")

    return {
        'has_data': total > 0 and with_both > 0,
        'total_tickers': total,
        'with_eps_avg': with_eps_avg,
        'with_price': with_price,
        'with_complete_data': with_both,
        'sec_source_count': sec_source,
        'sample': sample
    }

def check_recommendations(data):
    """Check /api/recommendations endpoint"""
    recs = data.get('recommendations', [])
    total = len(recs)

    print(f"Total recommendations: {total}")
    print(f"Total analyzed: {data.get('total_analyzed', 'N/A')}")

    if recs:
        sample = recs[0]
        print(f"\nTop recommendation: {sample.get('ticker')}")
        print(f"  - Company: {sample.get('company_name')}")
        print(f"  - Score: {sample.get('score')}")
        print(f"  - Current Price: ${sample.get('current_price')}")
        print(f"  - Estimated Value: ${sample.get('estimated_value')}")
        print(f"  - Price vs Value: {sample.get('price_vs_value')}%")
        print(f"  - Dividend Yield: {sample.get('dividend_yield')}%")
        print(f"  - EPS Years: {sample.get('eps_years')}")
        print(f"  - Reasons: {sample.get('reasons')}")

        # Check if all required fields are present
        required_fields = ['score', 'current_price', 'estimated_value', 'price_vs_value', 'eps_years']
        complete = all(sample.get(f) is not None for f in required_fields)
        print(f"  - Complete data: {complete}")

    return {
        'has_data': total > 0,
        'total_recommendations': total,
        'total_analyzed': data.get('total_analyzed'),
        'sample': recs[0] if recs else None
    }

def check_data_status(data):
    """Check /api/data-status endpoint"""
    indices = data.get('indices', [])
    sec = data.get('sec', {})

    print(f"Number of indices: {len(indices)}")
    print(f"\nSEC Status:")
    print(f"  - CIK mappings: {sec.get('cik_mappings')}")
    print(f"  - Companies cached: {sec.get('companies_cached')}")
    print(f"  - SEC unavailable: {sec.get('sec_unavailable')}")
    print(f"  - SEC unknown: {sec.get('sec_unknown')}")

    print(f"\nIndex Coverage:")
    for idx in indices:
        print(f"  {idx['name']:30s} - Total: {idx['total_tickers']:4d}, "
              f"Valuations: {idx['valuations_count']:4d}, "
              f"SEC: {idx['sec_source_count']:4d}, "
              f"Coverage: {idx['coverage_pct']}%")

    # Calculate totals
    all_index = next((i for i in indices if i['id'] == 'all'), None)
    if all_index:
        print(f"\nAggregate (All Indexes):")
        print(f"  - Total tickers: {all_index['total_tickers']}")
        print(f"  - With valuations: {all_index['valuations_count']}")
        print(f"  - SEC EPS count: {all_index['sec_source_count']}")
        print(f"  - Coverage: {all_index['coverage_pct']}%")

    return {
        'has_data': len(indices) > 0,
        'indices_count': len(indices),
        'sec_status': sec,
        'all_index_stats': all_index
    }

def check_eps_recommendations(data):
    """Check /api/eps-recommendations endpoint"""
    top_updates = data.get('top_updates', [])

    print(f"Total cached: {data.get('total_cached')}")
    print(f"Total missing: {data.get('total_missing')}")
    print(f"Total unavailable: {data.get('total_unavailable')}")
    print(f"Needs update count: {data.get('needs_update_count')}")
    print(f"Recently updated count: {data.get('recently_updated_count')}")
    print(f"Top updates: {len(top_updates)}")

    missing_by_index = data.get('missing_by_index', {})
    if missing_by_index:
        print(f"\nMissing by Index:")
        for idx, info in missing_by_index.items():
            if isinstance(info, dict):
                print(f"  {info.get('short_name', idx):15s}: {info.get('missing_count', 0)} missing")

    if top_updates:
        sample = top_updates[0]
        print(f"\nTop EPS update needed:")
        print(f"  - Ticker: {sample.get('ticker')}")
        print(f"  - Company: {sample.get('company_name')}")
        print(f"  - Latest FY: {sample.get('latest_fy')}")
        print(f"  - Expected filing: {sample.get('expected_filing')}")
        print(f"  - Reason: {sample.get('reason')}")

    return {
        'has_data': data.get('total_cached', 0) > 0,
        'total_cached': data.get('total_cached'),
        'total_missing': data.get('total_missing'),
        'needs_update_count': data.get('needs_update_count'),
        'top_updates': top_updates
    }

def main():
    """Run all endpoint tests"""
    print(f"\n{'#'*70}")
    print(f"# API Endpoint Testing - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"# Base URL: {BASE_URL}")
    print(f"{'#'*70}")

    results = []

    # Test 1: All tickers (equivalent to valuations)
    results.append(test_endpoint(
        "All Tickers (Valuations)",
        f"{BASE_URL}/api/all-tickers",
        check_all_tickers
    ))

    # Test 2: Recommendations
    results.append(test_endpoint(
        "Recommendations",
        f"{BASE_URL}/api/recommendations",
        check_recommendations
    ))

    # Test 3: Data Status
    results.append(test_endpoint(
        "Data Status",
        f"{BASE_URL}/api/data-status",
        check_data_status
    ))

    # Test 4: EPS Recommendations
    results.append(test_endpoint(
        "EPS Recommendations",
        f"{BASE_URL}/api/eps-recommendations",
        check_eps_recommendations
    ))

    # Summary
    print(f"\n\n{'='*70}")
    print("SUMMARY")
    print(f"{'='*70}")

    for result in results:
        status_symbol = "✓" if result['status'] == 'SUCCESS' else "✗" if result['status'] == 'FAILED' else "⚠"
        print(f"{status_symbol} {result['endpoint']:30s} - {result['status']}")
        if result.get('error'):
            print(f"  Error: {result['error']}")

    print(f"\n{'='*70}")
    print("KEY FINDINGS")
    print(f"{'='*70}")

    # Extract key metrics
    all_tickers_result = next((r for r in results if 'All Tickers' in r['endpoint']), None)
    if all_tickers_result and all_tickers_result.get('with_complete_data'):
        print(f"✓ Valuations: {all_tickers_result['with_complete_data']}/{all_tickers_result['total_tickers']} "
              f"tickers have complete data (eps_avg + current_price)")
        print(f"✓ SEC EPS Source: {all_tickers_result['sec_source_count']} tickers using SEC data")

    recs_result = next((r for r in results if 'Recommendations' in r['endpoint']), None)
    if recs_result and recs_result.get('total_recommendations'):
        print(f"✓ Recommendations: {recs_result['total_recommendations']} stocks with proper scores")

    data_status_result = next((r for r in results if 'Data Status' in r['endpoint']), None)
    if data_status_result and data_status_result.get('all_index_stats'):
        stats = data_status_result['all_index_stats']
        print(f"✓ Data Status: {stats['sec_source_count']} SEC EPS entries across {stats['total_tickers']} tickers")

    eps_recs_result = next((r for r in results if 'EPS Recommendations' in r['endpoint']), None)
    if eps_recs_result:
        print(f"✓ EPS Status: {eps_recs_result['total_cached']} cached, "
              f"{eps_recs_result['total_missing']} missing, "
              f"{eps_recs_result['needs_update_count']} need updates")

    print(f"\n{'='*70}\n")

    # Save results to JSON
    output_file = 'api_test_results.json'
    with open(output_file, 'w') as f:
        json.dump({
            'timestamp': datetime.now().isoformat(),
            'results': results
        }, f, indent=2, default=str)
    print(f"Full results saved to: {output_file}")

if __name__ == "__main__":
    main()
