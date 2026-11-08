import json
import logging
from logging.handlers import RotatingFileHandler
import os
import pathlib
import subprocess
import sys
import time
from datetime import datetime

# ------------------------------------------------------------------------------
# Paths & Single-Instance Initialization (Pre-import Guard)
# ------------------------------------------------------------------------------
BASE_DIR = pathlib.Path(__file__).resolve().parent
TRIGGER_FILE = BASE_DIR / "refresh.trigger"
ENV_FILE = BASE_DIR / ".env"
DATA_FILE = BASE_DIR / "data.json"
LAPTOP_USAGE_FILE = BASE_DIR / "laptop_usage.json"
LOG_FILE = BASE_DIR / "daemon.log"
SKIN_INC = (
    pathlib.Path(os.environ["USERPROFILE"])
    / "Documents"
    / "Rainmeter"
    / "Skins"
    / "SLTMonitor"
    / "variables.inc"
)

RAINMETER_EXE = r"C:\Program Files\Rainmeter\Rainmeter.exe"
LOGIN_URL = "https://myslt.slt.lk/"
API_BASE = "https://omniscapp.slt.lk/slt/ext/api/BBVAS/UsageSummary"
REFRESH_SECONDS = 1800  # 30 minutes SLT Portal polling
LAPTOP_TICK_SECONDS = 60  # 60 seconds local laptop data flush
AUTH_BACKOFF_SECONDS = 300  # 5 minutes failure cooldown for headless login

REFRESH_FLAGS = ("--refresh", "--sync-now", "--once", "-refresh", "/refresh")
WANTS_REFRESH = any((a or "").strip().lower() in REFRESH_FLAGS for a in sys.argv[1:])
_IS_OWNER = False
_last_auth_attempt_time = 0

# ------------------------------------------------------------------------------
# Diagnostic Logging Setup (Rotating File + Console / pythonw Stream Redirection)
# ------------------------------------------------------------------------------
logger = logging.getLogger("SLTMonitor")
logger.setLevel(logging.INFO)

if not logger.handlers:
    _rf = RotatingFileHandler(
        LOG_FILE, maxBytes=2 * 1024 * 1024, backupCount=1, encoding="utf-8"
    )
    _fmt = logging.Formatter(
        "[%(asctime)s] [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
    )
    _rf.setFormatter(_fmt)
    logger.addHandler(_rf)

    if sys.stdout is not None:
        _ch = logging.StreamHandler(sys.stdout)
        _ch.setFormatter(_fmt)
        logger.addHandler(_ch)


class _LoggerStream:
    """Redirect standard stream writes into the rotating logger."""

    def __init__(self, log_func):
        self._log_func = log_func
        self._buf = ""

    def write(self, s):
        if not s:
            return
        self._buf += s
        while "\n" in self._buf:
            line, self._buf = self._buf.split("\n", 1)
            line = line.strip()
            if line:
                self._log_func(line)

    def flush(self):
        if self._buf.strip():
            self._log_func(self._buf.strip())
            self._buf = ""


if sys.stdout is None:
    sys.stdout = _LoggerStream(logger.info)
if sys.stderr is None:
    sys.stderr = _LoggerStream(logger.error)


def _unhandled_exception(exc_type, exc_value, exc_traceback):
    if issubclass(exc_type, KeyboardInterrupt):
        sys.__excepthook__(exc_type, exc_value, exc_traceback)
        return
    logger.critical("Uncaught exception", exc_info=(exc_type, exc_value, exc_traceback))


sys.excepthook = _unhandled_exception


def request_refresh():
    """Signal the running daemon to poll immediately via IPC trigger file."""
    try:
        with open(TRIGGER_FILE, "w", encoding="utf-8") as f:
            f.write(str(time.time()))
        return True
    except Exception as e:
        logger.error(f"Could not write refresh trigger: {e}")
        return False


def consume_refresh_request():
    """Return True once per trigger file; deletes it so each signal = 1 poll."""
    try:
        if TRIGGER_FILE.exists():
            TRIGGER_FILE.unlink()
            return True
    except Exception:
        pass
    return False


def _ensure_single_instance():
    """OS-level single-instance mutex with auto-promotion."""
    global _IS_OWNER
    if os.name != "nt":
        _IS_OWNER = True
        return
    try:
        import ctypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        handle = kernel32.CreateMutexW(
            None, False, "Global\\SLTMonitorWidgetSingleInstance"
        )
        _ensure_single_instance._handle = handle
        already_running = (not handle) or (kernel32.GetLastError() == 183)
        if already_running:
            if request_refresh():
                print("[*] Daemon already running: refresh requested via trigger.")
            else:
                print("[!] Another instance is running. Exiting.")
            try:
                sys.stdout.flush()
            except Exception:
                pass
            os._exit(0)
        _IS_OWNER = True
        if WANTS_REFRESH:
            logger.info("Auto-promoting to daemon loop.")
    except SystemExit:
        raise
    except BaseException:
        _IS_OWNER = True


if __name__ == "__main__":
    _ensure_single_instance()

# ------------------------------------------------------------------------------
# Heavy Third-Party Imports (Loaded ONLY by Mutex Owner)
# ------------------------------------------------------------------------------
import psutil
import requests


# ------------------------------------------------------------------------------
# Configuration Management (.env)
# ------------------------------------------------------------------------------
def load_config():
    """Hot-reloads .env every cycle so manual token updates take effect immediately."""
    cfg = {
        "SLT_SUBSCRIBER_ID": "",
        "SLT_AUTHORIZATION": "",
        "SLT_X_IBM_CLIENT_ID": "b7402e9d66808f762ccedbe42c20668e",
        "SLT_COOKIE": "",
        "SLT_USERNAME": "",
        "SLT_PASSWORD": "",
    }
    try:
        if ENV_FILE.exists():
            with open(ENV_FILE, "r", encoding="utf-8") as f:
                for raw in f:
                    line = raw.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    k, v = line.split("=", 1)
                    k = k.strip()
                    if k in cfg:
                        cfg[k] = v.strip().strip('"').strip("'")
    except Exception as e:
        logger.error(f"Could not read .env: {e}")
    return cfg


def save_auth_to_env(authorization=None, cookie=None):
    """Atomically update SLT_AUTHORIZATION / SLT_COOKIE in .env, preserving keys."""
    try:
        lines = []
        if ENV_FILE.exists():
            with open(ENV_FILE, "r", encoding="utf-8") as f:
                lines = f.readlines()
        seen_auth = seen_cookie = False
        out = []
        for raw in lines:
            stripped = raw.strip()
            if stripped.startswith("SLT_AUTHORIZATION=") and authorization:
                out.append(f"SLT_AUTHORIZATION={authorization}\n")
                seen_auth = True
            elif stripped.startswith("SLT_COOKIE=") and cookie is not None:
                out.append(f"SLT_COOKIE={cookie}\n")
                seen_cookie = True
            else:
                out.append(raw)
        if authorization and not seen_auth:
            out.append(f"SLT_AUTHORIZATION={authorization}\n")
        if cookie is not None and not seen_cookie:
            out.append(f"SLT_COOKIE={cookie}\n")
        tmp = str(ENV_FILE) + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.writelines(out)
        os.replace(tmp, str(ENV_FILE))
        logger.info(".env updated with fresh session.")
    except Exception as e:
        logger.error(f"Could not update .env: {e}")


# ------------------------------------------------------------------------------
# Local Laptop Usage Telemetry (psutil)
# ------------------------------------------------------------------------------
def load_laptop_usage_state():
    """Load monthly accumulated bytes and baseline from laptop_usage.json."""
    current_ym = datetime.now().strftime("%Y-%m")
    default_state = {
        "year_month": current_ym,
        "accumulated_bytes": 0,
        "last_counter_bytes": None,
    }
    try:
        if LAPTOP_USAGE_FILE.exists():
            with open(LAPTOP_USAGE_FILE, "r", encoding="utf-8") as f:
                state = json.load(f)
                if isinstance(state, dict):
                    # Auto-reset on the 1st of every month
                    if state.get("year_month") != current_ym:
                        state["year_month"] = current_ym
                        state["accumulated_bytes"] = 0
                        state["last_counter_bytes"] = None
                    return state
    except Exception as e:
        logger.error(f"Error reading {LAPTOP_USAGE_FILE.name}: {e}")
    return default_state


def save_laptop_usage_state(state):
    """Atomically persist laptop usage state to laptop_usage.json."""
    try:
        tmp = str(LAPTOP_USAGE_FILE) + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2)
        os.replace(tmp, str(LAPTOP_USAGE_FILE))
    except Exception as e:
        logger.error(f"Error saving {LAPTOP_USAGE_FILE.name}: {e}")


def update_laptop_usage():
    """Track total laptop byte consumption, persist delta, and return formatted GB string."""
    try:
        counters = psutil.net_io_counters()
        current_counter = counters.bytes_sent + counters.bytes_recv
    except Exception as e:
        logger.error(f"psutil net_io_counters error: {e}")
        return "0.0 GB"

    state = load_laptop_usage_state()
    last_counter = state.get("last_counter_bytes")

    if last_counter is None:
        delta = 0
    elif current_counter >= last_counter:
        delta = current_counter - last_counter
    else:
        # System reboot or counter wrapped around
        delta = current_counter

    state["accumulated_bytes"] = state.get("accumulated_bytes", 0) + delta
    state["last_counter_bytes"] = current_counter
    save_laptop_usage_state(state)

    gb_val = state["accumulated_bytes"] / (1024 ** 3)
    return f"{gb_val:.1f} GB"


# ------------------------------------------------------------------------------
# Data Parsing & Ingestion (Unlimited Home Schema)
# ------------------------------------------------------------------------------
def sanitize_inc(value):
    """Make a value safe for Rainmeter .inc: no CR/LF, guarded leading semicolon."""
    if value is None:
        return ""
    s = str(value).replace("\r", " ").replace("\n", " ").strip()
    if s.startswith(";"):
        s = " " + s
    return s


def parse_usage(payload):
    """Parse verified SLT Unlimited Home response schema into a normalized dict."""
    db = payload.get("dataBundle") if isinstance(payload, dict) else {}
    if not isinstance(db, dict):
        db = {}

    info = db.get("my_package_info") or {}
    pkg_name = str(info.get("package_name") or "UNLIMITED HOME").strip()
    status = str(db.get("status") or "NORMAL").strip()

    # SLT Total Used
    pkg_summary = db.get("my_package_summary") or {}
    used = pkg_summary.get("used")
    unit = str(pkg_summary.get("volume_unit") or "GB").strip() or "GB"

    if used is not None and str(used).strip() and str(used).strip().lower() != "none":
        slt_total_used = f"{str(used).strip()} {unit}"
    else:
        # Fallback for quota packages or legacy usageDetails
        details = info.get("usageDetails") or []
        total_entry = None
        for d in details:
            if isinstance(d, dict) and "total" in (d.get("name") or "").lower():
                total_entry = d
                break
        if total_entry:
            u_used = total_entry.get("used")
            u_rem = total_entry.get("remaining")
            u_lim = total_entry.get("limit")
            u_unit = total_entry.get("volume_unit") or unit
            if u_used is not None and str(u_used).strip():
                slt_total_used = f"{str(u_used).strip()} {u_unit}"
            elif u_rem is not None and u_lim is not None:
                slt_total_used = f"{u_rem} {u_unit} / {u_lim} {u_unit}"
            else:
                slt_total_used = f"0.0 {unit}"
        else:
            slt_total_used = f"0.0 {unit}"

    return {
        "package_name": pkg_name,
        "slt_total_used": slt_total_used,
        "status": status,
        "unit": unit,
        "reported_time": str(db.get("reported_time") or "").strip(),
    }


def load_last_good():
    """Return last-good parsed dict from data.json so offline states retain values."""
    try:
        if DATA_FILE.exists():
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                body = json.load(f)
                if isinstance(body, dict) and isinstance(body.get("data"), dict):
                    d = body["data"]
                    if d.get("slt_total_used") or d.get("package_name"):
                        d["_cached_updated_at"] = (
                            body.get("updated_at")
                            or d.get("reported_time")
                            or ""
                        )
                        return d
    except Exception:
        pass
    return {
        "package_name": "UNLIMITED HOME",
        "slt_total_used": "0.0 GB",
        "status": "UNKNOWN",
        "unit": "GB",
        "_cached_updated_at": "",
    }


def write_variables_inc(
    data,
    laptop_used,
    fetch_status,
    slt_updated_at=None,
    local_updated_at=None,
):
    """Write variables.inc atomically (UTF-16 LE with BOM) ONLY to the live skin directory."""
    if not slt_updated_at:
        slt_updated_at = (
            data.get("reported_time")
            or data.get("_cached_updated_at")
            or "Unknown"
        )
    if not local_updated_at:
        local_updated_at = time.strftime("%I:%M:%S %p")

    status = data.get("status", "NORMAL")
    slt_used = data.get("slt_total_used", "0.0 GB")

    lines = [
        "[Variables]",
        f"PackageName={sanitize_inc(data.get('package_name', 'UNLIMITED HOME'))}",
        f"SLTTotalUsed={sanitize_inc(slt_used)}",
        f"LaptopUsed={sanitize_inc(laptop_used)}",
        f"Status={sanitize_inc(status)}",
        f"Unit={sanitize_inc(data.get('unit', 'GB'))}",
        f"SLTUpdatedAt={sanitize_inc(slt_updated_at)}",
        f"UpdatedAt={sanitize_inc(slt_updated_at)}",
        f"LocalUpdatedAt={sanitize_inc(local_updated_at)}",
        f"FetchStatus={sanitize_inc(fetch_status)}",
    ]

    content = "\n".join(lines) + "\n"

    try:
        SKIN_INC.parent.mkdir(parents=True, exist_ok=True)
        tmp = str(SKIN_INC) + ".tmp"
        with open(tmp, "w", encoding="utf-16") as f:
            f.write(content)
        os.replace(tmp, str(SKIN_INC))
    except Exception as e:
        logger.error(f"Could not write {SKIN_INC}: {e}")

    # Nudge Rainmeter to reload variables immediately
    try:
        if os.path.exists(RAINMETER_EXE):
            subprocess.run(
                [RAINMETER_EXE, "!Refresh", "SLTMonitor"],
                timeout=5,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
    except Exception:
        pass


# ------------------------------------------------------------------------------
# Silent Headless Browser Automation (Playwright + Stealth) with Failure Backoff
# ------------------------------------------------------------------------------
def can_attempt_auth(force=False):
    """Check if headless authentication is allowed by the cooldown policy."""
    global _last_auth_attempt_time
    if force:
        return True
    return (time.time() - _last_auth_attempt_time) >= AUTH_BACKOFF_SECONDS


def get_fresh_auth_headless(force=False):
    """Silent headless Playwright re-authentication with stealth evasions and failure backoff."""
    global _last_auth_attempt_time

    if not can_attempt_auth(force=force):
        remaining = int(AUTH_BACKOFF_SECONDS - (time.time() - _last_auth_attempt_time))
        logger.warning(
            f"Headless auth cooldown active ({remaining}s remaining). Skipping browser launch."
        )
        return None, None

    _last_auth_attempt_time = time.time()

    cfg = load_config()
    username = cfg.get("SLT_USERNAME") or ""
    password = cfg.get("SLT_PASSWORD") or ""
    if not username or not password:
        logger.warning(
            "SLT_USERNAME or SLT_PASSWORD not configured in .env; skipping headless login."
        )
        return None, None

    logger.info("Launching silent headless browser for session refresh...")
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        logger.error("playwright is not installed. Run: pip install playwright")
        return None, None

    captured = {}
    cookie_str = ""
    p = None
    browser = None
    context = None

    try:
        p = sync_playwright().start()
        browser = p.chromium.launch(
            headless=True,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-infobars",
                "--window-position=0,0",
                "--ignore-certificate-errors",
            ],
            timeout=30000,
        )
        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/130.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 720},
        )
        context.set_default_timeout(25000)
        page = context.new_page()

        # Apply stealth evasions
        try:
            from playwright_stealth import Stealth

            stealth = Stealth()
            stealth.apply_stealth_sync(page)
        except Exception as e:
            logger.warning(f"Stealth hook warning: {e}")

        def on_request(req):
            try:
                url = req.url or ""
                if "omniscapp.slt.lk" in url and "UsageSummary" in url:
                    hdrs = req.headers or {}
                    auth = hdrs.get("authorization") or hdrs.get("Authorization")
                    if auth and "authorization" not in captured:
                        captured["authorization"] = auth
                        logger.info("Captured fresh Authorization header from API request.")
            except Exception:
                pass

        page.on("request", on_request)

        try:
            page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=30000)
        except Exception as e:
            logger.warning(f"Page navigation warning: {e}")

        # Fill credentials
        try:
            page.wait_for_timeout(2000)
            user_input = page.locator(
                "input[type='text'], input[type='email'], input[name*='user'], input[id*='user'], input[name*='username']"
            ).first
            pass_input = page.locator("input[type='password']").first
            if user_input.is_visible(timeout=5000) and pass_input.is_visible(timeout=5000):
                user_input.fill(username)
                pass_input.fill(password)
                submit_btn = page.locator(
                    "button[type='submit'], input[type='submit'], button:has-text('Login'), button:has-text('Sign In')"
                ).first
                if submit_btn.is_visible(timeout=5000):
                    submit_btn.click()
                    logger.info("Credentials submitted on login form.")
        except Exception as e:
            logger.warning(f"Auto-fill note: {e}")

        # Wait for UsageSummary request (max 15s)
        for _ in range(15):
            page.wait_for_timeout(1000)
            if captured.get("authorization"):
                break

        # Fallback: sweep localStorage & sessionStorage for Bearer tokens
        if "authorization" not in captured:
            for store_name in ("localStorage", "sessionStorage"):
                try:
                    dump = (
                        page.evaluate(
                            f"() => {{ const o={{}}; try {{ for (let i=0;i<{store_name}.length;i++)"
                            f"{{ const k={store_name}.key(i); o[k]={store_name}.getItem(k); }} }} catch(e) {{}} return o; }}"
                        )
                        or {}
                    )
                    for k, v in dump.items():
                        if isinstance(v, str) and len(v) > 60 and (
                            "bearer" in v.lower()
                            or v.startswith("4KR")
                            or "eyJ" in v[:3]
                        ):
                            captured["authorization"] = (
                                v if v.lower().startswith("bearer") else f"bearer {v}"
                            )
                            logger.info(f"Recovered bearer token from {store_name}.")
                            break
                    if "authorization" in captured:
                        break
                except Exception as e:
                    logger.warning(f"Storage sweep note ({store_name}): {e}")

        try:
            cookies = context.cookies()
            cookie_str = "; ".join(
                f"{c['name']}={c['value']}" for c in cookies if c.get("value")
            )
        except Exception as e:
            logger.warning(f"Cookie collection note: {e}")

    except Exception as e:
        logger.error(f"Headless automation error: {e}")
    finally:
        if context:
            try:
                context.close()
            except Exception:
                pass
        if browser:
            try:
                browser.close()
            except Exception:
                pass
        if p:
            try:
                p.stop()
            except Exception:
                pass

    auth = captured.get("authorization")
    if auth or cookie_str:
        save_auth_to_env(auth, cookie_str if cookie_str else None)
    return auth, cookie_str


# ------------------------------------------------------------------------------
# API Polling Cycle
# ------------------------------------------------------------------------------
def fetch_data(cfg):
    subscriber = (cfg.get("SLT_SUBSCRIBER_ID") or "").strip()
    if not subscriber or "X" in subscriber:
        logger.error("SLT_SUBSCRIBER_ID missing or unconfigured in .env")
        return None
    url = f"{API_BASE}?subscriberID={subscriber}"
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/130.0.0.0 Safari/537.36"
        ),
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.9",
        "X-IBM-Client-Id": cfg.get("SLT_X_IBM_CLIENT_ID") or "b7402e9d66808f762ccedbe42c20668e",
        "Origin": "https://myslt.slt.lk",
        "Referer": "https://myslt.slt.lk/",
        "Sec-Fetch-Dest": "empty",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Site": "same-site",
    }
    if cfg.get("SLT_AUTHORIZATION"):
        headers["Authorization"] = cfg["SLT_AUTHORIZATION"]
    if cfg.get("SLT_COOKIE"):
        headers["Cookie"] = cfg["SLT_COOKIE"]

    try:
        return requests.get(url, headers=headers, timeout=20)
    except Exception as e:
        logger.error(f"Request exception: {e}")
        return None


def is_session_expired_response(res):
    """Determine if response indicates an expired or challenged session."""
    if res is None:
        return False
    if res.status_code in (401, 403):
        return True
    ctype = (res.headers.get("Content-Type") or "").lower()
    if "html" in ctype and res.status_code != 200:
        return True
    if "html" in ctype and "incapsula" in res.text[:2000].lower():
        return True
    try:
        body = res.json()
    except Exception:
        return res.status_code != 200
    if isinstance(body, dict):
        if body.get("isSuccess") is False and not body.get("dataBundle"):
            return True
        if not body.get("dataBundle"):
            return True
    return False


def update_data_cycle(laptop_used, force=False):
    """Execute one SLT portal polling cycle, write live skin feed, and update debug cache.
    Returns (data_dict, fetch_status, slt_updated_at)."""
    cfg = load_config()
    res = fetch_data(cfg)

    if is_session_expired_response(res):
        logger.warning(
            f"Portal response indicated session expired/blocked (HTTP {res.status_code if res else 'None'}). Triggering re-authentication..."
        )
        new_auth, _ = get_fresh_auth_headless(force=force)
        if new_auth:
            cfg = load_config()
            res = fetch_data(cfg)

    now_local = time.strftime("%I:%M:%S %p")
    if res is not None and res.status_code == 200:
        try:
            payload = res.json()
        except Exception:
            payload = None

        if isinstance(payload, dict) and payload.get("dataBundle"):
            data = parse_usage(payload)
            slt_sync_time = now_local

            # Atomic debug JSON cache
            try:
                tmp = str(DATA_FILE) + ".tmp"
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump(
                        {"status": "success", "updated_at": slt_sync_time, "data": data},
                        f,
                        indent=2,
                    )
                os.replace(tmp, str(DATA_FILE))
            except Exception as e:
                logger.error(f"Could not write {DATA_FILE.name}: {e}")

            write_variables_inc(
                data=data,
                laptop_used=laptop_used,
                fetch_status="success",
                slt_updated_at=slt_sync_time,
                local_updated_at=now_local,
            )
            logger.info(
                f"Synced: {data.get('package_name')} | "
                f"SLT Used: {data.get('slt_total_used')} | "
                f"Laptop: {laptop_used} | "
                f"Status: {data.get('status')}"
            )
            return data, "success", slt_sync_time

    # --- Truthful Error Reporting (Zero Fake Success) ---
    if res is not None and res.status_code in (401, 403):
        error_status = "AUTH_ERROR"
        fetch_status = "failed"
    elif res is not None and res.status_code != 200:
        error_status = "SYNC_FAILED"
        fetch_status = "failed"
    else:
        error_status = "SYNC_FAILED"
        fetch_status = "failed"

    last_good = load_last_good()
    cached_slt_time = (
        last_good.get("reported_time")
        or last_good.get("_cached_updated_at")
        or "Unknown"
    )

    # Truthful reporting: state reflects error, but keeps last known data for display
    error_data = dict(last_good)
    error_data["status"] = error_status

    write_variables_inc(
        data=error_data,
        laptop_used=laptop_used,
        fetch_status=fetch_status,
        slt_updated_at=cached_slt_time,
        local_updated_at=now_local,
    )
    logger.error(
        f"Portal sync failed (HTTP {res.status_code if res else 'None'}). "
        f"Emitted truthful state: Status={error_status}, FetchStatus={fetch_status}, LastPortalSync={cached_slt_time}"
    )
    return error_data, fetch_status, cached_slt_time


# ------------------------------------------------------------------------------
# Main Daemon Lifecycle
# ------------------------------------------------------------------------------
if __name__ == "__main__":
    consume_refresh_request()

    # Initial sync on daemon startup
    laptop_used = update_laptop_usage()
    current_data, current_fetch_status, current_slt_time = update_data_cycle(
        laptop_used, force=WANTS_REFRESH
    )

    last_laptop_time = time.time()
    last_portal_time = time.time()

    logger.info(
        f"Daemon running. SLT portal poll: {REFRESH_SECONDS}s, "
        f"Laptop usage tick: {LAPTOP_TICK_SECONDS}s, "
        f"IPC trigger: active."
    )

    while True:
        time.sleep(1)
        now = time.time()

        # Check for fast-path IPC trigger (e.g., skin double-click)
        if consume_refresh_request():
            logger.info("Manual refresh triggered via IPC.")
            laptop_used = update_laptop_usage()
            current_data, current_fetch_status, current_slt_time = update_data_cycle(
                laptop_used, force=True
            )
            last_laptop_time = now
            last_portal_time = now
            continue

        # Local laptop usage tick (60 seconds)
        if now - last_laptop_time >= LAPTOP_TICK_SECONDS:
            laptop_used = update_laptop_usage()
            last_laptop_time = now
            now_str = time.strftime("%I:%M:%S %p")
            write_variables_inc(
                data=current_data,
                laptop_used=laptop_used,
                fetch_status=current_fetch_status,
                slt_updated_at=current_slt_time,
                local_updated_at=now_str,
            )

        # SLT Portal polling tick (1800 seconds / 30 mins)
        if now - last_portal_time >= REFRESH_SECONDS:
            laptop_used = update_laptop_usage()
            current_data, current_fetch_status, current_slt_time = update_data_cycle(
                laptop_used, force=False
            )
            last_laptop_time = now
            last_portal_time = now
