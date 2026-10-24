# PIRATES ALERT V3 — Chrome Manifest V3 Extension

A lightweight, secure, and modern Chrome Manifest V3 toolbar extension for **PIRATES ALERT V3**.

---

## Architecture Overview

```
[ Active Web Page (e.g. Amazon, Artisan Shop) ]
                     │
         (User clicks extension icon)
                     ▼
┌──────────────────────────────────────────────────────────┐
│ Chrome Manifest V3 Extension Popup                      │
│ - Reads active tab URL                                   │
│ - User configures Page Type (Auto/E-commerce/Generic)    │
│ - Sends extraction request to local FastAPI backend      │
└────────────────────────────┬─────────────────────────────┘
                             │ POST http://127.0.0.1:8000/api/v1/extract
                             ▼
┌──────────────────────────────────────────────────────────┐
│ PIRATES ALERT V3 Python Backend                          │
│ 1. Validates target URL & blocks SSRF                    │
│ 2. Launches Playwright Chromium in headless sandbox      │
│ 3. Intercepts network subresources                       │
│ 4. Deterministic Extraction (JSON-LD / Heuristic DOM)    │
│ 5. If unfamiliar: Reuses saved SQLite strategy (0 AI)    │
│ 6. If needed: Gemini learns declarative CSS selectors     │
│ 7. Returns normalized, validated JSON to popup           │
└────────────────────────────┬─────────────────────────────┘
                             │
                             ▼
┌──────────────────────────────────────────────────────────┐
│ Extension Popup UI Results View                          │
│ - Diagnostic metrics (Source, Method, Zero-AI Badge)     │
│ - Formatted cards (Title, Price, MRP, Rating, Image)     │
│ - Copy/Download real backend JSON                        │
│ - Strategy versions & rollback controls                  │
└──────────────────────────────────────────────────────────┘
```

> **Security Guarantee**: The extension contains **zero API keys** and executes **no remote code**. Gemini credentials remain exclusively in the local backend `.env`.

---

## Installation & Setup Instructions

### 1. Start the Local Python Backend
Open Windows PowerShell:

```powershell
Set-Location "E:\PIRATES ALERT V3"

# Activate Python 3.11 Virtual Environment
.\.venv\Scripts\Activate.ps1

# Start the FastAPI server on loopback
.\.venv\Scripts\uvicorn.exe app.main:app --host 127.0.0.1 --port 8000 --reload
```

Verify backend health in your browser: [http://127.0.0.1:8000/health](http://127.0.0.1:8000/health).

---

### 2. Load the Extension into Google Chrome

1. Open Google Chrome.
2. Navigate to `chrome://extensions/`.
3. In the top-right corner, toggle **Developer mode** to **ON**.
4. In the top-left corner, click **Load unpacked**.
5. Select the folder: `E:\PIRATES ALERT V3\extension`.
6. The **PIRATES ALERT** extension card will appear with an extension ID (e.g., `fmlgkmocnhecbjdbagffidkjnidcfkno`).

---

### 3. Pin Extension Origin in Backend (Recommended for Production Hardening)

In development mode, the backend automatically accepts any local `chrome-extension://[a-z]{32}` origin. For production hardening, pin the specific extension ID in `E:\PIRATES ALERT V3\.env`:

```env
CHROME_EXTENSION_ORIGIN=chrome-extension://<your-extension-id-here>
```

Restart the backend server for changes to take effect:
```powershell
# Stop with Ctrl+C, then restart:
.\.venv\Scripts\uvicorn.exe app.main:app --host 127.0.0.1 --port 8000 --reload
```

---

## Step-by-Step Manual Testing Guide

### A. Connection Status Check
1. Click the **PIRATES ALERT** icon in the Chrome toolbar.
2. Observe the header pill: it should display a green dot with **Connected**.
3. If disconnected:
   - Ensure the server is running on `127.0.0.1:8000`.
   - Click the gear icon (⚙️) to verify the Backend API URL is `http://127.0.0.1:8000`.
   - Click the refresh button (↻) to re-check.

### B. Extract Active Tab
1. Browse to any public listing or search page.
2. Click the extension icon. The active tab's URL is automatically pre-filled in the URL input.
3. Choose the Page Classification:
   - **Auto-detect** (default)
   - **E-commerce Catalog**
   - **Generic Listing / Jobs / Directory**
4. Click **Extract**.
5. Observe the progress bar and status message ("Requesting backend Playwright extraction...").
6. Verify extracted records appear with titles, clickable links, images, prices, ratings, and product IDs.

### C. Verify Zero-Call AI Strategy Reuse
1. Click **Extract** a second time on the same unfamiliar listing URL.
2. Inspect the **Diagnostics Panel**:
   - `Strategy Source`: **saved**
   - `AI Calls`: **0 calls (Zero-AI)** highlighted in green!
   - `Execution Time`: Significantly faster than the initial learning pass.

### D. Generic Listing Testing (No Prices/Images)
1. Enter a job board or directory listing URL (or fixture page).
2. Set classification to **Generic Listing**.
3. Click **Extract**.
4. Verify extraction succeeds gracefully without requiring price, MRP, or image fields.

### E. Strategy Lifecycle & Rollback
1. Click **Learned Rules & Strategy Rollback** at the bottom of the popup.
2. Enter the target hostname (e.g. `artisan-shop.org`) and click **Fetch Rules**.
3. View the list of immutable strategy versions with the **ACTIVE** badge.
4. Click **Rollback to this** on an earlier version.
5. Provide a rollback reason (e.g. `Testing manual rollback`) and confirm.
6. Observe the confirmation toast and notice the target version becomes **ACTIVE**.

### F. JSON Export
- Click **Copy JSON**: Verify the notification toast appears and pasted clipboard content matches the real backend response.
- Click **Download**: Verify a `.json` file downloads containing the complete structured response.

### G. Error Handling Verification
- **Internal Chrome URL**: Open `chrome://version` and open the popup. Notice the scheme warning indicating internal URLs cannot be extracted.
- **Backend Stopped**: Stop the Uvicorn server (`Ctrl+C`) and click **Extract**. Verify the error banner explains the backend is unreachable without crashing or exposing raw stack traces.
- **Invalid URL**: Enter `http://127.0.0.1:9999/secret` and click **Extract**. Verify backend SSRF protection rejects the request and the popup displays the sanitized rejection message.

---

## Debugging & Developer Tools

- **Inspect Extension Popup**: Right-click anywhere in the extension popup and select **Inspect**. View the Chrome DevTools Console and Network tabs to inspect extension logs and requests.
- **Inspect Backend Logs**: In your PowerShell terminal, watch the structured JSON `EXTRACTION_EVENT` logs to verify timings, record counts, and zero-call strategy reuses.
