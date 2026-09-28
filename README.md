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

## Table of Contents

- [Preview](#preview)
- [System Architecture](#system-architecture)
- [Operating Model \& Daemon Lifecycle](#operating-model--daemon-lifecycle)
- [Fast-Path Manual Refresh \& IPC Signaling](#fast-path-manual-refresh--ipc-signaling)
- [Authentication \& Session Recovery](#authentication--session-recovery)
- [Data Bridge \& UI Synchronization](#data-bridge--ui-synchronization)
- [Prerequisites](#prerequisites)
- [Setup \& Installation](#setup--installation)
- [Windows Startup Configuration](#windows-startup-configuration)
- [CLI Arguments](#cli-arguments)
- [Project Structure](#project-structure)
- [`variables.inc` Shape](#variablesinc-shape)
- [Troubleshooting \& FAQ](#troubleshooting--faq)
- [Security Practices](#security-practices)
- [License](#license)

---

## Preview

![SLT Fiber Widget](docs/screenshots/slt_widget.png)

*Live widget: package name, Standard + Total quotas, throttling indicator, updated timestamp.*

*Throttled state: red `THROTTLED - SPEED REDUCED` alert when the Standard quota is exhausted.*

---

## System Architecture

No polling from Rainmeter. Python owns all I/O; Rainmeter is a dumb renderer fed by an atomically-written `variables.inc`.

```mermaid
flowchart LR
    A["MySLT Portal / Omniscapp API<br/><i>BBVAS UsageSummary</i>"] --> B["Background Daemon<br/><i>main.pyw</i><br/>Single-Instance Mutex<br/><i>Global\\SLTMonitorWidgetSingleInstance</i>"]
    B --> C["Session / Cookie Handler<br/><i>Playwright re-login +<br/>cached Bearer in .env</i>"]
    C --> D["UTF-16 LE Atomic Write<br/><i>tmp + os.replace</i>"]
    D --> E["Rainmeter Skin<br/><i>monitor.ini + variables.inc</i>"]
    E --> F["Desktop UI<br/><i>SLT FIBER widget</i>"]
    E -.->|"double-click --refresh<br/>refresh.trigger"| B
    B --> G["!Refresh SLTMonitor<br/>+ 15-min skin fallback"]
    G --> E
```

```text
                    +----------------------------+
                    | MySLT / Omniscapp API      |
                    | BBVAS UsageSummary         |
                    | ?subscriberID=...          |
                    +-------------+--------------+
                                  | GET (Bearer + Incapsula cookie)
                                  v
                    +----------------------------+
                    | main.pyw (pythonw.exe)     |
                    | - Mutex owner              |
                    | - 15-min poll + 1s trigger |
                    | - Playwright re-login      |
                    +-------------+--------------+
                                  | tmp + os.replace (UTF-16 LE BOM)
                                  v
                    +----------------------------+
                    | variables.inc (x2)         |
                    | skin live + repo backup    |
                    +-------------+--------------+
                                  | !Refresh SLTMonitor
                                  v
                    +----------------------------+
                    | monitor.ini -> Desktop UI  |
                    +----------------------------+
```

**Data path in one line:**

`Omniscapp API → main.pyw (requests + Playwright) → data.json (UTF-8, debug) + variables.inc (UTF-16 LE, display) → !Refresh SLTMonitor → Desktop UI`

| Stage | Artifact | Encoding | Notes |
|---|---|---|---|
| Raw fetch | `data.json` | UTF-8 | Parsed `data` dict + `updated_at` (`%I:%M:%S %p`) |
| Display feed | `variables.inc` | UTF-16 LE + BOM | `FetchStatus`, `Status`, `PackageName`, `StdData`, `TotalData`, `Expiry`, `UpdatedAt`, `BonusData`, `ExtraData` |
| Skin | `monitor.ini` | UTF-16 LE | Includes `variables.inc`, footer `Updated: #UpdatedAt#` |
| Refresh | Rainmeter bang | — | `Rainmeter.exe !Refresh SLTMonitor` + 15-min skin fallback |

---

## Operating Model & Daemon Lifecycle

- **Windowless background daemon** — production runs under `pythonw.exe` (no console window). All paths are `BASE_DIR`-anchored to the script folder, so `shell:startup` / headless launches work even when CWD is `System32`.
- **OS-level single-instance mutex** — `main.pyw` acquires `Global\SLTMonitorWidgetSingleInstance` via `CreateMutexW` **before any third-party import**. The handle is held for the process lifetime. This strictly prevents duplicate background instances, racing writers, and file-write collisions. Duplicates (double-clicks, Startup + manual start, editor runs) terminate in milliseconds.
- **Periodic polling: 15 minutes (900 s)** — `REFRESH_SECONDS = 900`. Each cycle hot-reloads `.env`, issues one authenticated `GET` to `https://omniscapp.slt.lk/slt/ext/api/BBVAS/UsageSummary?subscriberID=…`, parses `dataBundle`, and rewrites the display feed. The interval conserves resources and avoids provider rate-limiting.
- **1-second trigger polling** — the sleep between periodic fetches is a `1s × 900` loop (`wait_for_next_cycle()`) that checks `refresh.trigger` every second, so manual refreshes feel instant without shortening the 15-minute API cadence.
- **Fail-safe auto-promotion** — if no daemon is running when a manual refresh is invoked (skin double-click or CLI `--refresh`):
  1. The process finds the mutex free and **acquires it immediately** as owner.
  2. It performs an **initial sync/fetch now** and writes `variables.inc` for instant UI feedback.
  3. It does **NOT exit** — it seamlessly promotes itself into the persistent background daemon loop (1s trigger polling + 15-minute periodic syncs).

> Result: the system is never left without a running daemon after a user-initiated refresh, even if the daemon had previously died.

---

## Fast-Path Manual Refresh & IPC Signaling

Double-clicking the desktop widget runs (see `monitor.ini`):

```powershell
"<repo>\.venv\Scripts\pythonw.exe" "<repo>\main.pyw" --refresh
```

This is a **dual-mode** operation depending on daemon state:

| Path | Condition | Behavior |
|---|---|---|
| **Active daemon** | Mutex already held | Secondary process touches lightweight `refresh.trigger` (timestamp payload) and exits in **~0.1s without importing `requests`/Playwright**. The running daemon wakes on its 1s poll, consumes (deletes) the trigger so each click = 1 poll, runs an immediate API fetch, and bangs `!Refresh SLTMonitor`. |
| **Inactive daemon** | Mutex free | Process acquires the mutex, does a direct initial fetch + `variables.inc` write for instant UI feedback, then **auto-promotes into the daemon loop** instead of terminating. |

Additional hardening:

- A stale trigger left while the daemon was stopped is consumed/dropped on startup so boot does not double-poll (the first loop cycle already fetches).
- A trigger that lands **mid-fetch** is consumed right after the cycle and triggers one more immediate poll instead of sleeping a full interval.
- The guard runs before third-party imports, keeping the signal-and-exit path at millisecond scale.

---

## Authentication & Session Recovery

- **Cached session in `.env` (hot-reloaded every cycle)** — `SLT_SUBSCRIBER_ID`, `SLT_AUTHORIZATION` (Bearer), `SLT_X_IBM_CLIENT_ID`, `SLT_COOKIE` (Incapsula). Pasting a fresh token takes effect on the next cycle with **no restart**.
- **Headless Playwright automation on expiry** — when a fetch looks like auth/session failure (`401`/`403`, non-200 HTML, Incapsula block page, `isSuccess=false`, or missing `dataBundle`), the daemon opens a **headed Chromium** (`headless=False`) at `https://myslt.slt.lk/` and:
  1. Sniffs network traffic for `omniscapp.slt.lk` + `UsageSummary` and captures the fresh `Authorization` header.
  2. Falls back to sweeping `localStorage` / `sessionStorage` for bearer-looking tokens (`bearer` / `4KR…` / `eyJ…`).
  3. Captures cookies via `context.cookies()` and atomically persists both to `.env` (`tmp` + `os.replace`), then retries the API **once**.
- **Manual DevTools fallback** — `F12 → Network → UsageSummary → copy Authorization + Cookie into .env`. Always works, even if Playwright capture fails.
- **Clean error states on the desktop UI** — failures never blank the widget:
  - `FetchStatus=Session Expired` + last-good numbers preserved (still expired after re-login).
  - `FetchStatus=Sync Failed` + last-good numbers preserved (non-JSON / Incapsula block / network blip with `No Response`).
  - `FetchStatus=success` on healthy syncs.
- **Last-good preservation** — `load_last_good()` replays the parsed dict from `data.json` so expiry states keep package numbers on screen with an error banner.

---

## Data Bridge & UI Synchronization

- **Atomic writes (`tmp` + `os.replace`)** — every `variables.inc`, `data.json`, and `.env` update goes through a temp file plus atomic replace, so Rainmeter / JSON readers never observe a torn half-written file, even if the daemon is killed mid-flush.
- **Native UTF-16 LE BOM encoding** — `variables.inc` is written with `encoding="utf-16"` (BOM `FF FE`) to both the live skin (`%USERPROFILE%\Documents\Rainmeter\Skins\SLTMonitor\variables.inc`) and the repo-local backup (`.\variables.inc`). This matches `monitor.ini` and eliminates partial-read errors, mojibake, and UI flicker. `sanitize_inc()` additionally strips CR/LF and guards leading `;` (Rainmeter comment char).
- **Real-time UI updates via Rainmeter bangs** — after each successful write the daemon runs `Rainmeter.exe !Refresh SLTMonitor` (5s timeout, failures swallowed). The skin also self-refreshes every 15 minutes as a fallback if the bang ever fails.
- **12-hour AM/PM timestamping** — `UpdatedAt` uses `%I:%M:%S %p` (e.g. `07:17:31 PM`) on both success and error paths, rendered verbatim in the widget footer.

---

## Key Engineering Highlights

- **Bulletproof background execution** — Windows named mutex (`Global\SLTMonitorWidgetSingleInstance`) acquired before any third-party import, so duplicates die in milliseconds instead of spawning racing writers.
- **Fail-safe auto-promotion** — a manual `--refresh` with no daemon running becomes the daemon (initial sync + persistent loop) instead of a one-shot that leaves the system unmonitored.
- **Instant manual refresh** — `refresh.trigger` IPC + 1s daemon polling gives ~1s double-click-to-UI latency without shortening the 15-minute API interval.
- **Atomic file writes** — every `variables.inc` / `data.json` / `.env` update goes through temp-file + `os.replace`, so readers never see torn files.
- **Strict UTF-16 LE encoding** — Rainmeter-native BOM output, verified against `monitor.ini`.
- **Real-time status alerting** — green status line swaps to red `THROTTLED - SPEED REDUCED` the moment the API reports quota exhaustion.
- **Dual-mode authentication** — automated Playwright Bearer sniffing + persistent gitignored `.env` cache with manual-paste fallback (hot-reloaded).
- **Graceful degradation** — last-good values stay on screen with `Session Expired` / `Sync Failed` banners instead of blanking.

---

## Prerequisites

| Requirement | Notes |
|---|---|
| Windows 10/11 (64-bit) | Mutex, `pythonw.exe`, `shell:startup`, Rainmeter bang |
| [Rainmeter](https://www.rainmeter.net/) (latest) | Skin host |
| Python 3.12+ | See `pyproject.toml` (`requires-python = ">=3.12"`) |
| Package manager | `uv` (recommended) or `pip` + `venv` (repo-local `.venv`) |
| `requests >= 2.34.2` | API polling (`pyproject.toml`) |
| `playwright >= 1.40.0` + Chromium | Auto re-login only (`playwright install chromium`) |
| `python-dotenv` | Optional — **not required**; `main.pyw` ships a zero-dependency `.env` parser |

> `main.pyw` uses only `requests` + `playwright` at runtime. Install both (see below).

---

## Setup & Installation

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

### 3. Install dependencies

Using `uv` (recommended):

```powershell
uv sync
uv run playwright install chromium
```

Or plain `pip` (via `pyproject.toml`):

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -e .
.\.venv\Scripts\playwright.exe install chromium
```

### 4. Link the Rainmeter skin

Copy (or symlink) the skin folder so Rainmeter picks it up at
`Documents\Rainmeter\Skins\SLTMonitor\` containing at minimum:

- `monitor.ini` — widget layout (UTF-16 LE)
- `variables.inc` — live data feed, generated by the daemon (UTF-16 LE)

Then in Rainmeter: **Manage → Skins → SLTMonitor → Load**, or run:

```powershell
& "C:\Program Files\Rainmeter\Rainmeter.exe" "!Refresh" "SLTMonitor"
```

### 5. Start the daemon

```powershell
$root = $PWD.Path
Start-Process -FilePath "$root\.venv\Scripts\pythonw.exe" -ArgumentList "`"$root\main.pyw`"" -WorkingDirectory "$root"
```

Silent, console-free (`pythonw`), first poll runs immediately, then every 15 minutes (plus 1s trigger polling for double-click refreshes).

To watch logs once (visible console), run with `python.exe` instead of `pythonw.exe`:

```powershell
.\.venv\Scripts\python.exe main.pyw
```

### 6. Manual refresh at any time

Double-click the widget, or run:

```powershell
.\.venv\Scripts\pythonw.exe main.pyw --refresh
```

- Daemon running → signals via `refresh.trigger`, exits in ~0.1s, widget updates in ~1s.
- Daemon stopped → initial sync now + auto-promotes into the daemon loop.

---

## Windows Startup Configuration

Run automatically on Windows logon via `shell:startup`:

1. Press `Win + R`, type `shell:startup`, press Enter.
2. Create a shortcut (a ready-made `SLTMonitor.lnk` ships in the repo root — copy it):

```powershell
Copy-Item ".\SLTMonitor.lnk" ([Environment]::GetFolderPath("Startup"))
```

Or craft it manually — new shortcut with (replace `<repo>` with your clone location):

- **Target:** `<repo>\.venv\Scripts\pythonw.exe "<repo>\main.pyw"`
- **Start in:** `<repo>`

The single-instance mutex makes double launches harmless. `BASE_DIR`-anchored paths keep Startup launches (CWD = `System32`) working.

---

## CLI Arguments

| Flag | Mode | Behavior |
|---|---|---|
| *(none)* | Daemon | Acquire mutex (or signal + exit if held), clear stale trigger, enter loop: immediate fetch, then 1s trigger poll + 15-min periodic fetch. |
| `--refresh` | Manual sync (primary) | Daemon running → write `refresh.trigger`, exit ~0.1s. No daemon → initial sync + auto-promote to daemon. Bound to widget double-click. |
| `--sync-now` | Manual sync (alias) | Identical to `--refresh`. |
| `--once` | Manual sync (alias) | Identical to `--refresh` (legacy name; still auto-promotes, does **not** one-shot exit). |
| `-refresh`, `/refresh` | Manual sync (aliases) | Identical to `--refresh` (Rainmeter/CMD style variants). |

```powershell
# Daemon (normal production)
.\.venv\Scripts\pythonw.exe main.pyw

# Manual refresh (all equivalent)
.\.venv\Scripts\pythonw.exe main.pyw --refresh
.\.venv\Scripts\pythonw.exe main.pyw --sync-now
.\.venv\Scripts\pythonw.exe main.pyw --once
```

> All refresh aliases are matched case-insensitively against `sys.argv`. The mutex guard + trigger signaling run **before** third-party imports so the active-daemon path stays at ~0.1s.

---

## Project Structure

```text
D:\Projects\Myslt_meter\
├── main.pyw          # Background daemon (mutex, polling, Playwright re-login, atomic writes)
├── .env              # Secrets cache (gitignored, hot-reloaded)
├── .env.example      # Template for fresh setups
├── variables.inc     # Local backup of the live Rainmeter feed (UTF-16 LE)
├── data.json         # Last successful API payload (debug/inspection)
├── refresh.trigger   # Manual-refresh IPC signal (transient, gitignored)
├── SLTMonitor.lnk    # Ready-made Startup shortcut (pythonw + main.pyw)
├── pyproject.toml    # Dependencies (requests, playwright)
├── docs/
│   └── screenshots/
│       ├── slt_widget.png           # Widget preview (normal state)
│       └── slt_widget_throttled.png # Widget preview (throttled state)
└── README.md         # This file
```

Skin (outside repo, written by daemon):

```text
%USERPROFILE%\Documents\Rainmeter\Skins\SLTMonitor\
├── monitor.ini       # Widget layout, double-click → main.pyw --refresh, UTF-16 LE
└── variables.inc     # Live feed (written by daemon, UTF-16 LE + BOM)
```

Gitignored local state (never commit): `.env`, `variables.inc`, `data.json`, `refresh.trigger`, `SLTMonitor.lnk`, `__pycache__/`, `.venv/`.

---

## `variables.inc` Shape

```ini
[Variables]
FetchStatus=success
Status=THROTTLED
PackageName=WEB FAMILY PLUS
StdData=0.0 GB / 40.0 GB
StdRemaining=0.0
StdLimit=40.0
TotalData=44.5 GB / 100.0 GB
TotalRemaining=44.5
TotalLimit=100.0
Unit=GB
Expiry=30-Sep
ReportedTime=28-Sep-2026 07:17 PM
BonusData=0 / 2.6 GB
ExtraData=0 / 2.0 GB
UpdatedAt=07:17:31 PM
```

`FetchStatus` drives the skin banner: `success` (live), `Session Expired` (re-login needed, last-good kept), `Sync Failed` (transient, last-good kept).

---

## Troubleshooting & FAQ

**Session expired / widget shows `Session Expired`?**
The SLT Bearer token and Incapsula cookies rotate regularly — this is expected. The daemon detects
`401/403` (or Incapsula HTML / `isSuccess=false`) and opens a Chromium window: log in to MySLT once and it auto-captures the
fresh `Authorization` + cookies into `.env` and retries. Prefer the manual route? Open DevTools
(**F12 → Network → `UsageSummary`**), copy the `Authorization` and `Cookie` headers into `.env`, save —
the next cycle (≤15 min, or double-click for instant) picks them up with no restart.

**Widget shows `Sync Failed`?**
Transient path (`No Response` / non-JSON / Incapsula block). Last-good numbers are preserved. Check network/DNS, then double-click to retry immediately. If persistent, refresh the session as above and inspect `data.json` for the last successful payload.

**How do I verify the daemon is running?**

```powershell
Get-Process pythonw | Where-Object { $_.Path -like "*Myslt_meter*" } | Select-Object Id, Path
```

One row = healthy. The mutex guarantees there can only ever be one writer. A second `main.pyw --refresh` with a live daemon prints `Daemon already running: refresh requested` and exits in ~0.1s; with no daemon it prints `auto-promoting to daemon` and stays alive.

**Killing a stale daemon (after crashes or venv switches)?**

```powershell
# Targeted — kill only Myslt_meter processes
Get-CimInstance Win32_Process -Filter "Name='pythonw.exe' OR Name='python.exe'" |
  Where-Object { $_.CommandLine -like '*Myslt_meter*' } |
  ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
```

The `Global\` mutex is kernel-bound to the owning process — killing the owner releases it. No reboot needed; just relaunch.

**Widget shows stale data or blanks?**
Check `Documents\Rainmeter\Skins\SLTMonitor\variables.inc` — if `FetchStatus=Session Expired` / `Sync Failed` with
last-good numbers preserved, the API call is failing (network down or session dead). Check
`data.json` in the repo root for the last successful payload, then refresh the session as above.

**Garbled characters in the skin?**
Both `monitor.ini` and `variables.inc` must be **UTF-16 LE with BOM**. Never save them as UTF-8 from a
text editor — let `main.pyw` regenerate `variables.inc`, and if you hand-edit `monitor.ini`, re-save it
as UTF-16 LE. Verify:

```powershell
python -c "with open(r'$env:USERPROFILE\Documents\Rainmeter\Skins\SLTMonitor\variables.inc','rb') as f: print(list(f.read(2)))"
# Expect [255, 254]
```

**Rainmeter not picking up changes?**

```powershell
& "C:\Program Files\Rainmeter\Rainmeter.exe" "!Refresh" "SLTMonitor"
```

The skin also self-refreshes every 15 minutes as a fallback.

**Playwright / Chromium issues?**

```powershell
.\.venv\Scripts\playwright.exe install chromium
```

If the login window never appears, run once with `python.exe` (not `pythonw.exe`) to see console output.

---

## Security Practices

- Secrets live only in gitignored `.env` (`SLT_AUTHORIZATION`, `SLT_COOKIE`) — never in git, never in the skin.
- `.env.example` ships only placeholder tokens; real tokens are pasted locally or auto-captured by Playwright.
- `cookies` / Bearer tokens are written atomically and hot-reloaded; no token is ever logged.
- `data.json` / `variables.inc` contain only quota aggregates (no credentials) but remain gitignored as local state.
- Playwright uses a visible headed browser so the user completes MySLT login/OTP directly on the first-party site — the daemon only sniffs the resulting session headers, never keystrokes.

---

## License

MIT — do what you want, no warranty. MySLT portal/API belongs to SLT; this is an unofficial personal-use monitor.

*Standalone companion to the DialogMonitor project — same hardened daemon pattern, dedicated `SLTMonitor` Rainmeter skin.*
