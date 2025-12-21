# FinanceApp Architecture

## High-Level Overview

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                              BROWSER (Frontend SPA)                              │
│  ┌──────────┬──────────┬──────────┬──────────┬──────────┬──────────┬──────────┐ │
│  │ Summary  │ Holdings │ Profit   │ Company  │ Market   │ Recommen-│   Data   │ │
│  │   Tab    │   Tab    │ Timeline │  Lookup  │ Analysis │ dations  │   Sets   │ │
│  └────┬─────┴────┬─────┴────┬─────┴────┬─────┴────┬─────┴────┬─────┴────┬─────┘ │
│       └──────────┴──────────┴──────────┴──────────┴──────────┴──────────┘       │
│                                      │                                           │
│                           static/app.js + static/js/api.js                       │
└──────────────────────────────────────┼───────────────────────────────────────────┘
                                       │ HTTP/JSON
                                       ▼
┌──────────────────────────────────────────────────────────────────────────────────┐
│                              FLASK SERVER (app.py)                               │
│  ┌────────────────────────────────────────────────────────────────────────────┐  │
│  │                            REST API Routes                                  │  │
│  │  /api/holdings  /api/transactions  /api/valuation/{ticker}  /api/screener  │  │
│  │  /api/summary   /api/prices        /api/recommendations     /api/sec/*     │  │
│  └────────────────────────────────────────────────────────────────────────────┘  │
└──────────────────────────────────────┼───────────────────────────────────────────┘
                                       │
           ┌───────────────────────────┼───────────────────────────┐
           ▼                           ▼                           ▼
┌─────────────────────┐   ┌─────────────────────┐   ┌─────────────────────┐
│   SERVICES LAYER    │   │   SERVICES LAYER    │   │   SERVICES LAYER    │
│                     │   │                     │   │                     │
│  services/          │   │  services/          │   │  sec_data.py        │
│  ├─ screener.py     │   │  ├─ valuation.py    │   │  (SEC EDGAR)        │
│  ├─ holdings.py     │   │  ├─ recommendations │   │                     │
│  └─ stock_utils     │   │  └─ data_manager    │   │                     │
└─────────┬───────────┘   └─────────┬───────────┘   └─────────┬───────────┘
          │                         │                         │
          └─────────────────────────┼─────────────────────────┘
                                    │
                    ┌───────────────┴───────────────┐
                    ▼                               ▼
        ┌─────────────────────┐         ┌─────────────────────┐
        │  PROVIDER REGISTRY  │         │    DATABASE LAYER   │
        │  services/providers │         │    database.py      │
        │  ├─ yfinance        │         │                     │
        │  ├─ fmp             │         │    ┌─────────────┐  │
        │  └─ sec             │         │    │   SQLite    │  │
        └─────────┬───────────┘         │    └─────────────┘  │
                  │                     └──────────┬──────────┘
                  ▼                                │
┌─────────────────────────────────────┐           │
│         EXTERNAL APIs               │           │
│  ┌──────────┐ ┌──────┐ ┌─────────┐  │           │
│  │ Yahoo    │ │ SEC  │ │   FMP   │  │           │
│  │ Finance  │ │EDGAR │ │(backup) │  │           │
│  └──────────┘ └──────┘ └─────────┘  │           │
└─────────────────────────────────────┘           │
                                                  ▼
                              ┌────────────────────────────────────┐
                              │           DATABASES                │
                              │  ┌────────────┐  ┌──────────────┐  │
                              │  │ private.db │  │  public.db   │  │
                              │  │ (user data)│  │(market data) │  │
                              │  └────────────┘  └──────────────┘  │
                              └────────────────────────────────────┘
```

## Database Schema

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                              PRIVATE DATABASE                                   │
│                            (data_private/private.db)                            │
│                                                                                 │
│  ┌─────────────────────┐          ┌────────────────────────────────────────┐   │
│  │      stocks         │          │           transactions                  │   │
│  ├─────────────────────┤          ├────────────────────────────────────────┤   │
│  │ ticker (PK)         │◄─────────│ stock_ticker (FK)                       │   │
│  │ company_name        │          │ transaction_type (BUY/SELL)            │   │
│  │ stock_type          │          │ shares, price_per_share                │   │
│  │ exclude_from_display│          │ transaction_date                       │   │
│  └─────────────────────┘          │ notes                                  │   │
│                                   └────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────────────────┐
│                               PUBLIC DATABASE                                    │
│                             (data_public/public.db)                              │
│                                                                                  │
│  ┌──────────────────┐     ┌──────────────────┐     ┌──────────────────────────┐ │
│  │     indexes      │     │  ticker_indexes  │     │        tickers           │ │
│  ├──────────────────┤     ├──────────────────┤     ├──────────────────────────┤ │
│  │ name (PK)        │◄────│ index_name (FK)  │     │ ticker (PK)              │ │
│  │ display_name     │     │ ticker (FK)      │────►│ company_name             │ │
│  │ short_name       │     └──────────────────┘     │ cik, sec_status          │ │
│  └──────────────────┘                              └────────────┬─────────────┘ │
│                                                                 │               │
│  ┌──────────────────────────────────────────────────────────────┼─────────────┐ │
│  │                                                              │             │ │
│  │  ┌──────────────────┐   ┌──────────────────┐   ┌─────────────▼──────────┐ │ │
│  │  │   valuations     │   │   eps_history    │   │    sec_companies       │ │ │
│  │  ├──────────────────┤   ├──────────────────┤   ├────────────────────────┤ │ │
│  │  │ ticker (FK)      │   │ ticker (FK)      │   │ ticker (FK)            │ │ │
│  │  │ current_price    │   │ year, eps        │   │ cik, company_name      │ │ │
│  │  │ eps_avg          │   │ filed            │   │ sec_no_eps, reason     │ │ │
│  │  │ estimated_value  │   │ period_start/end │   └────────────────────────┘ │ │
│  │  │ price_vs_value   │   └──────────────────┘                              │ │
│  │  │ dividend         │                                                      │ │
│  │  │ off_high_pct     │   ┌──────────────────┐   ┌────────────────────────┐ │ │
│  │  │ selloff_1m_pct   │   │   sec_filings    │   │     cik_mapping        │ │ │
│  │  └──────────────────┘   ├──────────────────┤   ├────────────────────────┤ │ │
│  │                         │ ticker (FK)      │   │ ticker (FK)            │ │ │
│  │                         │ fiscal_year      │   │ cik, name              │ │ │
│  │                         │ document_url     │   │ updated                │ │ │
│  │                         │ form_type        │   └────────────────────────┘ │ │
│  │                         └──────────────────┘                              │ │
│  └───────────────────────────────────────────────────────────────────────────┘ │
│                                                                                  │
│  ┌──────────────────┐   ┌──────────────────┐                                    │
│  │ ticker_failures  │   │     metadata     │                                    │
│  ├──────────────────┤   ├──────────────────┤                                    │
│  │ ticker           │   │ key (PK)         │                                    │
│  │ failure_count    │   │ value            │                                    │
│  └──────────────────┘   └──────────────────┘                                    │
└─────────────────────────────────────────────────────────────────────────────────┘
```

## Data Flow Diagrams

### 1. Screener Update Flow

```
┌─────────────┐     POST /api/screener/start     ┌─────────────┐
│   Browser   │ ─────────────────────────────────► │   Flask     │
└─────────────┘                                   └──────┬──────┘
       ▲                                                 │
       │                                    Spawn Background Thread
       │                                                 │
       │ Poll /api/screener/progress                     ▼
       │ every 500ms                          ┌─────────────────────┐
       │                                      │ services/screener   │
       │                                      └──────────┬──────────┘
       │                                                 │
       │              ┌──────────────────────────────────┼──────────────────────┐
       │              │                                  │                      │
       │              ▼                                  ▼                      ▼
       │    ┌─────────────────┐              ┌─────────────────┐     ┌─────────────────┐
       │    │ Phase 1: Prices │              │ Phase 2: EPS    │     │ Phase 3: Divs   │
       │    │ (Yahoo Finance) │              │ (SEC EDGAR)     │     │ (Yahoo Finance) │
       │    │ Batch: 100/req  │              │ Rate: 10/sec    │     │                 │
       │    └────────┬────────┘              └────────┬────────┘     └────────┬────────┘
       │             │                                │                       │
       │             └────────────────────────────────┼───────────────────────┘
       │                                              ▼
       │                                   ┌─────────────────────┐
       │                                   │ Phase 4: Valuation  │
       │                                   │ Fair Value Formula: │
       │                                   │ (Avg EPS + Div) × 10│
       │                                   └──────────┬──────────┘
       │                                              │
       │                                              ▼
       │                                   ┌─────────────────────┐
       │                                   │   public.db         │
       │                                   │   - valuations      │
       │                                   │   - eps_history     │
       │                                   └──────────┬──────────┘
       │                                              │
       └──────────────────────────────────────────────┘
```

### 2. Holdings & FIFO Cost Basis Flow

```
┌─────────────┐     POST /api/transactions       ┌─────────────┐
│   Browser   │ ─────────────────────────────────► │   Flask     │
│  (Add Buy/  │                                   └──────┬──────┘
│   Sell)     │                                          │
└─────────────┘                                          ▼
       ▲                                      ┌─────────────────────┐
       │                                      │ services/holdings   │
       │                                      │                     │
       │                                      │  FIFO Matching:     │
       │                                      │  ┌───────────────┐  │
       │                                      │  │ BUY lot 1     │  │
       │                                      │  │ BUY lot 2     │──┼──► Match SELL
       │                                      │  │ BUY lot 3     │  │    against oldest
       │                                      │  └───────────────┘  │    lots first
       │                                      │                     │
       │                                      │  Calculate:         │
       │                                      │  - Cost basis       │
       │                                      │  - Realized gain    │
       │                                      └──────────┬──────────┘
       │                                                 │
       │                                                 ▼
       │                                      ┌─────────────────────┐
       │                                      │   private.db        │
       │                                      │   - stocks          │
       │                                      │   - transactions    │
       │                                      └──────────┬──────────┘
       │                                                 │
       │      GET /api/holdings-analysis                 │
       └─────────────────────────────────────────────────┘
```

### 3. Recommendations Scoring Flow

```
┌─────────────┐     GET /api/recommendations     ┌─────────────┐
│   Browser   │ ─────────────────────────────────► │   Flask     │
└─────────────┘                                   └──────┬──────┘
       ▲                                                 │
       │                                                 ▼
       │                                      ┌─────────────────────────────────┐
       │                                      │ services/recommendations        │
       │                                      │                                 │
       │                                      │  Score = weighted sum of:       │
       │                                      │                                 │
       │                                      │  ┌─────────────────────────────┐│
       │                                      │  │ Undervaluation (×1.0)       ││
       │                                      │  │ = -price_vs_value           ││
       │                                      │  └─────────────────────────────┘│
       │                                      │                +                │
       │                                      │  ┌─────────────────────────────┐│
       │                                      │  │ Dividend Score (×1.5)       ││
       │                                      │  │ 5 pts per % yield (max 30)  ││
       │                                      │  │ No dividend = -30 penalty   ││
       │                                      │  └─────────────────────────────┘│
       │                                      │                +                │
       │                                      │  ┌─────────────────────────────┐│
       │                                      │  │ Selloff Score (×0.8)        ││
       │                                      │  │ Base: -off_high_pct         ││
       │                                      │  │ Bonuses for severe drops    ││
       │                                      │  └─────────────────────────────┘│
       │                                      │                                 │
       │                                      └────────────────┬────────────────┘
       │                                                       │
       │                                                       ▼
       │                                              ┌─────────────────┐
       │                                              │   public.db     │
       │                                              │   - valuations  │
       │                                              └────────┬────────┘
       │                                                       │
       │                  Ranked recommendations               │
       └───────────────────────────────────────────────────────┘
```

### 4. Multi-Source Provider Architecture

```
                    ┌─────────────────────────────────────────────────────┐
                    │              Data Orchestrator                      │
                    │         services/providers/registry.py              │
                    │                                                     │
                    │  ┌─────────────────┐   ┌─────────────────────────┐  │
                    │  │ Circuit Breaker │   │    Timeout Handler      │  │
                    │  │                 │   │                         │  │
                    │  │ CLOSED ──────►  │   │  10s timeout per call   │  │
                    │  │   (normal)      │   │  Scales for batch ops   │  │
                    │  │       │         │   └─────────────────────────┘  │
                    │  │  3 failures     │                                │
                    │  │  in 2 min       │   Configurable via Settings:   │
                    │  │       ▼         │   - Provider priority order    │
                    │  │ OPEN ──────────►│   - Disabled providers         │
                    │  │   (skip 2min)   │   - API keys in data_private/  │
                    │  │       │         │                                │
                    │  │  cooldown       │                                │
                    │  │       ▼         │                                │
                    │  │ HALF_OPEN ─────►│                                │
                    │  │  (test 1 req)   │                                │
                    │  └─────────────────┘                                │
                    └───────────────────────────┬─────────────────────────┘
                                                │
                ┌───────────────────────────────┼───────────────────────────┐
                │                               │                           │
                ▼                               ▼                           ▼
       ┌───────────────┐               ┌───────────────┐           ┌───────────────┐
       │ Price Provider│               │ EPS Provider  │           │ Div Provider  │
       │   Interface   │               │   Interface   │           │   Interface   │
       └───────┬───────┘               └───────┬───────┘           └───────┬───────┘
               │                               │                           │
               │                               │ Priority                  │ Priority
               ▼                               ▼                           ▼
 ┌─────────────────────────┐           ┌───────────────┐           ┌───────────────┐
 │   REAL-TIME PROVIDERS   │           │ 1. SEC EDGAR  │           │ 1. yfinance   │
 │   (tried first)         │           └───────┬───────┘           └───────────────┘
 ├─────────────────────────┤                   │ Fallback
 │ 1. Alpaca               │                   ▼
 │ 2. yfinance             │           ┌───────────────┐
 │ 3. FMP API              │           │ 2. yfinance   │
 └───────────┬─────────────┘           └───────┬───────┘
             │ All realtime                    │ Fallback
             │ failed                          ▼
             ▼                         ┌───────────────┐
 ┌─────────────────────────┐           │ 3. DefeatBeta │
 │  HISTORICAL PROVIDERS   │           │   (weekly)    │
 │  (fallback only)        │           └───────────────┘
 ├─────────────────────────┤
 │ 4. DefeatBeta (weekly)  │
 └─────────────────────────┘

       For PRICES: Real-time providers always tried before historical
       For EPS: Historical is fine, order based on authoritativeness
       Provider order configurable via Settings tab

       Fault Tolerance:
       - Timeout: 10s per provider call (prevents hanging)
       - Circuit Breaker: 3 failures in 2 min → skip provider for 2 min
       - Automatic recovery: Half-open state tests if provider recovers
```

## Component Responsibilities

| Component | File | Responsibility |
|-----------|------|----------------|
| **Flask App** | `app.py` | 40+ REST API routes, request routing |
| **Database Layer** | `database.py` | SQLite CRUD, schema management, transactions |
| **SEC Integration** | `sec_data.py` | SEC EDGAR API, EPS fetching, CIK mapping |
| **Valuation Service** | `services/valuation.py` | Fair value calculation |
| **Recommendations** | `services/recommendations.py` | Scoring algorithm |
| **Screener** | `services/screener.py` | Batch processing, background jobs |
| **Holdings** | `services/holdings.py` | FIFO cost basis calculations |
| **Stock Utils** | `services/stock_utils.py` | Stock info, selloff metrics, EPS extraction (delegates to providers for prices) |
| **Provider Registry** | `services/providers/registry.py` | Multi-source orchestration, timeouts |
| **Circuit Breaker** | `services/providers/circuit_breaker.py` | Fault tolerance, skip failing providers |
| **Provider Config** | `services/providers/config.py` | Provider priority, timeout, circuit breaker settings |
| **Provider Secrets** | `services/providers/secrets.py` | API key storage (data_private/) |
| **Frontend** | `static/app.js` | Tab-based SPA, real-time updates |

## Key Formulas

```
Fair Value = (Average EPS over 8 years + Annual Dividend) × 10

Price vs Value = ((Current Price - Fair Value) / Fair Value) × 100

Recommendation Score = (Undervaluation × 1.0) + (Dividend × 1.5) + (Selloff × 0.8)
```

## Cache Durations

| Data Type | Cache Duration | Constant |
|-----------|---------------|----------|
| Prices | 5 minutes | `PRICE_CACHE_DURATION` |
| Valuations | 24 hours | `STALE_DATA_HOURS` |
| CIK Mapping | 7 days | `CIK_CACHE_DAYS` |
| EPS Data | 1 day | `EPS_CACHE_DAYS` |

## Rate Limits

| API | Rate Limit | Implementation |
|-----|------------|----------------|
| Yahoo Finance | 100 tickers/batch | 0.5s delay between batches |
| SEC EDGAR | 10 requests/second | 0.12s delay per request |
| FMP (free tier) | 250/day | Used as fallback only |
| Alpaca | No strict limit | 100ms delay per request |

## Fault Tolerance Settings

| Setting | Default | Description |
|---------|---------|-------------|
| `provider_timeout_seconds` | 10s | Max time per provider call |
| `failure_threshold` | 3 | Failures before circuit opens |
| `failure_window_seconds` | 120s | Window for counting failures |
| `cooldown_seconds` | 120s | Time before retrying failed provider |
