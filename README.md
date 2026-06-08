# FlexyPe Outreach

## What is FlexyPe Outreach?

FlexyPe Outreach is a Merchant Intelligence Platform designed to automate merchant research, lead qualification, and merchant monitoring.

The platform scans merchant websites, detects checkout providers, enriches merchant profiles with contact and technology information, and presents everything through a centralized dashboard.

The objective is to reduce manual merchant research and provide a single place to understand:

- Which checkout provider a merchant uses
- Whether a merchant is using FlexyPe
- Historical provider usage
- Contact information
- Technology stack
- Lead priority and scoring

The platform is designed for both prospective merchants and existing FlexyPe merchants.

---

## How to Reproduce

### 1. Create Virtual Environment

```bash
python -m venv venv
source venv/bin/activate
```

### 2. Install Dependencies

```bash
pip install -r requirements.txt
playwright install chromium
```

### 3. Add Merchant Domains

Update:

```text
domains.csv
```

Example:

```csv
domain
mokobara.com
crossword.in
wellbeingnutrition.com
```

### 4. Run Merchant Scanner

```bash
python main_scraper.py
```

Generated outputs:

```text
results.json
results.csv
results.xlsx
```

### 5. Launch Dashboard

```bash
python server.py
```

Open:

```text
http://localhost:8080
```

---

## How It Works

The platform uses a multi-stage merchant intelligence pipeline.

```text
Merchant Website
        ↓
Static Analysis
        ↓
Runtime Analysis
        ↓
Merchant Enrichment
        ↓
Lead Scoring
        ↓
Dashboard
```

### Static Analysis

The scanner first performs source-code analysis using:

- Requests
- BeautifulSoup

The website source code and assets are analyzed to detect:

- Historical checkout providers
- Shopify usage
- Contact emails
- Social profiles
- Technology stack
- Merchant metadata

This layer is fast and scalable, making it suitable for scanning thousands of merchants.

### Runtime Analysis

The platform then launches Playwright and simulates a real browser session.

Network requests are monitored during page execution to identify:

- Live checkout providers
- Dynamic integrations
- Authentication flows
- Runtime checkout activity

Unlike source-code analysis, runtime analysis verifies which provider is actively being used today.

### Historical vs Live Detection

Provider detection is intentionally separated into two categories.

#### Historical Providers

Detected through:

- Source code
- Scripts
- Assets
- Static references

#### Live Providers

Detected through:

- Runtime execution
- Network requests
- Dynamic provider loading

This distinction helps reduce false positives caused by legacy integrations that may still exist in source code.

### Merchant Enrichment

Results from both detection layers are merged into a single merchant profile containing:

- Domain
- Live checkout provider
- Historical checkout providers
- Shopify status
- Contact emails
- Social profiles
- Technology stack
- Merchant metadata

### Lead Scoring

Each merchant is automatically scored based on:

- Checkout signals
- Historical provider activity
- Shopify detection
- Contact information availability
- Technology stack
- Login / Kwikpass detection

The resulting score is used to classify merchants into priority buckets.

### Dashboard

The dashboard consumes the generated merchant intelligence data and provides:

- Merchant search
- Provider filtering
- Lead prioritization
- Watchlists
- Internal notes
- Status tracking
- Outreach workflows

---

## Demo

YouTube Demo:

```text
https://www.youtube.com/watch?v=d8DG8y0_tlI
```