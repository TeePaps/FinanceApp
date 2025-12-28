"""
Stock recommendation scoring service.

Provides:
- Composite scoring algorithm for stock recommendations
- Score explanation generation
- Top recommendation retrieval
"""

from config import (
    SCORING_WEIGHTS,
    DIVIDEND_NO_DIVIDEND_PENALTY,
    DIVIDEND_POINTS_PER_PERCENT,
    DIVIDEND_MAX_POINTS,
    SELLOFF_SEVERE_BONUS,
    SELLOFF_MODERATE_BONUS,
    SELLOFF_RECENT_BONUS
)


def score_stock(valuation_data):
    """
    Calculate recommendation score for a stock.

    Scoring components:
    1. Undervaluation: More negative price_vs_value = higher score
    2. Dividend: Higher yield = higher score, no dividend = penalty
    3. Selloff: Stocks down from highs get bonus points

    Args:
        valuation_data: Dict with valuation metrics

    Returns:
        Float score (higher = better recommendation)
    """
    current_price = valuation_data.get('current_price', 0) or 0
    price_vs_value = valuation_data.get('price_vs_value', 0) or 0
    annual_dividend = valuation_data.get('annual_dividend', 0) or 0
    off_high_pct = valuation_data.get('off_high_pct', 0) or 0
    in_selloff = valuation_data.get('in_selloff', False)
    selloff_severity = valuation_data.get('selloff_severity', 'none')

    # Calculate dividend yield
    dividend_yield = (annual_dividend / current_price * 100) if current_price > 0 else 0

    # 1. Undervaluation score: more negative price_vs_value = better
    #    -50% undervalued gets 50 points, 0% gets 0, +50% overvalued gets -50
    undervalue_score = -price_vs_value  # Flip sign so undervalued = positive

    # 2. Dividend score
    #    - No dividend: penalty
    #    - 0-6% yield mapped to 0-max points
    if dividend_yield <= 0:
        dividend_score = DIVIDEND_NO_DIVIDEND_PENALTY
    else:
        dividend_score = min(dividend_yield * DIVIDEND_POINTS_PER_PERCENT, DIVIDEND_MAX_POINTS)

    # 3. Selloff score: based on how far off the high
    #    -50% off high = 50 points, -20% = 20 points, 0% = 0 points
    selloff_score = -off_high_pct if off_high_pct < 0 else 0

    # Bonus for being in active selloff
    if in_selloff:
        if selloff_severity == 'severe':
            selloff_score += SELLOFF_SEVERE_BONUS
        elif selloff_severity in ('moderate', 'high'):
            selloff_score += SELLOFF_MODERATE_BONUS
        elif selloff_severity == 'recent':
            selloff_score += SELLOFF_RECENT_BONUS

    # Total score with weights from config
    total_score = (undervalue_score * SCORING_WEIGHTS['undervaluation']) + \
                  (dividend_score * SCORING_WEIGHTS['dividend']) + \
                  (selloff_score * SCORING_WEIGHTS['selloff'])

    return total_score


def explain_score(valuation_data):
    """
    Generate human-readable reasons for a stock's score.

    Args:
        valuation_data: Dict with valuation metrics

    Returns:
        List of reason strings
    """
    current_price = valuation_data.get('current_price', 0) or 0
    price_vs_value = valuation_data.get('price_vs_value', 0) or 0
    annual_dividend = valuation_data.get('annual_dividend', 0) or 0
    off_high_pct = valuation_data.get('off_high_pct', 0) or 0
    eps_years = valuation_data.get('eps_years', 0) or 0

    # Calculate dividend yield
    dividend_yield = (annual_dividend / current_price * 100) if current_price > 0 else 0

    reasons = []

    # Undervaluation reason
    if price_vs_value <= -30:
        reasons.append(f"Significantly undervalued at {price_vs_value:.0f}% below estimated value")
    elif price_vs_value <= -15:
        reasons.append(f"Undervalued at {price_vs_value:.0f}% below estimated value")
    elif price_vs_value <= 0:
        reasons.append(f"Slightly undervalued at {price_vs_value:.0f}% below estimated value")

    # Dividend reason
    if dividend_yield >= 4:
        reasons.append(f"High dividend yield of {dividend_yield:.1f}%")
    elif dividend_yield >= 2:
        reasons.append(f"Solid dividend yield of {dividend_yield:.1f}%")
    elif dividend_yield >= 1:
        reasons.append(f"Moderate dividend yield of {dividend_yield:.1f}%")

    # Selloff reason
    if off_high_pct <= -40:
        reasons.append(f"Down {-off_high_pct:.0f}% from 52-week high - severe selloff")
    elif off_high_pct <= -25:
        reasons.append(f"Down {-off_high_pct:.0f}% from 52-week high - significant pullback")
    elif off_high_pct <= -15:
        reasons.append(f"Down {-off_high_pct:.0f}% from 52-week high - moderate pullback")

    # Data quality note
    if eps_years >= 10:
        reasons.append(f"Strong {eps_years}-year earnings history")
    elif eps_years >= 8:
        reasons.append(f"Good {eps_years}-year earnings history")

    return reasons


def get_top_recommendations(valuations, ticker_indexes=None, limit=10,
                            filter_by_index=False, include_index_bonus=False):
    """
    Get top N stock recommendations.

    Args:
        valuations: Dict mapping ticker to valuation data
        ticker_indexes: Optional dict mapping ticker to list of indexes
        limit: Number of recommendations to return
        filter_by_index: If True, only include stocks that are in ticker_indexes
        include_index_bonus: If True, add bonus points for major index membership

    Returns:
        Dict with recommendations list and metadata
    """
    if not valuations:
        return {'recommendations': [], 'error': 'No valuation data available'}

    ticker_indexes = ticker_indexes or {}
    scored_stocks = []

    # Track exclusion reasons
    excluded = {
        'not_in_index': 0,
        'no_price': 0,
        'no_valuation': 0,
        'no_eps': 0
    }

    for ticker, val in valuations.items():
        # Skip stocks not in any enabled index (if filtering enabled)
        if filter_by_index and ticker not in ticker_indexes:
            excluded['not_in_index'] += 1
            continue

        # Skip stocks without key metrics
        if not val.get('current_price') or val.get('current_price', 0) <= 0:
            excluded['no_price'] += 1
            continue
        if val.get('price_vs_value') is None:
            excluded['no_valuation'] += 1
            continue

        eps_years = val.get('eps_years', 0) or 0

        # Skip stocks with no EPS data at all
        if eps_years == 0:
            excluded['no_eps'] += 1
            continue

        current_price = val.get('current_price', 0) or 0
        annual_dividend = val.get('annual_dividend', 0) or 0

        # Calculate dividend yield for display
        dividend_yield = (annual_dividend / current_price * 100) if current_price > 0 else 0

        # Calculate score
        total_score = score_stock(val)

        # Add index bonus if requested
        if include_index_bonus and ticker in ticker_indexes:
            stock_indexes = ticker_indexes.get(ticker, [])
            index_names_lower = [idx.lower().replace(' ', '').replace('&', '') for idx in stock_indexes]

            if any('dow' in idx or 'djia' in idx for idx in index_names_lower):
                total_score += 10  # Dow 30 - most prestigious blue chips
            elif any('sp500' in idx for idx in index_names_lower):
                total_score += 8   # S&P 500 - large cap, stable
            elif any('nasdaq' in idx for idx in index_names_lower):
                total_score += 6   # NASDAQ 100 - large cap tech

        # Build reasoning
        reasons = explain_score(val)

        scored_stocks.append({
            'ticker': ticker,
            'company_name': val.get('company_name', ticker),
            'current_price': current_price,
            'estimated_value': val.get('estimated_value'),
            'price_vs_value': val.get('price_vs_value'),
            'annual_dividend': annual_dividend,
            'dividend_yield': round(dividend_yield, 2),
            'off_high_pct': val.get('off_high_pct', 0) or 0,
            'in_selloff': val.get('in_selloff', False),
            'selloff_severity': val.get('selloff_severity', 'none'),
            'eps_years': eps_years,
            'score': round(total_score, 1),
            'reasons': reasons,
            'indexes': ticker_indexes.get(ticker, []),
            'updated': val.get('updated')
        })

    # Sort by score descending and take top N
    scored_stocks.sort(key=lambda x: x['score'], reverse=True)
    top_n = scored_stocks[:limit]

    return {
        'recommendations': top_n,
        'total_analyzed': len(scored_stocks),
        'excluded': excluded,
        'criteria': {
            'undervaluation': 'Stocks trading below estimated value (based on 10x average EPS)',
            'dividend': 'Higher dividend yield preferred',
            'selloff': 'Stocks that have pulled back from highs (potential buying opportunity)'
        }
    }
