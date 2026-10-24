# PIRATES ALERT V3 — Chrome Manifest V3 Extension & Extraction Backend

PIRATES ALERT V3 is a deterministic-first, Gemini-assisted web listing extraction system with immutable strategy versioning, safe rollback controls, and a Chrome Manifest V3 extension popup.

---

## Architecture Overview

```
E:\PIRATES ALERT V3\
├── app/
│   ├── ai/
│   │   ├── evidence_builder.py     # Sanitizes HTML, bounds token budget, frames untrusted content
│   │   ├── gemini_client.py        # Async Google Gen AI client with timeout, error redaction, mock injection
│   │   ├── strategy_learner.py     # Executes strategies, validates against quality layer, retry budget
│   │   └── strategy_schemas.py     # Strict Pydantic models for declarative CSS selector proposals
│   ├── api/
│   │   └── schemas.py              # Pydantic models for API request/response with camelCase serialization
│   ├── browser/
│   │   ├── page_loader.py          # Isolated context loader, subresource & navigation SSRF route interceptor, popup closer
│   │   └── playwright_manager.py   # Event-loop resilient Chromium lifecycle manager
│   ├── discovery/
│   │   ├── candidate_detector.py   # Heuristic DOM cluster discovery for repeating card structures
│   │   ├── page_classifier.py      # Content-based classification (ecommerce vs. generic_listing)
│   │   └── structured_data.py      # Schema.org JSON-LD extractor (ItemList, Product, JobPosting, etc.)
│   ├── extraction/
│   │   ├── engine.py               # Orchestrator: Deterministic -> Active Strategy -> Gemini Learning
│   │   ├── normalizer.py           # Numeric price parsing, currency, rating, URL resolution, deduplication
│   │   ├── quality.py              # Extraction quality evaluation layer (composite score, duplicate/contamination rates)
│   │   └── validator.py            # Record sanitization, boilerplate filtering, success/failure status
│   ├── monitoring/
│   │   └── observability.py        # Correlation IDs, StageTimer, safe structured logging, in-memory operational metrics
│   ├── rules/
│   │   ├── fingerprint.py          # Stable structural DOM fingerprinting (independent of content, titles, prices, ordering)
│   │   └── repository.py           # SQLite immutable strategy versioning, active pointers, rollback history, safe migration
│   ├── security/
│   │   └── url_validation.py       # SSRF protection, IP/DNS filtering, decimal/IPv6 literal handling, cloud metadata blocking
│   ├── config.py                   # Pydantic settings management (including CHROME_EXTENSION_ORIGIN)
│   └── main.py                     # FastAPI application endpoints with CORS (/health, /api/v1/extract, /api/v1/strategies, /metrics)
├── extension/
│   ├── manifest.json               # Chrome Manifest V3 configuration (activeTab, storage permissions)
│   ├── popup.html                  # Popup interface (Header, Form, Diagnostics, Records, Strategy Rollback)
│   ├── popup.css                   # Responsive dark-theme styling with accessible typography and cards
│   ├── popup.js                    # Vanilla JS logic with DOM sanitization, AbortController, and JSON export
│   ├── icons/                      # 16px, 48px, 128px PNG icon assets
│   └── README.md                   # Extension setup and manual testing guide
├── tests/                          # Automated pytest suite (68 passed, 1 skipped)
└── pytest.ini                      # Pytest warning filters
```

---

## Chrome Manifest V3 Extension (Phase 4)

The Chrome extension frontend allows users to extract listing pages directly from their browser while relying on the Python Playwright backend for rendering and extraction.

### Core Capabilities
1. **Target URL Pre-filling**: Reads the active tab URL via `activeTab` permission. Filters out internal schemes (`chrome://`, `about:`).
2. **Page Classification**: Allows selecting `Auto-detect`, `E-commerce Catalog`, or `Generic Listing`.
3. **Extraction Diagnostics**: Displays strategy source, method, AI call count (visually highlighted for `0 calls`), quality score, strategy ID/version, and execution time.
4. **Card Results Display**: Shows extracted title links, image previews, prices, MRP strikes, ratings, review counts, product IDs, and custom attributes.
5. **JSON Export**: Provides one-click clipboard copy and file download of the real backend response.
6. **Strategy Lifecycle UI**: Collapsible panel to view persisted strategy versions for a hostname and trigger explicit rollbacks with confirmation and reason logs.
7. **Safe DOM Handling**: Uses safe DOM APIs (`document.createElement`, `textContent`) and strict `http/https` URL sanitization.

---

## Backend Strategy Lifecycle & Versioning (Phase 3)

- **Immutable Strategy Versions**: Every newly validated AI-learned strategy commits a new sequential version in SQLite (`strategy_versions`), keeping the prior known-good version available.
- **Controlled Rollback**: Explicit rollback operation reactivates a prior valid version and records an audit log in `rollback_history`.
- **Zero-Call Reuse**: Reuses active known-good strategies with 0 Gemini calls, even when content, prices, titles, or ordering changes.
- **Quality Evaluation Layer**: Scores extraction results on validity, duplicate ratio, contamination rate, and page-type-aware completeness.

---

## Local Setup & Run Instructions (Windows PowerShell)

### 1. Prerequisites
- Windows 10/11
- Python 3.11 installed
- Google Chrome

### 2. Environment Setup & Dependencies
```powershell
Set-Location "E:\PIRATES ALERT V3"
.\.venv\Scripts\Activate.ps1
.\.venv\Scripts\pip.exe install -r requirements.txt
.\.venv\Scripts\playwright.exe install chromium
```

### 3. Start the Backend Server
```powershell
.\.venv\Scripts\uvicorn.exe app.main:app --host 127.0.0.1 --port 8000 --reload
```
Check health: [http://127.0.0.1:8000/health](http://127.0.0.1:8000/health)

### 4. Install the Chrome Extension
1. Open Google Chrome and navigate to `chrome://extensions/`.
2. Toggle **Developer mode** in the top-right corner to **ON**.
3. Click **Load unpacked** in the top-left corner.
4. Select the directory: `E:\PIRATES ALERT V3\extension`.
5. The **PIRATES ALERT** extension icon will appear in the toolbar.

### 5. CORS Configuration (Optional Production Hardening)
In development, the backend automatically accepts unpacked extension origins (`chrome-extension://[a-z]{32}`).
To pin a specific extension ID, add to `.env`:
```env
CHROME_EXTENSION_ORIGIN=chrome-extension://<your-extension-id>
```
Restart the server after editing `.env`.

---

## Automated Test Suite

Run the full automated test suite:
```powershell
.\.venv\Scripts\python.exe -m pytest -v
```

### Test Coverage
- **API & Endpoints**: Root, health check, CORS origin handling, extraction requests, strategy version listing, rollback, operational metrics.
- **Browser & Playwright Integration**: Lifecycle, clean shutdown across asyncio loops, local fixture server rendering, SSRF subresource abortion, popup auto-closing.
- **Candidate Detection & Normalization**: DOM clustering, JSON-LD extraction, price/currency normalization, rating regex, deduplication.
- **Strategy Learning & Versioning**: Offline mocked Gemini learning, immutable version increments, rollback restores, legacy migration, concurrency locks.
- **Fingerprinting & Quality**: Content-independent structural fingerprints, duplicate rejection, generic listing without prices/images, zero-call reuse.
- **Security & SSRF**: Loopback, link-local, private IPv4/IPv6, cloud metadata hostnames, decimal IP literals, embedded credentials.
