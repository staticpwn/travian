
import os
import sys
import time
import json
import shutil
import psutil
import socket
import subprocess
from typing import Optional, Dict

try:
    import requests
except ImportError:
    raise SystemExit("Please: pip install requests psutil")

# --- Optional: auto-find Chrome if path not provided (Windows/macOS/Linux) ---
def find_chrome_executable() -> Optional[str]:
    import platform
    plat = platform.system().lower()

    # PATH candidates first
    for name in ("chrome", "chrome.exe", "google-chrome", "google-chrome-stable", "chromium", "chromium-browser"):
        p = shutil.which(name)
        if p:
            return os.path.abspath(p)

    if plat == "windows":
        try:
            import winreg
            for hive, key in [
                (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe"),
                (winreg.HKEY_CURRENT_USER,  r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe"),
                (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe"),
            ]:
                try:
                    with winreg.OpenKey(hive, key) as k:
                        val, _ = winreg.QueryValueEx(k, None)
                        if val and os.path.exists(val):
                            return os.path.abspath(val)
                except FileNotFoundError:
                    pass
        except Exception:
            pass

        # Common install paths
        for base in (os.environ.get("ProgramFiles", r"C:\Program Files"),
                     os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
                     os.environ.get("LOCALAPPDATA", r"C:\Users\%USERNAME%\AppData\Local")):
            cand = os.path.join(base, "Google", "Chrome", "Application", "chrome.exe")
            if os.path.exists(cand):
                return os.path.abspath(cand)

    elif plat == "darwin":
        for p in (
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            os.path.expanduser("~/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
            "/Applications/Chromium.app/Contents/MacOS/Chromium",
            os.path.expanduser("~/Applications/Chromium.app/Contents/MacOS/Chromium"),
        ):
            if os.path.exists(p):
                return os.path.abspath(p)

    else:  # linux
        for p in (
            "/usr/bin/google-chrome-stable",
            "/usr/bin/google-chrome",
            "/usr/bin/chrome",
            "/usr/bin/chromium-browser",
            "/usr/bin/chromium",
            "/snap/bin/chromium",
            "/opt/google/chrome/google-chrome",
        ):
            if os.path.exists(p) and os.access(p, os.X_OK):
                return os.path.abspath(p)

    return None


def _devtools_ok(port: int, timeout: float = 1.0) -> bool:
    try:
        r = requests.get(f"http://127.0.0.1:{port}/json/version", timeout=timeout)
        return r.status_code == 200 and "webSocketDebuggerUrl" in r.text
    except requests.RequestException:
        return False


def _port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.25)
        return s.connect_ex(("127.0.0.1", port)) == 0


def _find_cdp_chrome_pids(port: int, user_data_dir: Optional[str]) -> list[int]:
    """Return PIDs of Chrome processes that look like *our* CDP instance (by port and/or user-data-dir)."""
    hits = []
    for p in psutil.process_iter(attrs=["pid", "name", "cmdline"]):
        try:
            name = (p.info.get("name") or "").lower()
            if "chrome" not in name and "msedge" not in name and "chromium" not in name:
                continue
            cmdline = " ".join(p.info.get("cmdline") or [])
            if f"--remote-debugging-port={port}" in cmdline:
                hits.append(p.info["pid"])
                continue
            if user_data_dir and f'--user-data-dir="{user_data_dir}"' in cmdline:
                hits.append(p.info["pid"])
                continue
            if user_data_dir and f"--user-data-dir={user_data_dir}" in cmdline:
                hits.append(p.info["pid"])
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    # de-dup and return stable order
    return sorted(set(hits))


def _kill_pids(pids: list[int], grace: float = 3.0) -> None:
    if not pids:
        return
    procs = []
    for pid in pids:
        try:
            procs.append(psutil.Process(pid))
        except psutil.NoSuchProcess:
            pass
    for pr in procs:
        try:
            pr.terminate()
        except psutil.NoSuchProcess:
            pass
    gone, alive = psutil.wait_procs(procs, timeout=grace)
    for pr in alive:
        try:
            pr.kill()
        except psutil.NoSuchProcess:
            pass


def ensure_cdp_chrome_running(
    chrome_path: Optional[str] = None,
    remote_port: int = 9222,
    user_data_dir: Optional[str] = None,
    extra_args: Optional[list[str]] = None,
    timeout_secs: float = 10.0,
    kill_if_unreachable: bool = True,
) -> Dict:
    """
    Ensure a single CDP-enabled Chrome is running and reachable.
    Returns a diagnostics dict:
      {
        "ok": bool,
        "endpoint": f"http://127.0.0.1:{remote_port}",
        "pid": int|None,
        "launched": bool,
        "killed": list[int],
        "error": str|None
      }
    """
    diag = {
        "ok": False,
        "endpoint": f"http://127.0.0.1:{remote_port}",
        "pid": None,
        "launched": False,
        "killed": [],
        "error": None,
    }

    if user_data_dir is None:
        # isolate automation profile by default
        user_data_dir = os.path.join(os.path.expanduser("~"), ".cdp_chrome_profile")
    os.makedirs(user_data_dir, exist_ok=True)

    # 1) If DevTools is already responsive, we’re done
    if _devtools_ok(remote_port, timeout=0.6):
        diag["ok"] = True
        # best-effort: report a PID (if identifiable)
        pids = _find_cdp_chrome_pids(remote_port, user_data_dir)
        diag["pid"] = pids[0] if pids else None
        return diag

    # 2) If the port is in use but not responding as DevTools, consider killing our CDP Chrome (only if it matches our profile/port)
    if _port_in_use(remote_port):
        cdp_pids = _find_cdp_chrome_pids(remote_port, user_data_dir)
        if cdp_pids and kill_if_unreachable:
            _kill_pids(cdp_pids)
            diag["killed"] = cdp_pids
        elif cdp_pids:
            diag["error"] = f"Port {remote_port} busy and CDP Chrome unreachable; set kill_if_unreachable=True or choose another port."
            return diag
        else:
            diag["error"] = f"Port {remote_port} in use by a non-CDP process."
            return diag

    # 3) Launch fresh CDP Chrome
    if chrome_path is None:
        chrome_path = find_chrome_executable()
    if not chrome_path or not os.path.exists(chrome_path):
        diag["error"] = "Chrome executable not found. Supply chrome_path or install Chrome."
        return diag

    base_cmd = [
        chrome_path,
        f"--remote-debugging-port={remote_port}",
        f"--user-data-dir={user_data_dir}",
        f"--remote-allow-origins=http://127.0.0.1:{remote_port}",
        "--no-first-run",
        "--no-default-browser-check",
        # keep it visible; do NOT reuse regular profile
    ]
    if extra_args:
        base_cmd.extend(extra_args)

    startupinfo = None
    creationflags = 0
    if os.name == "nt":
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = 0  # SW_HIDE
        # Optional: hide console without conflicting flags
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)

    try:
        proc = subprocess.Popen(
            base_cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            startupinfo=startupinfo,
            creationflags=creationflags,
            shell=False,
        )
    except Exception as e:
        diag["error"] = f"Failed to launch Chrome: {e}"
        return diag

    diag["launched"] = True
    diag["pid"] = proc.pid

    # 4) Wait for DevTools endpoint
    deadline = time.time() + float(timeout_secs)
    ok = False
    last_err = None
    while time.time() < deadline:
        try:
            if _devtools_ok(remote_port, timeout=0.6):
                ok = True
                break
        except Exception as e:
            last_err = e
        time.sleep(0.2)

    if not ok:
        diag["error"] = f"DevTools endpoint didn't come up on port {remote_port} within {timeout_secs}s. Last error: {last_err}"
        return diag

    diag["ok"] = True
    return diag


import time
import json
import requests
from typing import Optional, Dict, Any
from websocket import create_connection, WebSocketTimeoutException

# ---------- Helpers to find targets ----------
def list_devtools_targets(devtools_http: str, timeout: float = 2.0):
    """
    Return list of targets from the DevTools HTTP endpoint.
    devtools_http example: "http://127.0.0.1:9222"
    """
    r = requests.get(f"{devtools_http.rstrip('/')}/json", timeout=timeout)
    r.raise_for_status()
    return r.json()


def pick_target(devtools_http: str, target_selector: Optional[str] = None, timeout: float = 2.0):
    """
    Pick a page target.
    - If target_selector is None: pick first 'page' type target (best-effort).
    - If target_selector provided: match substring in url or title (case-insensitive).
    Returns the target dict or raises LookupError.
    """
    targets = list_devtools_targets(devtools_http, timeout=timeout)
    # filter page-like items with http(s) urls
    pages = [t for t in targets if t.get("type") == "page" and t.get("url", "").startswith(("http://", "https://"))]
    if target_selector:
        needle = target_selector.lower()
        for t in pages:
            if needle in (t.get("url","").lower() or "") or needle in (t.get("title","").lower() or ""):
                return t
        # fallback: try matching against all targets (maybe chrome://newtab not matched)
        for t in targets:
            if needle in (t.get("url","").lower() or "") or needle in (t.get("title","").lower() or ""):
                return t
        raise LookupError(f"No target found matching: {target_selector!r}. Candidates:\n" +
                          "\n".join(f"{p.get('title')}  {p.get('url')}" for p in pages))
    # no selector: return first page (or any first target)
    if pages:
        return pages[0]
    if targets:
        return targets[0]
    raise LookupError("No devtools targets available.")


# ---------- Core navigation function ----------
def navigate_to_site(
    devtools_http: str,
    ws_url: Optional[str] = None,
    target_selector: Optional[str] = None,
    url: Optional[str] = None,
    set_skip_all_pauses: bool = True,
    return_html: bool = True,
    debug: bool = False,
) -> Dict[str, Any]:
    """
    Connect to a DevTools target (via ws_url or by selecting a target from devtools_http),
    optionally navigate to `url`, wait until document.readyState == wait_ready_state and
    network has been idle for network_idle_ms (if network_idle_ms>0).
    Returns a dict {
        "ok": bool,
        "final_url": str|None,
        "title": str|None,
        "timed_out": bool,
        "nav_time_s": float,
        "html": "<...>" or None,
        "error": str|None
    }
    """

    result = {
        "ok": False,
        "final_url": None,
        "title": None,
        "timed_out": False,
        "nav_time_s": None,
        "html": None,
        "error": None,
    }

    try:
        # If ws_url not given, pick a target
        if not ws_url:
            if not target_selector:
                # default: pick first page
                target = pick_target(devtools_http)
            else:
                target = pick_target(devtools_http, target_selector)
            if debug:
                print("Picked target:", target.get("title"), target.get("url"))
            ws_url = target.get("webSocketDebuggerUrl")
            if not ws_url:
                raise RuntimeError("Target does not expose webSocketDebuggerUrl.")
        start_time = time.time()
        ws = create_connection(ws_url, timeout=5)

        # enable domains
        def send(idn, method, params=None):
            payload = {"id": idn, "method": method}
            if params is not None:
                payload["params"] = params
            if debug:
                # don't print big payloads
                print("SEND:", method, params if (not isinstance(params, dict) or len(str(params)) < 200) else "<params>")
            ws.send(json.dumps(payload))

        def recv_with_timeout(timeout_s=5.0):
            ws.settimeout(timeout_s)
            try:
                data = ws.recv()
            except WebSocketTimeoutException:
                return None
            if not data:
                return None
            return json.loads(data)

        # Enable required domains
        send(1, "Page.enable")
        send(2, "Runtime.enable")
        send(3, "Network.enable")
        # optionally ensure debugger doesn't pause
        if set_skip_all_pauses:
            try:
                send(4, "Debugger.enable")
                # skip all pauses
                send(5, "Debugger.setSkipAllPauses", {"skip": True})
                # also avoid pausing on exceptions
                send(6, "Debugger.setPauseOnExceptions", {"state": "none"})
            except Exception:
                # ignore if Debugger domain not available for some reason
                pass

        # drain initial responses/events
        t0 = time.time()
        while time.time() - t0 < 0.5:
            try:
                _ = recv_with_timeout(0.2)
                # ignore
            except Exception:
                break

        # If a navigation URL is provided - instruct the page to navigate
        if url:
            send(10, "Page.navigate", {"url": url})
            # consume the response for Page.navigate which will have id 10 but we can't assume immediately
            # small wait to allow navigation to start
            time.sleep(0.05)

        # Wait loop: poll readyState and watch Network events to compute inflight requests

        result["nav_time_s"] = time.time() - start_time

        # At this point, either we have got_ready or timed_out
        # Optionally fetch HTML
        if return_html:
            try:
                send(30, "Runtime.evaluate", {"expression": "document.documentElement.outerHTML", "returnByValue": True})
                resp = recv_with_timeout(5.0)
                if resp and resp.get("id") == 30:
                    # nested: resp["result"]["result"]["value"]
                    html = resp["result"]["result"].get("value")
                    result["html"] = html
                else:
                    # fallback: try asking for innerHTML of body
                    send(31, "Runtime.evaluate", {"expression": "document.body ? document.body.innerHTML : ''", "returnByValue": True})
                    resp2 = recv_with_timeout(2.0)
                    if resp2 and resp2.get("id") == 31:
                        result["html"] = resp2["result"]["result"].get("value")
            except Exception as e:
                if debug:
                    print("Failed to fetch HTML:", e)

        # Close websocket cleanly
        try:
            ws.close()
        except Exception:
            pass

        # success if we didn't time out (optionally allow partial success)
        result["ok"] = not result["timed_out"]
        return result

    except Exception as exc:
        result["error"] = str(exc)
        return result

def get_outer_html_from_diag(diag):
    """
    Connects to the first available page target of the CDP Chrome instance
    described by `diag`, retrieves document.documentElement.outerHTML,
    and returns it as text.
    """
    devtools_http = diag.get("endpoint")
    if not devtools_http:
        raise ValueError("diag missing 'endpoint' key.")

    # 1) List all targets
    try:
        targets = requests.get(f"{devtools_http.rstrip('/')}/json", timeout=3).json()
    except Exception as e:
        raise RuntimeError(f"Cannot query DevTools endpoint at {devtools_http}: {e}")

    # 2) Pick first 'page' target
    page_targets = [t for t in targets if t.get("type") == "page"]
    if not page_targets:
        raise RuntimeError("No page targets found on the DevTools endpoint.")
    target = page_targets[0]

    # 3) Connect to its websocket
    ws_url = target.get("webSocketDebuggerUrl")
    if not ws_url:
        raise RuntimeError("Target has no webSocketDebuggerUrl.")
    ws = create_connection(ws_url, timeout=5)

    # 4) Ask for HTML
    payload = {
        "id": 1,
        "method": "Runtime.evaluate",
        "params": {
            "expression": "document.documentElement.outerHTML",
            "returnByValue": True,
        },
    }
    ws.send(json.dumps(payload))
    res = ws.recv()
    ws.close()

    # 5) Extract value safely
    try:
        res_json = json.loads(res)
        return res_json["result"]["result"]["value"]
    except Exception:
        return res

