# PIRATES ALERT V3 — Phase 2 Backend: Gemini Strategy Learning and Validation

PIRATES ALERT V3 is a hybrid deterministic and AI-assisted e-commerce and generic listing extraction backend built with Python 3.11, FastAPI, Playwright Chromium, and the official Google Gen AI SDK (`google-genai`).

The engine adheres strictly to a **Deterministic-First** architecture: structured data (JSON-LD) and heuristic DOM discovery are always evaluated first. When unfamiliar layouts defeat heuristics, Gemini learns a declarative CSS extraction strategy. Python executes and rigorously validates the strategy against rendered HTML; only validated strategies are persisted in a local SQLite repository and reused with **zero subsequent AI calls**.

---

## Architecture Overview

```
app/
├── ai/
│   ├── __init__.py
│   ├── evidence_builder.py     # Sanitizes HTML, bounds token budget, frames untrusted web content
│   ├── gemini_client.py        # Async Google Gen AI client with timeout, error redaction, mock injection
│   ├── strategy_learner.py     # Strategy execution engine, acceptance validation, and correction loop
│   └── strategy_schemas.py     # Strict Pydantic models for declarative CSS selector proposals
├── api/
│   ├── __init__.py
│   └── schemas.py              # Pydantic models with camelCase JSON API serialization
├── browser/
│   ├── __init__.py
│   ├── page_loader.py          # Isolated context loader, subresource & navigation SSRF route interceptor
│   └── playwright_manager.py   # Async Chromium lifecycle and health management
├── discovery/
│   ├── __init__.py
│   ├── candidate_detector.py   # Heuristic DOM cluster discovery for repeating card structures
│   ├── page_classifier.py      # Content-based classification (ecommerce vs. generic_listing)
│   └── structured_data.py      # Schema.org JSON-LD extractor (ItemList, Product, JobPosting, etc.)
├── extraction/
│   ├── __init__.py
│   ├── engine.py               # Orchestrator: Deterministic -> Saved Strategy -> Gemini Learning
│   ├── normalizer.py           # Numeric price parsing, currency, rating, URL resolution, deduplication
│   └── validator.py            # Record sanitization, boilerplate filtering, success/failure status
├── rules/
│   ├── __init__.py
│   ├── fingerprint.py          # Structural DOM layout fingerprinting (independent of titles/prices)
│   └── repository.py           # SQLite strategy persistence with concurrency locks and atomic upserts
├── security/
│   ├── __init__.py
│   └── url_validation.py       # SSRF protection, IP/DNS filtering, redirect guard
├── config.py                   # Pydantic settings management
└── main.py                     # FastAPI application routes (/health, /api/v1/extract, /)
```

---

## Strategy Learning & Execution Lifecycle

```
[ Incoming Request URL ]
         │
         ▼
[ URL & DNS Validation ] ──(Invalid IP / Scheme)──> HTTP 400
         │
         ▼
[ Playwright Chromium ] ──(Subresource SSRF Interception / Bot Challenge Detection)
         │
         ▼
[ Phase 1 Deterministic Extraction ]
    ├─> Structured Data (JSON-LD)
    └─> Heuristic DOM Cluster Discovery
         │
    (Succeeded with >= 2 Valid Records?)
    ├── YES ───────────────────────────────────────> Return Result [strategySource: "deterministic", aiCalls: 0]
    └── NO
         │
         ▼
[ Compute Structural Fingerprint ]
         │
         ▼
[ Check SQLite Strategy Repository ]
    (Compatible Saved Strategy Exists?)
    ├── YES ──> [ Execute Saved Strategy ]
    │                │
    │           (Passed Validation?)
    │           ├── YES ───────────────────────────> Return Result [strategySource: "saved", aiCalls: 0]
    │           └── NO  ──> Mark Failed & Proceed to Recovery
    │
    └── NO / FAILED
         │
         ▼
    (GEMINI_API_KEY Configured?)
    ├── NO ────────────────────────────────────────> Return Unsuccessful Diagnostic [aiCalls: 0]
    └── YES
         │
         ▼
[ Build Sanitized Evidence (Max 10KB, 3 Cards) ]
         │
         ▼
[ Call Gemini Flash via google-genai ] ──(Timeout / Quota / JSON Error Handled)
         │
         ▼
[ Validate Proposed CSS Selectors against Page ]
    (Passed Multi-Record Validation?)
    ├── YES ──> [ Persist in SQLite ] ────────────> Return Result [strategySource: "ai_learned", aiCalls: 1..N]
    └── NO  ──> [ Bounded Correction Attempt ] ───> Return Failed Diagnostic (Never persist invalid strategy)
```

### Deterministic-First Trigger Conditions
Gemini is **only** invoked when:
1. Structured data (JSON-LD) produces 0 valid records.
2. Heuristic DOM detection produces 0 valid records (e.g. non-standard layouts where titles and links are decoupled).
3. No valid saved strategy exists for the `(hostname, page_type, fingerprint)` tuple (or the saved strategy failed revalidation).
4. `GEMINI_API_KEY` is configured in environment.

---

## Setup & Run Instructions (Windows PowerShell)

### 1. Prerequisites
- Windows 10/11
- Python 3.11 installed

### 2. Create and Activate Virtual Environment
```powershell
Set-Location "E:\PIRATES ALERT V3"
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
```

### 3. Install Dependencies
```powershell
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\pip.exe install -r requirements.txt
```

### 4. Install Playwright Chromium
```powershell
.\.venv\Scripts\playwright.exe install chromium
```

### 5. Configure Environment Variables
Copy `.env.example` to `.env` and set your optional Gemini API key:
```powershell
Copy-Item .env.example .env
```
Key configuration settings:
- `GEMINI_API_KEY`: Your Google AI Studio API key (leave blank to run in deterministic-only mode).
- `GEMINI_MODEL`: `gemini-2.5-flash` (recommended for fast structured strategy learning).
- `GEMINI_TIMEOUT_SECONDS`: `30` (bounded request timeout).
- `MAX_AI_ATTEMPTS_PER_EXTRACTION`: `2` (strict call budget per extraction).
- `STRATEGY_DB_PATH`: `strategies.db` (local SQLite repository path).

### 6. Run the Server
```powershell
.\.venv\Scripts\uvicorn.exe app.main:app --host 127.0.0.1 --port 8000 --reload
```
Interactive documentation:
- Swagger UI: `http://127.0.0.1:8000/docs`
- ReDoc: `http://127.0.0.1:8000/redoc`

### 7. Run the Test Suite
```powershell
# Run the complete test suite (all mocked, offline, zero live API calls required)
.\.venv\Scripts\pytest.exe -v
```

---

## API Specification

### `GET /health`
Returns service health, Playwright readiness, and Gemini AI status without exposing keys:
```json
{
  "status": "healthy",
  "playwrightAvailable": true,
  "geminiAvailable": true,
  "geminiModel": "gemini-2.5-flash",
  "timestamp": "2026-10-23T22:00:00Z"
}
```

### `POST /api/v1/extract`
Accepts a listing URL and optional `pageType` (`auto`, `ecommerce`, `generic_listing`).

#### Example Response (AI-Learned Strategy):
```json
{
  "sourceUrl": "https://artisan-shop.org/catalog",
  "pageType": "ecommerce",
  "success": true,
  "records": [
    {
      "title": "Handmade Ceramic Coffee Mug",
      "url": "https://artisan-shop.org/crafts/handmade-ceramic-mug",
      "productId": "ART-001",
      "imageUrl": "https://artisan-shop.org/crafts/ceramic-mug.jpg",
      "sellingPrice": 1850.0,
      "mrp": 2200.0,
      "currency": "INR",
      "rating": 4.7,
      "reviewCount": 65,
      "extra": {}
    }
  ],
  "metadata": {
    "recordCount": 3,
    "extractionMethod": "ai_strategy",
    "validationStatus": "passed",
    "strategySource": "ai_learned",
    "aiCalls": 1,
    "fingerprint": "a3f890b2c1d4e7f9",
    "metrics": {
      "candidateCount": 3,
      "recordCount": 3,
      "duplicateRatio": 0.0,
      "duration_ms": 1420
    },
    "recoveryStatus": null,
    "warnings": []
  }
}
```

#### Example Response (Saved Strategy Reuse - Zero AI Calls):
```json
{
  "sourceUrl": "https://artisan-shop.org/catalog",
  "pageType": "ecommerce",
  "success": true,
  "records": [...],
  "metadata": {
    "recordCount": 3,
    "extractionMethod": "saved_strategy",
    "validationStatus": "passed",
    "strategySource": "saved",
    "aiCalls": 0,
    "fingerprint": "a3f890b2c1d4e7f9",
    "warnings": []
  }
}
```

---

## Security & Deployment Considerations

1. **Prompt Injection & Untrusted Web Content**:
   - The engine never executes model-generated code (no Python, JavaScript, XPath, or shell expressions).
   - Only declarative CSS selectors and standard attributes are accepted after strict regex safety validation (`DISALLOWED_SELECTOR_PATTERNS`).
   - HTML evidence provided to Gemini is stripped of scripts, forms, cookies, and hidden tokens, and explicitly bounded (<= 10KB, <= 3 card samples).
2. **SSRF & Subresource Protection**:
   - URL validation enforces HTTP/HTTPS and rejects loopback, RFC1918 private subnets, link-local addresses (`169.254.0.0/16`), Carrier-Grade NAT, and non-public hostnames before navigation.
   - Playwright route interception validates every outgoing network request (both main-frame navigations and subresources like images or styles) against the SSRF policy, aborting forbidden requests.
3. **Egress Restrictions for Production**:
   - While DNS pre-resolution and route interception prevent direct SSRF, full defense-in-depth requires network-level egress filtering (e.g. cloud VPC security groups blocking access to metadata services and internal subnets) before deploying publicly to protect against DNS rebinding.

---

## Testing Strategy & Test Suite Breakdown

- **Deterministic Unit & Extraction Tests (`test_candidate_detection.py`, `test_extraction.py`)**: Tests DOM clustering, JSON-LD parsing, price/currency normalization, rating regex, URL resolution, deduplication, and boilerplate filtering.
- **SSRF & URL Security Tests (`test_url_validation.py`)**: Tests 100% offline rejection of localhost, private IPv4/IPv6, link-local, AWS metadata, malformed URLs, embedded credentials, and unresolvable domains.
- **FastAPI Endpoint Tests (`test_api.py`)**: Tests ASGI request/response models, status codes (200, 400, 403, 422, 502, 504), and error handling.
- **Playwright Browser Tests (`test_browser_integration.py`)**: Tests Chromium lifecycle and health checks.
- **Gemini Strategy Learning & Persistence Tests (`test_strategy_learning.py`)**:
  - Unfamiliar e-commerce learning with mocked Gemini.
  - Generic listing learning without requiring prices or images.
  - SQLite persistence and **explicit assertion that subsequent reuse causes exactly zero Gemini calls (`aiCalls == 0`)**.
  - Saved strategy failure detection and bounded recovery.
  - Rejection of invalid JSON, invalid schemas, zero-element selectors, and navigation/footer proposals.
  - Safe operation when `GEMINI_API_KEY` is missing.
  - Enforcement of AI attempt budgets and database survival across reinitialization.
- **Live Smoke Test (`test_gemini_live_smoke.py`)**: Optional live test skipped automatically unless `GEMINI_API_KEY` is present in the environment.
