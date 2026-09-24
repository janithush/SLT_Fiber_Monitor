# SLT Fiber Rainmeter Data Monitor

A standalone background desktop monitor for **SLT Broadband (MySLT)** usage quotas, rendered as a dedicated
Rainmeter skin. A lightweight Python daemon polls the Omniscapp `UsageSummary` API every 15 minutes and feeds
live quota data — Standard remaining/limit, Total remaining/limit, throttling status, expiry, and timestamps —
straight to your desktop. No WebParser, no RegEx: the skin stays dumb, the daemon does the work.

![Python](https://img.shields.io/badge/Python-3.12%2B-3776AB?logo=python&logoColor=white)
![Rainmeter](https://img.shields.io/badge/Rainmeter-Skin-1E6FBA)
![Playwright](https://img.shields.io/badge/Playwright-Auto%20Login-2EAD33)
![Windows](https://img.shields.io/badge/Windows-10%2F11-0078D4?logo=windows&logoColor=white)
![License](https://img.shields.io/badge/License-MIT-lightgrey)

---

## Preview

![SLT Fiber Widget](docs/screenshots/slt_widget.png)

*Live widget: package name, Standard + Total quotas, throttling indicator, updated timestamp.*

*Throttled state: red `THROTTLED - SPEED REDUCED` alert when the Standard quota is exhausted.*


---

## System Architecture

```mermaid
flowchart LR
    A["MySLT Portal / Omniscapp API<br/><i>BBVAS UsageSummary</i>"] --> B["Background Daemon<br/><i>main.pyw</i><br/>Single-Instance Mutex<br/><i>Global\\SLTMonitorWidgetSingleInstance</i>"]
    B --> C["Session / Cookie Handler<br/><i>Playwright re-login +<br/>cached Bearer in .env</i>"]
    C --> D["UTF-16 LE Atomic Write<br/><i>tmp + os.replace</i>"]
    D --> E["Rainmeter Skin<br/><i>monitor.ini + variables.inc</i>"]
    E --> F["Desktop UI<br/><i>SLT FIBER widget</i>"]
```

**Data flow in plain terms:**

1. `main.pyw` wakes every **15 minutes** (`REFRESH_SECONDS = 900`), hot-reloads `.env`, and issues an
   authenticated `GET` to `https://omniscapp.slt.lk/slt/ext/api/BBVAS/UsageSummary?subscriberID=…` with the
   cached Bearer token, `X-IBM-Client-Id`, and Incapsula cookies.
2. On `401/403` (or an Incapsula block page), the daemon launches a **Playwright** Chromium window for a
   one-time MySLT login, sniffs the fresh `Authorization` header off the `UsageSummary` request, persists it
   (plus cookies) back to `.env`, and retries once. A manual DevTools token paste into `.env` always works as
   fallback — no restart needed.
3. The parsed payload (package name, Standard/Total remaining + limits, `THROTTLED` status, expiry,
   reported time) is written **atomically** (`*.tmp` + `os.replace`) as **UTF-16 LE** to both
   `Documents\Rainmeter\Skins\SLTMonitor\variables.inc` (live feed) and the repo-local backup
   `variables.inc`, then Rainmeter is nudged with `Rainmeter.exe !Refresh SLTMonitor`.
4. `monitor.ini` includes `variables.inc` and renders the widget; a built-in 15-minute self-refresh acts as a
   fallback if the refresh bang ever fails.

---

## Key Engineering Highlights

- **Bulletproof background execution** — a Windows named mutex (`Global\SLTMonitorWidgetSingleInstance`)
  acquired before any third-party import, so duplicate launches (double-clicks, Startup + manual start,
  editor runs) terminate in milliseconds instead of spawning racing writers.
- **Atomic file writes** — every `variables.inc` / `data.json` / `.env` update goes through temp-file +
  `os.replace`, so Rainmeter never reads a torn half-written file.
- **Strict UTF-16 LE encoding** — `monitor.ini` and `variables.inc` are written UTF-16 LE with BOM,
  Rainmeter's native encoding, eliminating mojibake and parse failures.
- **Real-time status alerting** — the skin swaps a green status line for a red
  `THROTTLED - SPEED REDUCED` indicator the moment the API reports quota exhaustion.
- **12-hour AM/PM timestamping** — `Updated: 10:49:50 PM` style stamps via `%I:%M:%S %p`, rendered cleanly
  on the widget footer.
- **Dual-mode authentication** — automated Playwright re-login on session expiry, backed by a persistent
  gitignored `.env` cache that also accepts manual token pastes (hot-reloaded each cycle).
- **Graceful degradation** — on transient failures the last-good values stay on screen with an
  `error`/`expired` banner instead of blanking the widget.

---

## Prerequisites

| Requirement | Notes |
|---|---|
| Windows 10/11 | Mutex, `pythonw.exe`, Startup folder, Rainmeter bang |
| [Rainmeter](https://www.rainmeter.net/) (latest) | Skin host |
| Python 3.12+ | See `pyproject.toml` (`requires-python = ">=3.12"`) |
| Virtual environment | Repo-local `.venv` recommended |
| Playwright + Chromium | `pip install playwright` then `playwright install chromium` |

---

## Quick Start

### 1. Clone and enter the repo

```powershell
git clone <your-remote-url> Myslt_meter
Set-Location Myslt_meter
```

### 2. Configure credentials via `.env`

```powershell
Copy-Item .env.example .env
notepad .env
```

Fill in the values (grab them once from MySLT in your browser: **F12 → Network → `UsageSummary`**
→ copy the `Authorization` and `Cookie` request headers):

```ini
SLT_SUBSCRIBER_ID=94XXXXXXXXX
SLT_AUTHORIZATION=bearer PASTE_FRESH_TOKEN_HERE
SLT_X_IBM_CLIENT_ID=YOUR_IBM_CLIENT_ID
SLT_COOKIE=incap_ses_972_3243345=PASTE; visid_incap_3243345=PASTE
```

> `.env` is gitignored and **hot-reloaded every poll cycle** — pasting a fresh token takes effect
> automatically without restarting the daemon.

### 3. Install dependencies (via `pyproject.toml`)

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -e .
```

### 4. Install the Playwright browser

```powershell
.\.venv\Scripts\playwright.exe install chromium
```

### 5. Link the Rainmeter skin

Copy (or symlink) the skin folder so Rainmeter picks it up at
`Documents\Rainmeter\Skins\SLTMonitor\` containing at minimum:

- `monitor.ini` — widget layout (UTF-16 LE)
- `variables.inc` — live data feed, generated by the daemon (UTF-16 LE)

Then in Rainmeter: **Manage → Skins → SLTMonitor → Load**, or run:

```powershell
& "C:\Program Files\Rainmeter\Rainmeter.exe" "!Refresh" "SLTMonitor"
```

### 6. Start the daemon

```powershell
$root = $PWD.Path
Start-Process -FilePath "$root\.venv\Scripts\pythonw.exe" -ArgumentList "`"$root\main.pyw`"" -WorkingDirectory "$root"
```

Silent, console-free (`pythonw`), first poll runs immediately, then every 15 minutes.

### 7. Autostart on Windows logon (`shell:startup`)

Create a shortcut (a ready-made `SLTMonitor.lnk` ships in the repo root — copy it):

```powershell
Copy-Item ".\SLTMonitor.lnk" ([Environment]::GetFolderPath("Startup"))
```

Or craft it manually: **Win+R → `shell:startup`** → new shortcut with (replace `<repo>` with
your clone location, e.g. whatever `$PWD.Path` printed in step 6):

- Target: `<repo>\.venv\Scripts\pythonw.exe "<repo>\main.pyw"`
- Start in: `<repo>`

The single-instance mutex makes double launches harmless.

---

## Troubleshooting & FAQ

**Session expired / widget shows “Session expired - re-login running…”?**
The SLT Bearer token and Incapsula cookies rotate regularly — this is expected. The daemon detects
`401/403` (or Incapsula HTML) and opens a Chromium window: log in to MySLT once and it auto-captures the
fresh `Authorization` + cookies into `.env` and retries. Prefer the manual route? Open DevTools
(**F12 → Network → `UsageSummary`**), copy the `Authorization` and `Cookie` headers into `.env`, save —
the next cycle (≤15 min) picks them up with no restart.

**How do I verify the daemon is running?**
```powershell
Get-Process pythonw | Where-Object { $_.Path -like "*Myslt_meter*" } | Select-Object Id, Path
```
One row = healthy. The mutex guarantees there can only ever be one writer; launching `main.pyw` again
prints `Another SLTMonitor instance is already running` and exits instantly.

**Widget shows stale data or blanks?**
Check `Documents\Rainmeter\Skins\SLTMonitor\variables.inc` — if `FetchStatus=error/expired` with
last-good numbers preserved, the API call is failing (network down or session dead). Check
`data.json` in the repo root for the last successful payload, then refresh the session as above.

**Garbled characters in the skin?**
Both `monitor.ini` and `variables.inc` must be **UTF-16 LE with BOM**. Never save them as UTF-8 from a
text editor — let `main.pyw` regenerate `variables.inc`, and if you hand-edit `monitor.ini`, re-save it
as UTF-16 LE.

**Rainmeter not picking up changes?**
```powershell
& "C:\Program Files\Rainmeter\Rainmeter.exe" "!Refresh" "SLTMonitor"
```
The skin also self-refreshes every 15 minutes as a fallback.

---

## Repository Layout

```text
D:\Projects\Myslt_meter\
├── main.pyw          # Background daemon (mutex, polling, Playwright re-login, atomic writes)
├── .env              # Secrets cache (gitignored, hot-reloaded)
├── .env.example      # Template for fresh setups
├── variables.inc     # Local backup of the live Rainmeter feed (UTF-16 LE)
├── data.json         # Last successful API payload (debug/inspection)
├── SLTMonitor.lnk    # Ready-made Startup shortcut (pythonw + main.pyw)
├── pyproject.toml    # Dependencies (requests, playwright)
├── docs/
│   └── screenshots/
│       ├── slt_widget.png           # Widget preview (normal state)
│       └── slt_widget_throttled.png # Widget preview (throttled state)
└── README.md         # This file
```

*Standalone companion to the DialogMonitor project — same hardened daemon pattern, dedicated `SLTMonitor`
Rainmeter skin.*
