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
    SELLOFF_RECENT_BONUS,
    RECOMMENDATION_MIN_EPS_YEARS
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
    current_price = valuation_data.get('current_price', 0)
    price_vs_value = valuation_data.get('price_vs_value', 0)
    annual_dividend = valuation_data.get('annual_dividend', 0)
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
    current_price = valuation_data.get('current_price', 0)
    price_vs_value = valuation_data.get('price_vs_value', 0)
    annual_dividend = valuation_data.get('annual_dividend', 0)
    off_high_pct = valuation_data.get('off_high_pct', 0) or 0
    eps_years = valuation_data.get('eps_years', 0)

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


def get_top_recommendations(valuations, ticker_indexes=None, limit=10):
    """
    Get top N stock recommendations.

    Args:
        valuations: Dict mapping ticker to valuation data
        ticker_indexes: Optional dict mapping ticker to list of indexes
        limit: Number of recommendations to return

    Returns:
        Dict with recommendations list and metadata
    """
    if not valuations:
        return {'recommendations': [], 'error': 'No valuation data available'}

    ticker_indexes = ticker_indexes or {}
    scored_stocks = []

    for ticker, val in valuations.items():
        # Skip stocks without key metrics
        if not val.get('current_price') or val.get('current_price', 0) <= 0:
            continue
        if val.get('price_vs_value') is None:
            continue

        eps_years = val.get('eps_years', 0)

        # Skip stocks with very low data quality
        if eps_years < RECOMMENDATION_MIN_EPS_YEARS:
            continue

        current_price = val.get('current_price', 0)
        annual_dividend = val.get('annual_dividend', 0)

        # Calculate dividend yield for display
        dividend_yield = (annual_dividend / current_price * 100) if current_price > 0 else 0

        # Calculate score
        total_score = score_stock(val)

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
        'criteria': {
            'undervaluation': 'Stocks trading below estimated value (based on 10x average EPS)',
            'dividend': 'Higher dividend yield preferred',
            'selloff': 'Stocks that have pulled back from highs (potential buying opportunity)'
        }
    }


class RecommendationService:
    """
    Service class for recommendation-related operations.

    Provides a higher-level interface for working with recommendations.
    """

    def __init__(self, data_manager=None, weights=None):
        """
        Initialize the recommendation service.

        Args:
            data_manager: Optional data manager instance
            weights: Optional custom scoring weights
        """
        self.data_manager = data_manager
        self.weights = weights or SCORING_WEIGHTS

    def get_recommendations(self, limit=10):
        """
        Get top recommendations from cached valuation data.

        Args:
            limit: Number of recommendations to return

        Returns:
            Dict with recommendations list and metadata
        """
        if not self.data_manager:
            return {'recommendations': [], 'error': 'No data manager configured'}

        valuations = self.data_manager.load_valuations().get('valuations', {})
        return get_top_recommendations(valuations, limit=limit)

    def score_ticker(self, ticker):
        """
        Get score for a specific ticker.

        Args:
            ticker: Stock ticker symbol

        Returns:
            Dict with score and reasons
        """
        if not self.data_manager:
            return {'error': 'No data manager configured'}

        valuations = self.data_manager.load_valuations().get('valuations', {})
        val = valuations.get(ticker.upper())

        if not val:
            return {'error': f'No valuation data for {ticker}'}

        return {
            'ticker': ticker.upper(),
            'score': round(score_stock(val), 1),
            'reasons': explain_score(val)
        }

    def compare_tickers(self, tickers):
        """
        Compare scores for multiple tickers.

        Args:
            tickers: List of ticker symbols

        Returns:
            List of dicts with ticker scores, sorted by score
        """
        if not self.data_manager:
            return []

        valuations = self.data_manager.load_valuations().get('valuations', {})
        results = []

        for ticker in tickers:
            val = valuations.get(ticker.upper())
            if val:
                results.append({
                    'ticker': ticker.upper(),
                    'company_name': val.get('company_name', ticker),
                    'score': round(score_stock(val), 1),
                    'price_vs_value': val.get('price_vs_value'),
                    'reasons': explain_score(val)
                })

        results.sort(key=lambda x: x['score'], reverse=True)
        return results
