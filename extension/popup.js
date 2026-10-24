/**
 * PIRATES ALERT V3 - Chrome Extension Popup Logic
 * Manifest V3 compatible, pure vanilla JavaScript, no remote dependencies.
 */

(function () {
  "use strict";

  const DEFAULT_BACKEND_URL = "http://127.0.0.1:8000";
  let backendApiUrl = DEFAULT_BACKEND_URL;
  let activeAbortController = null;
  let isExtracting = false;
  let lastExtractionData = null;
  let pendingRollbackTarget = null;

  // DOM Elements
  const connectionPill = document.getElementById("connection-pill");
  const connectionText = document.getElementById("connection-text");
  const refreshConnBtn = document.getElementById("refresh-conn-btn");
  const toggleSettingsBtn = document.getElementById("toggle-settings-btn");
  const settingsPanel = document.getElementById("settings-panel");
  const backendUrlInput = document.getElementById("backend-url-input");
  const saveSettingsBtn = document.getElementById("save-settings-btn");
  const connectionAlert = document.getElementById("connection-alert");
  const connectionAlertDetail = document.getElementById("connection-alert-detail");

  const targetUrlInput = document.getElementById("target-url");
  const useActiveTabBtn = document.getElementById("use-active-tab-btn");
  const urlSchemeWarning = document.getElementById("url-scheme-warning");
  const pageTypeSelect = document.getElementById("page-type");
  const extractBtn = document.getElementById("extract-btn");
  const extractBtnText = document.getElementById("extract-btn-text");
  const extractSpinner = document.getElementById("extract-spinner");

  const progressSection = document.getElementById("progress-section");
  const progressStatusText = document.getElementById("progress-status-text");
  const cancelExtractBtn = document.getElementById("cancel-extract-btn");

  const errorSection = document.getElementById("error-section");
  const errorTitle = document.getElementById("error-title");
  const errorDetail = document.getElementById("error-detail");

  const resultsSection = document.getElementById("results-section");
  const recordCountBadge = document.getElementById("record-count-badge");
  const copyJsonBtn = document.getElementById("copy-json-btn");
  const downloadJsonBtn = document.getElementById("download-json-btn");
  const openSourceBtn = document.getElementById("open-source-btn");
  const recordsList = document.getElementById("records-list");
  const emptyState = document.getElementById("empty-state");

  const diagStrategySource = document.getElementById("diag-strategy-source");
  const diagMethod = document.getElementById("diag-method");
  const diagAiCalls = document.getElementById("diag-ai-calls");
  const diagQualityScore = document.getElementById("diag-quality-score");
  const diagStrategyId = document.getElementById("diag-strategy-id");
  const diagExecutionTime = document.getElementById("diag-execution-time");
  const diagWarningsContainer = document.getElementById("diag-warnings-container");

  const toggleStrategiesBtn = document.getElementById("toggle-strategies-btn");
  const strategiesPanel = document.getElementById("strategies-panel");
  const strategyHostInput = document.getElementById("strategy-host-input");
  const fetchStrategiesBtn = document.getElementById("fetch-strategies-btn");
  const strategyVersionsList = document.getElementById("strategy-versions-list");

  const rollbackDialog = document.getElementById("rollback-dialog");
  const rollbackPromptText = document.getElementById("rollback-prompt-text");
  const rollbackReasonInput = document.getElementById("rollback-reason-input");
  const confirmRollbackBtn = document.getElementById("confirm-rollback-btn");
  const cancelRollbackBtn = document.getElementById("cancel-rollback-btn");

  const toast = document.getElementById("toast");

  // Storage helper
  async function loadSettings() {
    try {
      if (typeof chrome !== "undefined" && chrome.storage && chrome.storage.local) {
        const stored = await chrome.storage.local.get(["backendApiUrl"]);
        if (stored.backendApiUrl) {
          backendApiUrl = stored.backendApiUrl.replace(/\/+$/, "");
        }
      } else {
        const stored = localStorage.getItem("backendApiUrl");
        if (stored) backendApiUrl = stored.replace(/\/+$/, "");
      }
    } catch {
      backendApiUrl = DEFAULT_BACKEND_URL;
    }
    backendUrlInput.value = backendApiUrl;
  }

  async function saveSettings() {
    const rawUrl = backendUrlInput.value.trim().replace(/\/+$/, "");
    if (!rawUrl || (!rawUrl.startsWith("http://") && !rawUrl.startsWith("https://"))) {
      showToast("Please enter a valid HTTP/HTTPS URL");
      return;
    }
    backendApiUrl = rawUrl;
    try {
      if (typeof chrome !== "undefined" && chrome.storage && chrome.storage.local) {
        await chrome.storage.local.set({ backendApiUrl: rawUrl });
      } else {
        localStorage.setItem("backendApiUrl", rawUrl);
      }
      showToast("Backend URL saved");
      settingsPanel.classList.add("hidden");
      checkBackendHealth();
    } catch {
      showToast("Failed to save settings");
    }
  }

  // Toast feedback
  let toastTimer = null;
  function showToast(message) {
    if (toastTimer) clearTimeout(toastTimer);
    toast.textContent = message;
    toast.classList.remove("hidden");
    toastTimer = setTimeout(() => {
      toast.classList.add("hidden");
    }, 2500);
  }

  // Sanitize URL for safe link and image injection
  function isSafeHttpUrl(urlString) {
    if (!urlString || typeof urlString !== "string") return false;
    try {
      const parsed = new URL(urlString);
      return parsed.protocol === "http:" || parsed.protocol === "https:";
    } catch {
      return false;
    }
  }

  function extractHostname(urlString) {
    try {
      return new URL(urlString).hostname;
    } catch {
      return "";
    }
  }

  // Backend Health Check
  async function checkBackendHealth() {
    connectionPill.className = "status-pill status-checking";
    connectionText.textContent = "Checking...";
    connectionAlert.classList.add("hidden");

    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 4000);

    try {
      const res = await fetch(`${backendApiUrl}/health`, {
        method: "GET",
        signal: controller.signal,
      });
      clearTimeout(timer);

      if (res.ok) {
        const data = await res.json();
        const isHealthy = data.status === "healthy";
        connectionPill.className = `status-pill ${isHealthy ? "status-connected" : "status-disconnected"}`;
        connectionText.textContent = isHealthy ? "Connected" : "Degraded";

        if (!data.playwrightAvailable || data.databaseAvailable === false) {
          connectionAlert.classList.remove("hidden");
          const reasons = [];
          if (!data.playwrightAvailable) reasons.push("Playwright browser unavailable");
          if (data.databaseAvailable === false) reasons.push("Database unavailable");
          connectionAlertDetail.textContent = reasons.join(", ");
        }
      } else {
        throw new Error(`HTTP ${res.status}`);
      }
    } catch (err) {
      clearTimeout(timer);
      connectionPill.className = "status-pill status-disconnected";
      connectionText.textContent = "Disconnected";
      connectionAlert.classList.remove("hidden");
      connectionAlertDetail.textContent = `Server at ${backendApiUrl} unreachable. Ensure FastAPI backend is running.`;
    }
  }

  // Active Tab URL detection
  async function populateActiveTabUrl() {
    urlSchemeWarning.classList.add("hidden");
    if (typeof chrome === "undefined" || !chrome.tabs || !chrome.tabs.query) {
      return;
    }

    try {
      const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
      if (tab && tab.url) {
        const urlStr = tab.url;
        if (isSafeHttpUrl(urlStr)) {
          targetUrlInput.value = urlStr;
          const host = extractHostname(urlStr);
          if (host) strategyHostInput.value = host;
        } else {
          urlSchemeWarning.classList.remove("hidden");
        }
      }
    } catch {
      // Ignore tab query failure
    }
  }

  // Extraction logic
  async function executeExtraction() {
    const rawUrl = targetUrlInput.value.trim();
    if (!rawUrl) {
      showToast("Please enter a listing page URL");
      targetUrlInput.focus();
      return;
    }

    if (!isSafeHttpUrl(rawUrl)) {
      showToast("Invalid URL scheme. Only HTTP and HTTPS are supported.");
      return;
    }

    const host = extractHostname(rawUrl);
    if (host && !strategyHostInput.value) {
      strategyHostInput.value = host;
    }

    setExtractionLoading(true);
    errorSection.classList.add("hidden");
    resultsSection.classList.add("hidden");
    emptyState.classList.add("hidden");

    activeAbortController = new AbortController();
    const timeoutId = setTimeout(() => {
      if (activeAbortController) {
        activeAbortController.abort();
      }
    }, 45000);

    const payload = {
      url: rawUrl,
      pageType: pageTypeSelect.value,
    };

    try {
      progressStatusText.textContent = "Requesting backend Playwright extraction...";
      const res = await fetch(`${backendApiUrl}/api/v1/extract`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-Request-ID": `ext_${Date.now().toString(36)}`,
        },
        body: JSON.stringify(payload),
        signal: activeAbortController.signal,
      });

      clearTimeout(timeoutId);
      setExtractionLoading(false);

      if (!res.ok) {
        let errMessage = `Server error (${res.status})`;
        try {
          const errData = await res.json();
          if (errData && errData.detail) {
            errMessage = typeof errData.detail === "string" ? errData.detail : JSON.stringify(errData.detail);
          }
        } catch {
          // Fallback message
        }
        showError("Extraction Failed", errMessage);
        return;
      }

      const data = await res.json();
      lastExtractionData = data;
      renderExtractionResults(data);
    } catch (err) {
      clearTimeout(timeoutId);
      setExtractionLoading(false);

      if (err.name === "AbortError") {
        showError("Extraction Timed Out", "Backend Playwright request timed out or was cancelled by user.");
      } else {
        showError("Network Error", `Could not connect to backend at ${backendApiUrl}. Check if server is running.`);
      }
    }
  }

  function setExtractionLoading(loading) {
    isExtracting = loading;
    extractBtn.disabled = loading;
    if (loading) {
      extractBtnText.textContent = "Extracting...";
      extractSpinner.classList.remove("hidden");
      progressSection.classList.remove("hidden");
      progressStatusText.textContent = "Loading page with Chromium & analyzing DOM...";
    } else {
      extractBtnText.textContent = "Extract";
      extractSpinner.classList.add("hidden");
      progressSection.classList.add("hidden");
      activeAbortController = null;
    }
  }

  function showError(title, message) {
    errorTitle.textContent = title;
    errorDetail.textContent = message;
    errorSection.classList.remove("hidden");
  }

  // Render results
  function renderExtractionResults(data) {
    const records = Array.isArray(data.records) ? data.records : [];
    const metadata = data.metadata || {};

    resultsSection.classList.remove("hidden");
    recordCountBadge.textContent = `${records.length} ${records.length === 1 ? "record" : "records"}`;

    // Diagnostics
    const stratSource = metadata.strategySource || "deterministic";
    diagStrategySource.textContent = stratSource;
    diagMethod.textContent = metadata.extractionMethod || "unknown";

    const aiCalls = typeof metadata.aiCalls === "number" ? metadata.aiCalls : 0;
    diagAiCalls.textContent = aiCalls === 0 ? "0 calls (Zero-AI)" : `${aiCalls} ${aiCalls === 1 ? "call" : "calls"}`;
    if (aiCalls === 0) {
      diagAiCalls.className = "diag-val diag-badge-zero";
    } else {
      diagAiCalls.className = "diag-val";
    }

    const qScore = metadata.qualityScore;
    diagQualityScore.textContent = typeof qScore === "number" ? `${Math.round(qScore * 100)}%` : "N/A";

    const stratId = metadata.strategyId || "-";
    const stratVer = metadata.strategyVersion ? `v${metadata.strategyVersion}` : "";
    diagStrategyId.textContent = stratVer ? `${stratId} (${stratVer})` : stratId;
    diagStrategyId.title = stratId;

    const timeMs = metadata.executionTimeMs;
    diagExecutionTime.textContent = typeof timeMs === "number" ? `${Math.round(timeMs)} ms` : "-";

    // Warnings
    const warnings = Array.isArray(metadata.warnings) ? metadata.warnings : [];
    diagWarningsContainer.replaceChildren();
    if (warnings.length > 0 || metadata.recoveryStatus) {
      diagWarningsContainer.classList.remove("hidden");
      if (metadata.recoveryStatus) {
        const recP = document.createElement("p");
        recP.textContent = `Recovery: ${metadata.recoveryStatus}`;
        diagWarningsContainer.appendChild(recP);
      }
      for (const w of warnings.slice(0, 3)) {
        const warnP = document.createElement("p");
        warnP.textContent = `• ${w}`;
        diagWarningsContainer.appendChild(warnP);
      }
    } else {
      diagWarningsContainer.classList.add("hidden");
    }

    // Render cards
    recordsList.replaceChildren();
    if (records.length === 0) {
      emptyState.classList.remove("hidden");
      return;
    }
    emptyState.classList.add("hidden");

    for (const item of records) {
      const card = document.createElement("div");
      card.className = "record-card";

      // Thumbnail
      const hasImage = isSafeHttpUrl(item.imageUrl);
      if (hasImage) {
        const img = document.createElement("img");
        img.className = "record-thumbnail";
        img.src = item.imageUrl;
        img.alt = item.title || "Product image";
        img.loading = "lazy";
        img.onerror = function () {
          const fallback = document.createElement("div");
          fallback.className = "record-thumbnail-placeholder";
          fallback.textContent = "📦";
          card.replaceChild(fallback, img);
        };
        card.appendChild(img);
      } else {
        const placeholder = document.createElement("div");
        placeholder.className = "record-thumbnail-placeholder";
        placeholder.textContent = "📦";
        card.appendChild(placeholder);
      }

      // Body
      const body = document.createElement("div");
      body.className = "record-body";

      // Title link
      const titleLink = document.createElement("a");
      titleLink.className = "record-title-link";
      titleLink.textContent = item.title || "(Untitled record)";
      if (isSafeHttpUrl(item.url)) {
        titleLink.href = item.url;
        titleLink.target = "_blank";
        titleLink.rel = "noopener noreferrer";
      }
      body.appendChild(titleLink);

      // Pricing row
      const priceVal = item.sellingPrice;
      const mrpVal = item.mrp;
      const currency = item.currency || "";

      if (priceVal !== null && priceVal !== undefined) {
        const priceRow = document.createElement("div");
        priceRow.className = "record-pricing-row";

        const priceSpan = document.createElement("span");
        priceSpan.className = "record-price";
        priceSpan.textContent = currency ? `${currency} ${priceVal}` : `${priceVal}`;
        priceRow.appendChild(priceSpan);

        if (mrpVal !== null && mrpVal !== undefined && mrpVal > priceVal) {
          const mrpSpan = document.createElement("span");
          mrpSpan.className = "record-mrp";
          mrpSpan.textContent = currency ? `${currency} ${mrpVal}` : `${mrpVal}`;
          priceRow.appendChild(mrpSpan);
        }

        body.appendChild(priceRow);
      }

      // Rating & Reviews
      if (item.rating !== null && item.rating !== undefined) {
        const ratingRow = document.createElement("div");
        ratingRow.className = "record-rating-badge";
        const revCount = item.reviewCount ? ` (${item.reviewCount})` : "";
        ratingRow.textContent = `★ ${item.rating}${revCount}`;
        body.appendChild(ratingRow);
      }

      // Meta Pills (Product ID and extra fields)
      const metaRow = document.createElement("div");
      metaRow.className = "record-meta-row";

      if (item.productId) {
        const idBadge = document.createElement("span");
        idBadge.className = "record-meta-pill";
        idBadge.textContent = `ID: ${item.productId}`;
        metaRow.appendChild(idBadge);
      }

      if (item.extra && typeof item.extra === "object") {
        for (const [k, v] of Object.entries(item.extra)) {
          if (v && typeof v !== "object") {
            const extraBadge = document.createElement("span");
            extraBadge.className = "record-meta-pill";
            extraBadge.textContent = `${k}: ${String(v).slice(0, 24)}`;
            metaRow.appendChild(extraBadge);
          }
        }
      }

      if (metaRow.hasChildNodes()) {
        body.appendChild(metaRow);
      }

      card.appendChild(body);
      recordsList.appendChild(card);
    }
  }

  // Copy JSON action
  async function copyExtractedJson() {
    if (!lastExtractionData) {
      showToast("No extraction data to copy");
      return;
    }
    const jsonStr = JSON.stringify(lastExtractionData, null, 2);
    try {
      await navigator.clipboard.writeText(jsonStr);
      showToast("Copied JSON to clipboard!");
    } catch {
      showToast("Clipboard access denied");
    }
  }

  // Download JSON action
  function downloadExtractedJson() {
    if (!lastExtractionData) {
      showToast("No extraction data to download");
      return;
    }
    const jsonStr = JSON.stringify(lastExtractionData, null, 2);
    const blob = new Blob([jsonStr], { type: "application/json" });
    const blobUrl = URL.createObjectURL(blob);
    const host = extractHostname(lastExtractionData.sourceUrl || "extraction") || "extraction";
    const filename = `pirates_alert_${host}_${Date.now()}.json`;

    const a = document.createElement("a");
    a.href = blobUrl;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(blobUrl);
    showToast("Downloaded JSON file");
  }

  function openSourceUrl() {
    const rawUrl = targetUrlInput.value.trim();
    if (isSafeHttpUrl(rawUrl)) {
      window.open(rawUrl, "_blank", "noopener,noreferrer");
    } else {
      showToast("Invalid source URL");
    }
  }

  // Strategy Management & Rollback
  async function fetchStrategies() {
    const hostname = strategyHostInput.value.trim();
    if (!hostname) {
      showToast("Please enter a hostname");
      return;
    }

    strategyVersionsList.replaceChildren();
    const loadingP = document.createElement("p");
    loadingP.className = "caption-muted";
    loadingP.textContent = "Fetching strategy versions from backend...";
    strategyVersionsList.appendChild(loadingP);

    try {
      const url = `${backendApiUrl}/api/v1/strategies?hostname=${encodeURIComponent(hostname)}`;
      const res = await fetch(url);
      if (!res.ok) {
        throw new Error(`HTTP ${res.status}`);
      }
      const data = await res.json();
      renderStrategyVersions(data.versions || [], hostname);
    } catch (err) {
      strategyVersionsList.replaceChildren();
      const errP = document.createElement("p");
      errP.className = "caption-muted text-danger";
      errP.textContent = `Failed to fetch versions: ${err.message}`;
      strategyVersionsList.appendChild(errP);
    }
  }

  function renderStrategyVersions(versions, hostname) {
    strategyVersionsList.replaceChildren();
    rollbackDialog.classList.add("hidden");

    if (versions.length === 0) {
      const emptyP = document.createElement("p");
      emptyP.className = "caption-muted";
      emptyP.textContent = `No strategy versions found for ${hostname}.`;
      strategyVersionsList.appendChild(emptyP);
      return;
    }

    for (const v of versions) {
      const item = document.createElement("div");
      item.className = "strategy-version-item";

      const info = document.createElement("div");
      info.className = "strategy-version-info";

      const line1 = document.createElement("div");
      line1.style.display = "flex";
      line1.style.alignItems = "center";
      line1.style.gap = "6px";

      const num = document.createElement("span");
      num.className = "strategy-version-num";
      num.textContent = `Version ${v.version}`;
      line1.appendChild(num);

      if (v.isActive) {
        const activeBadge = document.createElement("span");
        activeBadge.className = "badge badge-success";
        activeBadge.textContent = "ACTIVE";
        line1.appendChild(activeBadge);
      }

      info.appendChild(line1);

      const line2 = document.createElement("div");
      line2.className = "form-caption";
      const qualityScore = v.validationMetrics && typeof v.validationMetrics.qualityScore === "number"
        ? `Score: ${Math.round(v.validationMetrics.qualityScore * 100)}%`
        : `Status: ${v.validationStatus}`;
      line2.textContent = `${v.pageType} • ${v.updateSource} • ${qualityScore}`;
      info.appendChild(line2);

      item.appendChild(info);

      if (!v.isActive && v.validationStatus === "passed") {
        const rbBtn = document.createElement("button");
        rbBtn.className = "btn btn-sm btn-secondary";
        rbBtn.textContent = "Rollback to this";
        rbBtn.addEventListener("click", () => {
          promptRollback(hostname, v.pageType, v.version);
        });
        item.appendChild(rbBtn);
      }

      strategyVersionsList.appendChild(item);
    }
  }

  function promptRollback(hostname, pageType, targetVersion) {
    pendingRollbackTarget = { hostname, pageType, targetVersion };
    rollbackPromptText.textContent = `Roll back active strategy for ${hostname} to Version #${targetVersion}?`;
    rollbackReasonInput.value = "Manual rollback via Chrome extension";
    rollbackDialog.classList.remove("hidden");
    rollbackReasonInput.focus();
  }

  async function executeRollback() {
    if (!pendingRollbackTarget) return;

    const reason = rollbackReasonInput.value.trim();
    if (reason.length < 3) {
      showToast("Reason must be at least 3 characters");
      rollbackReasonInput.focus();
      return;
    }

    confirmRollbackBtn.disabled = true;
    confirmRollbackBtn.textContent = "Rolling back...";

    try {
      const res = await fetch(`${backendApiUrl}/api/v1/strategies/rollback`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          hostname: pendingRollbackTarget.hostname,
          pageType: pendingRollbackTarget.pageType,
          targetVersion: pendingRollbackTarget.targetVersion,
          reason: reason,
        }),
      });

      confirmRollbackBtn.disabled = false;
      confirmRollbackBtn.textContent = "Confirm Rollback";

      if (!res.ok) {
        const errData = await res.json().catch(() => ({}));
        showToast(`Rollback failed: ${errData.detail || res.statusText}`);
        return;
      }

      const resData = await res.json();
      showToast(`Activated Version ${resData.activeVersion}!`);
      rollbackDialog.classList.add("hidden");
      fetchStrategies();
    } catch (err) {
      confirmRollbackBtn.disabled = false;
      confirmRollbackBtn.textContent = "Confirm Rollback";
      showToast(`Network error during rollback: ${err.message}`);
    }
  }

  // Event Listeners
  function attachEventListeners() {
    // Settings
    toggleSettingsBtn.addEventListener("click", () => {
      settingsPanel.classList.toggle("hidden");
      if (!settingsPanel.classList.contains("hidden")) {
        backendUrlInput.focus();
      }
    });
    saveSettingsBtn.addEventListener("click", saveSettings);
    refreshConnBtn.addEventListener("click", checkBackendHealth);

    // URL & Extraction
    useActiveTabBtn.addEventListener("click", populateActiveTabUrl);
    targetUrlInput.addEventListener("input", () => {
      urlSchemeWarning.classList.add("hidden");
      const host = extractHostname(targetUrlInput.value.trim());
      if (host) strategyHostInput.value = host;
    });

    document.getElementById("extraction-form").addEventListener("submit", (e) => {
      e.preventDefault();
      executeExtraction();
    });

    cancelExtractBtn.addEventListener("click", () => {
      if (activeAbortController) {
        activeAbortController.abort();
      }
    });

    // Export actions
    copyJsonBtn.addEventListener("click", copyExtractedJson);
    downloadJsonBtn.addEventListener("click", downloadExtractedJson);
    openSourceBtn.addEventListener("click", openSourceUrl);

    // Strategy Accordion & Rollback
    toggleStrategiesBtn.addEventListener("click", () => {
      const isHidden = strategiesPanel.classList.toggle("hidden");
      toggleStrategiesBtn.setAttribute("aria-expanded", String(!isHidden));
      if (!isHidden && strategyHostInput.value.trim()) {
        fetchStrategies();
      }
    });

    fetchStrategiesBtn.addEventListener("click", fetchStrategies);
    strategyHostInput.addEventListener("keydown", (e) => {
      if (e.key === "Enter") {
        e.preventDefault();
        fetchStrategies();
      }
    });

    confirmRollbackBtn.addEventListener("click", executeRollback);
    cancelRollbackBtn.addEventListener("click", () => {
      rollbackDialog.classList.add("hidden");
      pendingRollbackTarget = null;
    });
  }

  // Initialization
  async function init() {
    attachEventListeners();
    await loadSettings();
    checkBackendHealth();
    populateActiveTabUrl();
  }

  document.addEventListener("DOMContentLoaded", init);
})();
