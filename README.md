# SLT Fiber Rainmeter Telemetry Monitor

A standalone background desktop telemetry monitor for **SLT Broadband (MySLT)** and local system bandwidth, rendered as a dedicated Rainmeter skin. A lightweight Python daemon handles SLT broadband billing updates and local laptop usage telemetry, while native Rainmeter measures handle real-time network throughput and ping.

![Python](https://img.shields.io/badge/Python-3.12%2B-3776AB?logo=python&logoColor=white)
![Rainmeter](https://img.shields.io/badge/Rainmeter-Skin-1E6FBA)
![Playwright](https://img.shields.io/badge/Playwright-Stealth%20Headless-2EAD33)
![Windows](https://img.shields.io/badge/Windows-10%2F11-0078D4?logo=windows&logoColor=white)
![License](https://img.shields.io/badge/License-MIT-lightgrey)

---

## Table of Contents

- [System Architecture](#system-architecture)
- [Separation of Concerns](#separation-of-concerns)
- [Operating Model & Daemon Lifecycle](#operating-model--daemon-lifecycle)
- [Local PC Usage Telemetry](#local-pc-usage-telemetry)
- [Fast-Path Manual Refresh & IPC Signaling](#fast-path-manual-refresh--ipc-signaling)
- [Silent Headless Authentication](#silent-headless-authentication)
- [Data Bridge & UI Synchronization](#data-bridge--ui-synchronization)
- [Prerequisites & Dependencies](#prerequisites--dependencies)
- [Setup & Windows Startup](#setup--windows-startup)
- [Repository Structure](#repository-structure)
- [`variables.inc` Schema](#variablesinc-schema)
- [Troubleshooting & FAQ](#troubleshooting--faq)
- [Security Practices](#security-practices)
- [License](#license)

---

## System Architecture

```mermaid
flowchart LR
    A["MySLT Portal / Omniscapp API<br/><i>BBVAS UsageSummary</i>"] -->|"30-min cycle / trigger"| B["Background Daemon<br/><i>main.pyw</i><br/>Single-Instance Mutex"]
    C["psutil.net_io_counters()<br/><i>Local Laptop Usage</i>"] -->|"60-s cycle"| B
    B --> D["UTF-16 LE Atomic Write<br/><i>live skin variables.inc</i>"]
    D --> E["Rainmeter Skin<br/><i>monitor.ini</i>"]
    F["Native Measures<br/><i>NetIn / NetOut / Ping (1.1.1.1)</i>"] -->|"1-second tick"| E
    E --> G["Desktop UI<br/><i>SLT Telemetry Widget</i>"]
    E -.->|"double-click --refresh<br/>refresh.trigger"| B
    B --> H["!Refresh SLTMonitor"]
    H --> E
```

---

## Separation of Concerns

1. **Local Metrics (Native Rainmeter):**
   - Real-time Download Speed (`NetIn`, `[MeasureNetInMB]`)
   - Real-time Upload Speed (`NetOut`, `[MeasureNetOutMB]`)
   - Real-time Latency / Ping (`PingPlugin` targeting Cloudflare `1.1.1.1`)
   - Refreshed continuously every 1000ms with zero Python overhead or external HTTP requests.
2. **External Billing & Account Metrics (Python Daemon):**
   - Periodic SLT portal polling every **30 minutes** (1800s) or on-demand double-click.
   - Robust parsing supporting SLT Unlimited Fiber schemas (`UNLIMITED HOME`).
3. **Local PC Data Tracking (Python Daemon + psutil):**
   - Tracks total laptop byte consumption across system sessions.
   - Flushes accumulated byte deltas to `laptop_usage.json` every **60 seconds**.
   - Automatically resets on the 1st of every month.

---

## Operating Model & Daemon Lifecycle

- **Windowless background daemon** — production runs under `pythonw.exe`. All paths are dynamically anchored via `pathlib.Path(__file__).resolve().parent`.
- **OS-level single-instance mutex** — `main.pyw` acquires `Global\SLTMonitorWidgetSingleInstance` before third-party imports. Duplicates terminate in milliseconds.
- **Dual Polling Cadence:**
  - **SLT Portal HTTP polling:** 30 minutes (`REFRESH_SECONDS = 1800`) or instant manual trigger.
  - **Local Laptop Usage tracking:** 60 seconds (`LAPTOP_TICK_SECONDS = 60`).
  - **IPC Trigger Polling:** 1-second interval (`consume_refresh_request()`).
- **Fail-safe auto-promotion** — if no daemon is running when a manual refresh is invoked, the process acquires the mutex, performs an initial sync immediately, and smoothly transitions into the background daemon loop.

---

## Local PC Usage Telemetry

- Tracks system-wide ingress and egress via `psutil.net_io_counters()`.
- State is persisted in `laptop_usage.json`:
  ```json
  {
    "year_month": "2026-11",
    "accumulated_bytes": 1048576000,
    "last_counter_bytes": 35213851
  }
  ```
- Handles reboot delta calculation without double-counting past uptime.
- Formatted as `LaptopUsed=<value> GB` in `variables.inc`.

---

## Fast-Path Manual Refresh & IPC Signaling

Double-clicking the desktop widget runs:

```powershell
"D:\Projects\Myslt_meter\.venv\Scripts\pythonw.exe" "D:\Projects\Myslt_meter\main.pyw" --refresh
```

- **Active daemon:** Mutex held → touches `refresh.trigger` and exits in ~0.1s. The running daemon wakes on its 1s poll, clears the trigger, executes an immediate portal sync and laptop usage update, and refreshes the skin.
- **Inactive daemon:** Mutex free → process acquires mutex, does initial sync, and auto-promotes into the daemon loop.

---

## Silent Headless Authentication

- `get_fresh_auth_headless()` runs Playwright Chromium with `headless=True` and applies `playwright-stealth`.
- Configured with `--disable-blink-features=AutomationControlled` to evade anti-bot checks.
- Sniffs `Authorization` header off the `UsageSummary` API call and captures Incapsula cookies, updating `.env` silently without intrusive popups.

---

## Data Bridge & UI Synchronization

- **Atomic file writes:** `variables.inc` is written via `.tmp` + `os.replace` to prevent torn reads.
- **Native UTF-16 LE BOM encoding:** written with `encoding="utf-16"`, matching Rainmeter standards.
- **Direct skin target:** written directly to `%USERPROFILE%\Documents\Rainmeter\Skins\SLTMonitor\variables.inc`.
- **Lightweight skin refresh:** Python notifies Rainmeter using `Rainmeter.exe !Refresh SLTMonitor`.
- **Static meters optimization:** SLT text meters use `UpdateDivider=-1` to ensure zero CPU cycles are spent re-rendering static strings.

---

## Prerequisites & Dependencies

| Requirement | Notes |
|---|---|
| Windows 10/11 (64-bit) | Mutex, `pythonw.exe`, Startup folder, Rainmeter bang |
| [Rainmeter](https://www.rainmeter.net/) | Skin host |
| Python 3.12+ | Python runtime |
| `requests >= 2.34.2` | SLT Portal HTTP polling |
| `psutil >= 7.0.0` | Local system data consumption tracking |
| `playwright >= 1.40.0` | Headless browser automation |
| `playwright-stealth >= 2.0.0` | Headless bot detection evasion |

---

## Setup & Windows Startup

### 1. Configure credentials in `.env`

```powershell
Copy-Item .env.example .env
notepad .env
```

```ini
SLT_SUBSCRIBER_ID=94XXXXXXXXX
SLT_AUTHORIZATION=bearer PASTE_FRESH_TOKEN_HERE
SLT_X_IBM_CLIENT_ID=b7402e9d66808f762ccedbe42c20668e
SLT_COOKIE=incap_ses_...; visid_incap_...
```

### 2. Silent Startup on Windows Boot (`launch_daemon.vbs`)

A silent VBS launcher (`launch_daemon.vbs`) is provided to start the daemon in the background with zero terminal windows:
```vbs
Set WshShell = CreateObject("WScript.Shell")
WshShell.Run "pythonw.exe """ & WshShell.CurrentDirectory & "\main.pyw""", 0, False
```

The shortcut `SLTMonitor.lnk` in `$env:APPDATA\Microsoft\Windows\Start Menu\Programs\Startup\` ensures the daemon launches silently on every Windows login.

---

## Repository Structure

```text
D:\Projects\Myslt_meter\
├── main.pyw              # Background daemon (mutex, timers, psutil, headless auth)
├── monitor.ini           # Rainmeter skin definition (native bandwidth, ping, SLT UI)
├── launch_daemon.vbs     # Silent VBS launcher for Windows startup
├── laptop_usage.json     # Monthly accumulated PC byte tracking (gitignored)
├── data.json             # Last successful parsed API payload (gitignored)
├── .env                  # Credentials cache (gitignored)
├── .env.example          # Sanitized template
├── pyproject.toml        # Project dependencies (requests, psutil, playwright, playwright-stealth)
├── SLTMonitor.lnk        # Startup shortcut for Windows logon
└── README.md             # Documentation
```

---

## `variables.inc` Schema

```ini
[Variables]
PackageName=UNLIMITED HOME
SLTTotalUsed=49.2 GB
LaptopUsed=0.6 GB
Status=NORMAL
Unit=GB
UpdatedAt=05:50:00 PM
FetchStatus=success
```

---

## Troubleshooting & FAQ

**How do I test or verify the running daemon?**
```powershell
Get-Process pythonw | Where-Object { $_.Path -like "*Myslt_meter*" }
```

**Restarting the daemon manually:**
```powershell
Get-Process pythonw | Where-Object { $_.Path -like "*Myslt_meter*" } | Stop-Process -Force
wscript.exe "D:\Projects\Myslt_meter\launch_daemon.vbs"
```

---

## Security Practices

- `.env.example` contains only generic placeholders (`94XXXXXXXXX`).
- `.env`, `laptop_usage.json`, and `data.json` are strictly gitignored.
- No plaintext passwords or tokens are checked into version control.

---

## License

MIT
