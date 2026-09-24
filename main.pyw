import json
import os
import sys
import time


def _ensure_single_instance():
    """Exit immediately if another copy is already running.

    Kills duplicate-writer races (double-clicks, editor F5, Startup +
    manual launch). Must run before any third-party imports so duplicates
    die in milliseconds.
    """
    if os.name != "nt":
        return
    try:
        import ctypes
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        handle = kernel32.CreateMutexW(
            None, False, "Global\\SLTMonitorWidgetSingleInstance")
        # Keep a reference alive for the process lifetime.
        _ensure_single_instance._handle = handle
        if not handle or kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
            print("[!] Another SLTMonitor instance is already running. Exiting.")
            os._exit(0)
    except Exception:
        pass  # Never block startup on the guard itself.


_ensure_single_instance()

import requests
import subprocess

# Anchor all relative files to script folder so Startup/headless runs work
# even when CWD is System32 or elsewhere.
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ENV_FILE = os.path.join(BASE_DIR, ".env")
DATA_FILE = os.path.join(BASE_DIR, "data.json")
LOCAL_INC = os.path.join(BASE_DIR, "variables.inc")
# Direct write into the skin folder (user approved). Keep local copy too.
SKIN_INC = os.path.join(
    os.path.expanduser("~"),
    "Documents", "Rainmeter", "Skins", "SLTMonitor", "variables.inc",
)
RAINMETER_EXE = r"C:\Program Files\Rainmeter\Rainmeter.exe"
LOGIN_URL = "https://myslt.slt.lk/"
API_BASE = "https://omniscapp.slt.lk/slt/ext/api/BBVAS/UsageSummary"
REFRESH_SECONDS = 900  # 15 min (user choice)


def load_config():
    """Tiny .env parser (no external dep). Hot-reloaded every cycle so a
    manual token paste takes effect without restarting the daemon."""
    cfg = {
        "SLT_SUBSCRIBER_ID": "",
        "SLT_AUTHORIZATION": "",
        "SLT_X_IBM_CLIENT_ID": "b7402e9d66808f762ccedbe42c20668e",
        "SLT_COOKIE": "",
    }
    try:
        if os.path.exists(ENV_FILE):
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
        print(f"[!] Could not read .env: {e}")
    return cfg


def save_auth_to_env(authorization=None, cookie=None):
    """Update SLT_AUTHORIZATION / SLT_COOKIE in .env, preserving other keys."""
    try:
        lines = []
        if os.path.exists(ENV_FILE):
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
        tmp = ENV_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.writelines(out)
        os.replace(tmp, ENV_FILE)
        print("[+] .env updated with fresh session.")
    except Exception as e:
        print(f"[!] Could not update .env: {e}")


def sanitize_inc(value):
    """Make a value safe for a Rainmeter .inc line: no newlines, no CR."""
    if value is None:
        return ""
    s = str(value).replace("\r", " ").replace("\n", " ").strip()
    # Leading semicolon would be parsed as a comment in .inc, so guard it.
    if s.startswith(";"):
        s = " " + s
    return s


def write_variables_inc(data, fetch_status, updated_at):
    """Write Rainmeter-friendly variables.inc (no JSON/RegEx needed)."""
    lines = ["[Variables]"]
    lines.append(f"FetchStatus={sanitize_inc(fetch_status)}")
    lines.append(f"Status={sanitize_inc(data.get('status', ''))}")
    lines.append(f"PackageName={sanitize_inc(data.get('package_name', ''))}")
    lines.append(f"StdData={sanitize_inc(data.get('std_data', ''))}")
    lines.append(f"StdRemaining={sanitize_inc(data.get('std_remaining', ''))}")
    lines.append(f"StdLimit={sanitize_inc(data.get('std_limit', ''))}")
    lines.append(f"TotalData={sanitize_inc(data.get('total_data', ''))}")
    lines.append(f"TotalRemaining={sanitize_inc(data.get('total_remaining', ''))}")
    lines.append(f"TotalLimit={sanitize_inc(data.get('total_limit', ''))}")
    lines.append(f"Unit={sanitize_inc(data.get('unit', 'GB'))}")
    lines.append(f"Expiry={sanitize_inc(data.get('expiry', ''))}")
    lines.append(f"ReportedTime={sanitize_inc(data.get('reported_time', ''))}")
    lines.append(f"BonusData={sanitize_inc(data.get('bonus_data', ''))}")
    lines.append(f"ExtraData={sanitize_inc(data.get('extra_data', ''))}")
    lines.append(f"UpdatedAt={sanitize_inc(updated_at)}")

    content = "\n".join(lines) + "\n"

    for path in (LOCAL_INC, SKIN_INC):
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            # UTF-16 LE (with BOM) is Rainmeter's native encoding
            # (Rainmeter.ini itself is UTF-16). Must match monitor.ini.
            # Atomic write (tmp + replace) so readers never see torn files.
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-16") as f:
                f.write(content)
            os.replace(tmp, path)
        except Exception as e:
            print(f"[!] Could not write {path}: {e}")

    # Nudge Rainmeter to reload the new variables immediately.
    # Skin also auto-refreshes every 15 min as a fallback, so failure here is non-fatal.
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


def fetch_data(cfg):
    subscriber = (cfg.get("SLT_SUBSCRIBER_ID") or "").strip()
    if not subscriber:
        print("[!] SLT_SUBSCRIBER_ID missing in .env")
        return None
    url = f"{API_BASE}?subscriberID={subscriber}"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:156.0) Gecko/20100101 Firefox/156.0",
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
        print(f"[!] Request exception: {e}")
        return None


def parse_usage(payload):
    """Map UsageSummary JSON -> flat dict for variables.inc.

    Live shape (24-Sep-2026):
      dataBundle.status = THROTTLED
      dataBundle.reported_time = 24-Sep-2026 10:32 PM
      my_package_info.package_name = WEB FAMILY PLUS
      usageDetails[] = [{name: Standard, limit, remaining, ...},
                        {name: Total (Standard + Free), ...}]
    """
    db = (payload.get("dataBundle") or {}) if isinstance(payload, dict) else {}
    info = (db.get("my_package_info") or {}) if isinstance(db, dict) else {}
    details = info.get("usageDetails") or []
    if not isinstance(details, list):
        details = []

    def find(pred):
        for d in details:
            if isinstance(d, dict) and pred(d):
                return d
        return {}

    std = find(lambda d: (d.get("name") or "").strip().lower() == "standard")
    total = find(lambda d: "total" in (d.get("name") or "").lower())
    if not total and details:
        # Fallback: largest limit entry is usually the total.
        try:
            total = max([d for d in details if isinstance(d, dict)],
                        key=lambda d: float(d.get("limit") or 0))
        except Exception:
            total = details[0] if isinstance(details[0], dict) else {}

    unit = (std.get("volume_unit") or total.get("volume_unit") or "GB").strip() or "GB"

    def fmt_pair(remaining, limit):
        r = (str(remaining or "").strip(), str(limit or "").strip())
        if r[0] and r[1]:
            return f"{r[0]} {unit} / {r[1]} {unit}"
        if r[0]:
            return f"{r[0]} {unit}"
        if r[1]:
            return f"{r[1]} {unit}"
        return ""

    def fmt_bonus(node):
        if not isinstance(node, dict):
            return ""
        lim, used = str(node.get("limit") or "").strip(), str(node.get("used") or "").strip()
        u = str(node.get("volume_unit") or "GB").strip() or "GB"
        if not lim and not used:
            return ""
        try:
            rem = float(lim or 0) - float(used or 0)
            rem_s = ("%g" % rem)
        except Exception:
            rem_s = ""
        if rem_s and lim:
            return f"{rem_s} / {lim} {u}"
        return ""

    return {
        "status": str(db.get("status") or "UNKNOWN").strip(),
        "reported_time": str(db.get("reported_time") or info.get("reported_time") or "").strip(),
        "package_name": str(info.get("package_name") or "").strip(),
        "std_remaining": str(std.get("remaining") or "").strip(),
        "std_limit": str(std.get("limit") or "").strip(),
        "std_data": fmt_pair(std.get("remaining"), std.get("limit")),
        "total_remaining": str(total.get("remaining") or "").strip(),
        "total_limit": str(total.get("limit") or "").strip(),
        "total_data": fmt_pair(total.get("remaining"), total.get("limit")),
        "unit": unit,
        "expiry": str(std.get("expiry_date") or total.get("expiry_date") or "").strip(),
        "bonus_data": fmt_bonus(db.get("bonus_data_summary")),
        "extra_data": fmt_bonus(db.get("extra_gb_data_summary")),
    }


def load_last_good():
    """Return last-good parsed dict from data.json so expiry states keep
    numbers on screen instead of blanking the skin."""
    try:
        if os.path.exists(DATA_FILE):
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                body = json.load(f)
                if isinstance(body, dict) and isinstance(body.get("data"), dict):
                    return body["data"]
    except Exception:
        pass
    return {}


def get_fresh_auth_auto():
    """Playwright auto-login (user choice): open MySLT, let the user log in,
    sniff the UsageSummary request for a fresh Authorization header, grab
    cookies, save to .env. Returns (authorization, cookie) or (None, cookie).
    Manual .env paste remains the fallback (hot-reloaded every cycle)."""
    print("[*] Launching browser for MySLT login...")
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("[!] playwright not installed. Run: pip install playwright && playwright install chromium")
        print("[!] Fallback: paste a fresh Authorization + Cookie into .env manually.")
        return None, None

    captured = {}
    cookie_str = ""
    with sync_playwright() as p:
        try:
            browser = p.chromium.launch(headless=False)
        except Exception as e:
            print(f"[!] Could not launch Chromium: {e}")
            print("[!] Run once: playwright install chromium")
            return None, None
        context = browser.new_context()
        page = context.new_page()

        def on_request(req):
            try:
                url = req.url or ""
                if "omniscapp.slt.lk" in url and "UsageSummary" in url:
                    hdrs = req.headers or {}
                    auth = hdrs.get("authorization") or hdrs.get("Authorization")
                    if auth and "authorization" not in captured:
                        captured["authorization"] = auth
                        print("[+] Sniffed fresh Authorization from UsageSummary call.")
            except Exception:
                pass

        page.on("request", on_request)
        try:
            page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=60000)
        except Exception as e:
            print(f"[!] Navigation issue (continuing, complete login manually): {e}")

        print("[*] Please LOG IN to MySLT in the opened browser window.")
        print("[*] Waiting up to 180s for a fresh session (auto-continues on capture)...")
        for _ in range(180):
            time.sleep(1)
            if captured.get("authorization"):
                time.sleep(2)  # let cookies settle
                break
            try:
                # If user already logged in, visiting the dashboard XHRs may fire the API.
                url_now = (page.url or "").lower()
                if "omniscapp" in url_now:
                    pass
            except Exception:
                pass
        else:
            print("[!] Timed out waiting for UsageSummary traffic.")

        # Fallback: sweep localStorage / sessionStorage for bearer-looking tokens.
        if "authorization" not in captured:
            for store_name in ("localStorage", "sessionStorage"):
                try:
                    dump = page.evaluate(
                        f"() => {{ const o={{}}; try {{ for (let i=0;i<{store_name}.length;i++)"
                        f"{{ const k={store_name}.key(i); o[k]={store_name}.getItem(k); }} }} catch(e) {{}} return o; }}"
                    ) or {}
                    for k, v in dump.items():
                        if isinstance(v, str) and len(v) > 60 and (
                            "bearer" in v.lower() or v.startswith("4KR") or "eyJ" in v[:3]
                        ):
                            captured["authorization"] = v if v.lower().startswith("bearer") else "bearer " + v
                            print(f"[+] Recovered bearer from {store_name} key {k!r}.")
                            break
                    if "authorization" in captured:
                        break
                except Exception as e:
                    print(f"[!] {store_name} sweep failed: {e}")

        try:
            cookies = context.cookies()
            cookie_str = "; ".join(f"{c['name']}={c['value']}" for c in cookies if c.get("value"))
        except Exception as e:
            print(f"[!] Cookie capture failed: {e}")
        try:
            browser.close()
        except Exception:
            pass

    auth = captured.get("authorization")
    if auth or cookie_str:
        save_auth_to_env(auth, cookie_str if cookie_str else None)
    if not auth:
        print("[!] Could not auto-capture bearer. Fallback: open DevTools -> Network ->")
        print("    UsageSummary -> copy Authorization + Cookie into .env (hot-reloaded).")
    return auth, cookie_str


def is_session_expired_response(res):
    """True when the failure looks like auth/session (worth one auto-login)."""
    if res is None:
        return False  # network blip -> don't pop a browser
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


def update_data_cycle():
    cfg = load_config()  # hot-reload: manual .env paste takes effect here
    res = fetch_data(cfg)

    if is_session_expired_response(res):
        print("[!] Session expired/blocked. Triggering Playwright re-login...")
        auth, _cookie = get_fresh_auth_auto()
        cfg = load_config()  # re-read whatever auto-login (or manual paste) saved
        # One retry only; if the user closed the browser, retry with old session once.
        res = fetch_data(cfg)
        if is_session_expired_response(res):
            print("[!] Still expired after re-login. Keeping last-good values.")
            last = load_last_good()
            now = time.strftime("%I:%M:%S %p")
            write_variables_inc(last, "expired", now)
            return

    if res is not None and res.status_code == 200:
        try:
            payload = res.json()
        except Exception:
            print("[!] Non-JSON response (possible Incapsula block).")
            last = load_last_good()
            write_variables_inc(last, "error", time.strftime("%I:%M:%S %p"))
            return
        if not isinstance(payload, dict) or not payload.get("dataBundle"):
            print(f"[!] Unexpected payload: {str(payload)[:200]}")
            last = load_last_good()
            write_variables_inc(last, "error", time.strftime("%I:%M:%S %p"))
            return
        data = parse_usage(payload)
        now = time.strftime("%I:%M:%S %p")
        # Backup data.json for debugging (atomic so readers never see torn JSON).
        try:
            tmp = DATA_FILE + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"status": "success", "updated_at": now, "data": data}, f)
            os.replace(tmp, DATA_FILE)
        except Exception as e:
            print(f"[!] Could not write data.json: {e}")
        write_variables_inc(data, "success", now)
        print(f"[+] {data.get('package_name')} | Std {data.get('std_data')} | "
              f"Total {data.get('total_data')} | {data.get('status')}")
    else:
        status = res.status_code if res is not None else "No Response"
        print(f"[!] Fetch failed with status: {status}")
        last = load_last_good()
        try:
            write_variables_inc(last, "error", time.strftime("%I:%M:%S %p"))
        except Exception as e:
            print(f"[!] Could not write error state: {e}")


if __name__ == "__main__":
    while True:
        update_data_cycle()
        time.sleep(REFRESH_SECONDS)  # 15 min (user choice)
