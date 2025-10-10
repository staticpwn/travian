import re
from typing import List, Dict, Any, Tuple, Optional
import json, time, requests
from websocket import create_connection, WebSocketTimeoutException

from bs4 import BeautifulSoup  # pip install beautifulsoup4

_GID_RE = re.compile(r"gid-(\d+)")

def extract_build_items(html: str) -> List[Dict[str, Any]]:
    """
    Parse the given HTML and extract building items from:
        #build_list > .build_list__column > .build_list__item[id="gid-<num>"]

    Returns a list of dicts: [{ "gid": int, "name": str, "column": int, "raw_id": str }]
    - 'name' is taken as the first non-empty text line within the item
    - 'column' is zero-based index of the column the item was found in
    """
    soup = BeautifulSoup(html, "html.parser")
    container = soup.select_one("#build_list")
    if not container:
        return []

    results: List[Dict[str, Any]] = []

    columns = container.select(".build_list__column")
    for col_idx, col in enumerate(columns):
        for item in col.select(".build_list__item"):
            rid = item.get("id", "")  # e.g., "gid-19"
            m = _GID_RE.search(rid)
            if not m:
                continue
            gid = int(m.group(1))

            # Get a clean, human-readable name:
            # take the first line of text inside the item.
            text_block = item.get_text(separator="\n", strip=True)
            name = next((ln for ln in text_block.split("\n") if ln), "")

            results.append({
                "gid": gid,
                "name": name,
                "column": col_idx,
                "raw_id": rid,
            })

    # optional: stable ordering by gid then name
    results.sort(key=lambda r: (r["column"], r["gid"], r["name"]))
    return results

_cdp_msg_id = 1000
def _cdp_call(ws, method, params=None, timeout=6.0):
    """Send a CDP command and return the response that has the same 'id'."""
    global _cdp_msg_id
    _cdp_msg_id += 1
    msg_id = _cdp_msg_id
    ws.send(json.dumps({"id": msg_id, "method": method, "params": params or {}}))

    deadline = time.time() + timeout
    while time.time() < deadline:
        ws.settimeout(max(0.05, deadline - time.time()))
        try:
            raw = ws.recv()
        except WebSocketTimeoutException:
            break
        if not raw:
            continue
        data = json.loads(raw)
        # ignore async events (no 'id'); return only our matching response
        if data.get("id") == msg_id:
            if "error" in data:
                raise RuntimeError(f"CDP error for {method}: {data['error']}")
            return data
    raise TimeoutError(f"Timed out waiting for response to {method}")

def click_element(diag: dict, selector: str, click_count: int = 1) -> bool:
    """
    Click an element in the current page using CDP, given:
      - diag['endpoint'] (e.g., 'http://127.0.0.1:9222')
      - CSS selector for the target element (e.g., '#gid-19')

    Returns True on success; raises if element not found.
    """
    # 1) discover a page target and get its ws URL
    base = diag["endpoint"].rstrip("/")
    targets = requests.get(f"{base}/json", timeout=3).json()
    page = next((t for t in targets if t.get("type") == "page"), None)
    if not page:
        raise RuntimeError("No 'page' targets found on DevTools endpoint.")
    ws_url = page.get("webSocketDebuggerUrl")
    if not ws_url:
        raise RuntimeError("Selected target has no webSocketDebuggerUrl.")

    # 2) connect and enable domains
    ws = create_connection(ws_url, timeout=8)
    try:
        _cdp_call(ws, "Page.enable")
        _cdp_call(ws, "Runtime.enable")
        _cdp_call(ws, "DOM.enable")
        try:
            _cdp_call(ws, "Debugger.enable")
            _cdp_call(ws, "Debugger.setSkipAllPauses", {"skip": True})
            _cdp_call(ws, "Debugger.setPauseOnExceptions", {"state": "none"})
        except Exception:
            pass

        # 3) locate element nodeId
        doc = _cdp_call(ws, "DOM.getDocument", {"depth": 1, "pierce": True})
        root_id = doc["result"]["root"]["nodeId"]

        q = _cdp_call(ws, "DOM.querySelector", {"nodeId": root_id, "selector": selector})
        node_id = q["result"]["nodeId"]
        if not node_id:
            raise ValueError(f"Element not found for selector: {selector!r}")

        # 4) scroll into view, compute center point, click (trusted mouse events)
        _cdp_call(ws, "DOM.scrollIntoViewIfNeeded", {"nodeId": node_id}, timeout_s=2.0)
        bm = _cdp_call(ws, "DOM.getBoxModel", {"nodeId": node_id}, timeout_s=2.0)
        content = bm["result"]["model"]["content"]  # [x1,y1, x2,y2, x3,y3, x4,y4]
        x = (content[0] + content[2] + content[4] + content[6]) / 4.0
        y = (content[1] + content[3] + content[5] + content[7]) / 4.0

        for i in range(click_count):
            _cdp_call(ws, "Input.dispatchMouseEvent", {"type": "mouseMoved", "x": x, "y": y}, timeout_s=1.0)
            _cdp_call(ws, "Input.dispatchMouseEvent", {"type": "mousePressed", "x": x, "y": y, "button": "left", "clickCount": 1}, timeout_s=1.0)
            _cdp_call(ws, "Input.dispatchMouseEvent", {"type": "mouseReleased", "x": x, "y": y, "button": "left", "clickCount": 1}, timeout_s=1.0)
            time.sleep(0.05)

        return True
    finally:
        try:
            ws.close()
        except Exception:
            pass


def extract_table_rows(html: str, name) -> List[List[str]]:
    """
    Extracts the first 5 <td> values of each <tr> (excluding thead and last row)
    from a table with id='data'.

    Returns:
        List[List[str]] where each inner list is a row of up to 5 cell strings.
    """
    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table", {"id": "data"})
    if not table:
        return []

    # Find all body rows (ignore thead entirely)
    body_rows = table.find_all("tr")
    if not body_rows:
        return []

    # Drop the last row (typically totals)
    body_rows = body_rows[1:-1]

    extracted = []
    for tr in body_rows:
        tds = tr.find_all("td")
        if not tds:
            continue
        row = [td.get_text(strip=True) for td in tds[:5]]

        row_dict = {
            "name": name,
            "lvl": row[0] if len(row) > 0 else "",
            "wood": row[1] if len(row) > 1 else "",
            "clay": row[2] if len(row) > 2 else "",
            "iron": row[3] if len(row) > 3 else "",
            "crop": row[4] if len(row) > 4 else "",
        }

        extracted.append(row_dict)

    return extracted


_GID_RE = re.compile(r"^\d{7}$")  # exactly 7 digits
_LAST4_RE = re.compile(r"\b(\d{4})\b")   # last <text> contains 4-digit int

def extract_svg_group_ids(html: str) -> List[str]:
    """
    Extracts all <g> element IDs that are 7-digit numbers from the SVG
    with id='centerCanvas'.

    Returns:
        List[str] of matching group IDs (e.g., ['1234567', '9876543']).
    """
    soup = BeautifulSoup(html, "html.parser")
    canvas = soup.find("svg", {"id": "centerCanvas"})
    if not canvas:
        return []

    results: List[Dict[str, int]] = []

    for g in canvas.find_all("g", id=True):
        gid = g["id"].strip()
        if not _GID_RE.match(gid):
            continue

        texts = g.find_all("text")
        if not texts:
            continue

        last_text = texts[-1].get_text(strip=True)
        m = _LAST4_RE.search(last_text)
        if not m:
            continue

        results.append({"id": gid, "flight": int(m.group(1))})
    return results


def get_flight_details_url(id):
    return f"http://apollo.emirates.com/Intranet/WebUI/FlightWatch/Flight/FlightDetails.aspx?fltseqnm={id}&tz=UTC"


import calendar
from datetime import date
from datetime import datetime

def month_bounds(year: int, month: int):
    """
    Return the first and last day of the given month and year.

    Args:
        year (int): e.g. 2025
        month (int): 1–12

    Returns:
        (first_day: date, last_day: date)
    """
    # first day is always 1
    first_day = date(year, month, 1)
    # last day = number of days in that month (calendar.monthrange returns (weekday, days))
    last_day = date(year, month, calendar.monthrange(year, month)[1])
    return str(first_day.day).zfill(2), str(last_day.day).zfill(2)


def _eval(ws, expr: str, timeout: float = 6.0):
    # assumes you already have _cdp_call from your snippet
    _cdp_call(ws, "Runtime.enable")
    _cdp_call(ws, "Page.enable")  # harmless if already enabled
    return _cdp_call(ws, "Runtime.evaluate", {
        "expression": expr,
        "returnByValue": True,
        "awaitPromise": True
    }, timeout=timeout)

def is_loader_visible(ws_url: str, timeout: float = 6.0) -> bool:
    """
    Connects to the page WS and checks if #loader is effectively visible.
    Uses computed styles; treats non-existent element as not visible.
    """
    ws = create_connection(ws_url, timeout=8)
    try:
        expr = r"""
        (() => {
          const el = document.getElementById('loader');
          if (!el) return {exists:false, visible:false};
          const cs = window.getComputedStyle(el);
          const visible = cs.visibility !== 'hidden'
                          && cs.display !== 'none'
                          && parseFloat(cs.opacity || '1') > 0.01;
          return {exists:true, visible};
        })()
        """
        resp = _eval(ws, expr, timeout=timeout)
        val = (resp.get("result") or {}).get("result", {}).get("value", {})
        return bool(val.get("visible", False))
    finally:
        ws.close()

def wait_until_loader_hidden(
    ws_url: str,
    timeout: float = 30.0,
    poll_interval: float = 0.25,
    require_stable_samples: int = 2
) -> bool:
    """
    Polls until #loader is hidden for 'require_stable_samples' consecutive polls.
    Returns True if achieved within 'timeout', else False.
    """
    deadline = time.time() + timeout
    stable = 0
    while time.time() < deadline:
        if not is_loader_visible(ws_url, timeout=6.0):
            stable += 1
            if stable >= require_stable_samples:
                return True
        else:
            stable = 0
        time.sleep(poll_interval)
    return False

def get_flight_details(flight_details_html):

    soup = BeautifulSoup(flight_details_html, 'html.parser')
    time_element = soup.find('span', id='lblFlightTime')
    header_element = soup.find('span', id='UCHeader_lblFlightHeader')
    header_full_text =  header_element.text.strip()
    flight_number, arrival_date = header_full_text.split("/")[2:4]

    flight_details_dict = {
        "flight_number": flight_number.strip(),
        "arrival_date": arrival_date.strip(),
        "flight_time": time_element.text.strip() if time_element else None
    }
    
    return flight_details_dict



def convert_date_slash_to_dash(date_str: str) -> str:
    """
    Convert a date from 'DD/MM/YYYY' to 'DD-MMM-YYYY' (e.g. '01/09/2025' → '01-SEP-2025').

    Returns uppercase month abbreviation.
    """
    dt = datetime.strptime(date_str, "%d/%m/%Y")
    return dt.strftime("%d-%b-%Y").upper()



def set_dates_and_run(diag, start_date: str, end_date: str, ws_url=None):
    """
    Uses Chrome DevTools Protocol to:
      - type start_date into #txtFromDate
      - type end_date into #txtToDate
      - click the run button #imgLoad

    Args:
        diag (dict): Diagnostic info with 'endpoint' (e.g. http://127.0.0.1:9222)
        start_date (str): e.g. '2025-10-01'
        end_date (str): e.g. '2025-10-31'
    """
    if not ws_url:
        base = diag["endpoint"].rstrip("/")
        targets = requests.get(f"{base}/json").json()
        page = next((t for t in targets if t.get("type") == "page"), None)
        if not page:
            raise RuntimeError("No page targets found.")
        ws_url = page.get("webSocketDebuggerUrl")
        if not ws_url:
            raise RuntimeError("Selected target has no webSocketDebuggerUrl.")
    
    ws = create_connection(ws_url, timeout=8)
    try:
        # Enable required domains
        for cmd in ["Page.enable", "Runtime.enable", "DOM.enable"]:
            _cdp_call(ws, cmd)

        # Get root document node
        doc = _cdp_call(ws, "DOM.getDocument", {"depth": 1, "pierce": True})
        root_id = doc["result"]["root"]["nodeId"]

        # Helper to set text by selector
        def set_text(selector, value):
            q = _cdp_call(ws, "DOM.querySelector", {"nodeId": root_id, "selector": selector})
            node_id = q["result"]["nodeId"]
            if not node_id:
                raise ValueError(f"Element not found: {selector}")
            rn = _cdp_call(ws, "DOM.resolveNode", {"nodeId": node_id})
            obj_id = rn["result"]["object"]["objectId"]
            _cdp_call(ws, "Runtime.callFunctionOn", {
                "objectId": obj_id,
                "functionDeclaration": "(function(val){ this.value = val; this.dispatchEvent(new Event('input')); })",
                "arguments": [{"value": value}],
            })

        # Set the dates
        set_text("#txtFromDate", convert_date_slash_to_dash(start_date))
        set_text("#txtToDate", convert_date_slash_to_dash(end_date))

        # Click the run image button
        q = _cdp_call(ws, "DOM.querySelector", {"nodeId": root_id, "selector": "#imgLoad"})
        node_id = q["result"]["nodeId"]
        if not node_id:
            raise ValueError("Run button #imgLoad not found.")

        # Scroll into view and click it
        _cdp_call(ws, "DOM.scrollIntoViewIfNeeded", {"nodeId": node_id})
        bm = _cdp_call(ws, "DOM.getBoxModel", {"nodeId": node_id})
        content = bm["result"]["model"]["content"]
        x = sum(content[::2]) / 4
        y = sum(content[1::2]) / 4

        for etype in ["mouseMoved", "mousePressed", "mouseReleased"]:
            _cdp_call(ws, "Input.dispatchMouseEvent", {
                "type": etype,
                "x": x,
                "y": y,
                "button": "left",
                "clickCount": 1
            })
            time.sleep(0.05)

        print("✅ Dates set and run button clicked successfully.")
        return True

    finally:
        try:
            ws.close()
        except Exception:
            pass