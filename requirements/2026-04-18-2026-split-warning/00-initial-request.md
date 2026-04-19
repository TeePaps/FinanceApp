# Initial Request

**Date:** 2026-04-18
**Slug:** split-warning

## User's Request

> Seems I need to add "Split Warning" to the analyze page and recommendations. This will deeply skew the analysis because the EPS grabbed from EDGAR data will not reflect the split price change.

## Core Problem

Stock splits adjust share price but do not retroactively adjust historical EPS values reported to SEC EDGAR on a consistent basis. When the FinanceApp pulls EPS history for fair-value calculation (`(8yr avg EPS + div) × 10`), a recent split can skew the fair value wildly — making a stock appear dramatically over- or undervalued when it isn't.

## Goal

Surface a visible "Split Warning" on:
1. The **Analyze page** (company lookup results)
2. The **Recommendations** list

So the user knows to discount or verify the valuation before acting on it.
