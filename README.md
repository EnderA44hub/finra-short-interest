# FINRA Short Interest Monitor

Automatically detects which NYSE/NASDAQ tickers have their short interest at or near all-time highs, publishing an interactive page via GitHub Pages.

## What it does
- Downloads FINRA bulk file 2x per month (settlement dates)
- Accumulates historical short interest series in shares and float %
- Detects ATH 🔴 and Near High 🟠 per ticker
- Generates docs/index.html with Koyfin-style charts
- Auto-updates via GitHub Actions

## Setup
git clone https://github.com/EnderA44hub/finra-short-interest
cd finra-short-interest
python setup.py
pip install -r requirements.txt
python pipeline/run_pipeline.py

## Activate GitHub Pages
1. Go to Settings → Pages
2. Source: Deploy from a branch
3. Branch: main / folder: /docs
4. URL: https://EnderA44hub.github.io/finra-short-interest/

## Structure
finra-short-interest/
├── setup.py
├── requirements.txt
├── pipeline/
│   ├── extract.py
│   ├── validate.py
│   ├── transform.py
│   ├── analyze.py
│   ├── float_fetcher.py
│   ├── report.py
│   └── run_pipeline.py
├── data/
│   ├── history/
│   ├── latest/
│   ├── registry.json
│   ├── raw/
│   └── processed/
├── docs/
│   └── index.html
└── .github/workflows/
    └── pipeline.yml

## Flags
| Flag | Criteria |
|------|----------|
| 🔴 ATH | Short interest == all-time high |
| 🟠 Near High | Short interest >= 95th percentile historically |
