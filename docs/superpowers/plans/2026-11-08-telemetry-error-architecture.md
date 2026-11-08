# Resilient Telemetry & Error Handling Architecture Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement resilient error reporting, hardware-API timestamp decoupling, bounded auto-login lifecycle with backoff, and rotating diagnostic logging in `main.pyw` and `monitor.ini`.

**Architecture:** 
- A standard `logging.handlers.RotatingFileHandler` writes structured diagnostic logs to `daemon.log` (2MB max, 1 backup) and safely replaces stdout/stderr redirection.
- External SLT portal sync state and timestamps (`SLTUpdatedAt`, `Status`, `FetchStatus`) are strictly separated from local laptop telemetry (`LocalUpdatedAt`, `LaptopUsed`). Fake success is eliminated so API/Auth errors persist truthful statuses to `variables.inc` and alert visual states to `monitor.ini`.
- Headless Playwright automation is hardened with a strict 35s timeout, guaranteed `browser.close()` in `finally`, and a 5-minute failure backoff window to prevent endless hangs or spamming.

**Tech Stack:** Python 3.14 (`main.pyw`), Rainmeter INI Skin (`monitor.ini`), Playwright, requests, psutil.

**Spec:** User request and approved brainstorming design on 2026-11-08.

## Global Constraints
- Win32 single-instance mutex `Global\SLTMonitorWidgetSingleInstance` and 1-second `refresh.trigger` IPC consumption must be preserved.
- Rainmeter native measures `[MeasureNetIn]`, `[MeasureNetOut]`, `[MeasurePing]` must remain untouched.
- `variables.inc` writes must continue using atomic write-and-replace (`.tmp` to `.inc`) in `UTF-16 LE with BOM`.
- `.env` configuration keys and `laptop_usage.json` tracking must be preserved.

## Review Focus
1. When API returns 401 or network drops and headless re-auth fails or is backed off, `FetchStatus` must never be "success" and `Status` must reflect `AUTH_ERROR` or `SYNC_FAILED`.
2. Rainmeter badge must switch to `#ColorWarn#` (red) for `AUTH_ERROR`, `SYNC_FAILED`, `ERROR`, or `THROTTLED`.
3. 60-second local laptop ticks must update `LaptopUsed` and `LocalUpdatedAt` without modifying `SLTUpdatedAt`.
4. Playwright browser context must close cleanly without leaving orphaned `chrome.exe` processes on timeout or exception.
5. In headless execution under `pythonw.exe`, logs must flow to `daemon.log` without raising stream errors.

---

### Task 1: Persistent Rotating Diagnostic Logging

**Files:**
- Modify: `D:\Projects\Myslt_meter\main.pyw`

**Step-by-step instructions:**
- [ ] 1. Import `logging` and `logging.handlers.RotatingFileHandler` in `main.pyw`.
- [ ] 2. Configure a module-level logger `logger = logging.getLogger("SLTMonitor")` with file handler targeting `BASE_DIR / "daemon.log"`, `maxBytes=2*1024*1024`, `backupCount=1`, encoding `utf-8`.
- [ ] 3. Implement a stream-to-logger bridge or custom write redirection so `sys.stdout` and `sys.stderr` route uncaught outputs and warnings to `logger.info` and `logger.error` instead of `os.devnull`.
- [ ] 4. Replace unformatted `print(...)` statements with structured `logger.info`, `logger.warning`, and `logger.error` calls.

---

### Task 2: Timestamp Decoupling & Truthful State Reporting in `main.pyw`

**Files:**
- Modify: `D:\Projects\Myslt_meter\main.pyw`

**Step-by-step instructions:**
- [ ] 1. Update `write_variables_inc` to support `data`, `laptop_used`, `fetch_status`, `slt_updated_at`, `local_updated_at`, ensuring `SLTUpdatedAt` records portal fetch time and `UpdatedAt` / `LocalUpdatedAt` records local telemetry tick.
- [ ] 2. Refactor `update_data_cycle()` so that if `fetch_data()` or `get_fresh_auth_headless()` fails, `fetch_status` is explicitly set to `"failed"` (or `"expired"` / `"auth_error"`) and `Status` is set to `"AUTH_ERROR"` (for 401/403) or `"SYNC_FAILED"`.
- [ ] 3. Eliminate the forced `write_variables_inc(last, laptop_used, "success", now)` fallback. If offline, use cached `last_good` values for data display if available, but pass the actual error `fetch_status` and `status`, preserving the previous valid `slt_updated_at`.
- [ ] 4. In the 60-second laptop loop, update only `LaptopUsed` and `LocalUpdatedAt` in `variables.inc` while retaining the current `slt_updated_at`, `FetchStatus`, and `Status`.

---

### Task 3: Resilient Headless Auto-Login Lifecycle with Backoff

**Files:**
- Modify: `D:\Projects\Myslt_meter\main.pyw`

**Step-by-step instructions:**
- [ ] 1. Define backoff tracking variables: `_last_auth_attempt_time = 0` and `AUTH_BACKOFF_SECONDS = 300` (5 minutes).
- [ ] 2. Allow manual IPC triggers (`WANTS_REFRESH` or `consume_refresh_request()`) to bypass the backoff cooldown.
- [ ] 3. Refactor `get_fresh_auth_headless()`:
  - Add strict 35s overall navigation and locator timeouts.
  - Wrap entire Playwright execution in `try ... finally` ensuring `browser.close()` is always called.
  - Prevent uncaught exceptions from bubbling up to terminate the daemon process.
  - Record the attempt timestamp `_last_auth_attempt_time = time.time()`.

---

### Task 4: UI Error States & Badge Alerts in `monitor.ini`

**Files:**
- Modify: `D:\Projects\Myslt_meter\monitor.ini`

**Step-by-step instructions:**
- [ ] 1. Update `[MeasureThrottled]` (or rename to `[MeasureStatus]`) to match `(?i)(THROTTLED|AUTH_ERROR|SYNC_FAILED|ERROR|FAILED)` so any error status triggers `MeterStatusWarn` (red) and hides `MeterStatusOK`.
- [ ] 2. Update `[MeasureFetchStatus]` to match `(?i)^(error|expired|sync failed|session expired|failed|auth_error)$` to trigger `[MeterError]`.
- [ ] 3. Update `[MeterFooter]` to display `SLT: #SLTUpdatedAt#` and include `MeterError` styling for clear visibility when errors occur.
- [ ] 4. Style `[MeterSLTTotalValue]` to visibly flag stale/failed state (e.g., using dynamic font color or clear error banner when `FetchStatus` is not success).

---

### Task 5: End-to-End Verification Cycle

**Files:**
- Run isolated verification script in `scratch/`
- Test IPC trigger and daemon state
- Verify `daemon.log`, `variables.inc`, and `monitor.ini`

**Step-by-step instructions:**
- [ ] 1. Run unit/integration test on `main.pyw` functions (`write_variables_inc`, `update_data_cycle`, error handling, logging).
- [ ] 2. Restart the daemon process gracefully with the new code.
- [ ] 3. Trigger manual refresh via `--refresh` and verify `daemon.log` reflects the exact lifecycle, HTTP responses, and timestamp decoupling.
- [ ] 4. Inspect generated `variables.inc` to verify atomic write, `UTF-16 LE with BOM` format, and truthful status fields.
