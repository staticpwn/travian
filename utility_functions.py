
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

