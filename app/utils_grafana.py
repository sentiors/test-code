import os
import requests
import re
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import time

GRAFANA_URL = os.getenv("GRAFANA_URL")
GRAFANA_USER = os.getenv("GRAFANA_USER") 
GRAFANA_PASS = os.getenv("GRAFANA_PASS")

_session = None

def get_grafana_session():
    global _session
    if _session:
        return _session
    
    if not all([GRAFANA_URL, GRAFANA_USER, GRAFANA_PASS]):
        return None
    
    _session = requests.Session()
    login_url = f"{GRAFANA_URL.rstrip('/')}/login"
    
    login_data = {"user": GRAFANA_USER, "password": GRAFANA_PASS}
    r = _session.post(login_url, json=login_data, timeout=10)
    
    if r.status_code != 200 or "Logged in" not in r.text:
        print(f"DEBUG: Login gagal: {r.status_code}")
        return None
    
    print("DEBUG: Grafana login OK")
    return _session

def grafana_request(method, path, params=None):
    session = get_grafana_session()
    if not session:
        return None, "login_failed"
    
    url = f"{GRAFANA_URL.rstrip('/')}{path}"
    try:
        r = session.request(method, url, params=params, timeout=10)
        print(f"DEBUG grafana: {method} {url} status={r.status_code}")
        
        if r.status_code == 200:
            return r.json(), None
        return None, f"error_{r.status_code}"
    except Exception as e:
        return None, str(e)

def check_grafana_health():
    data, err = grafana_request("GET", "/api/health")
    return data.get("database") == "ok" if data else False, err or "ok"

def check_dashboard_folder(uid):
    data, err = grafana_request("GET", f"/api/folders/{uid}")
    return bool(data), err or "folder_ok"

def check_datasource(name):
    data, err = grafana_request("GET", "/api/datasources")
    if err: return False, err
    for ds in data:
        if ds.get("name") == name:
            return True, "datasource_ok"
    return False, "not_found"

def _normalize(s: str) -> str:
    # lower + buang semua karakter selain huruf dan angka
    return re.sub(r"[^a-z0-9]+", "", s.lower())

def check_grafana_alert_rule(rule_name: str):
    """Cek alert rule sudah ada dan namanya sesuai (abaikan spasi/tanda baca/case)."""
    data, err = grafana_request("GET", "/api/v1/provisioning/alert-rules")
    if err:
        return False, err

    target = _normalize(rule_name)
    for rule in data:
        title_raw = rule.get("title") or rule.get("name") or ""
        title_norm = _normalize(title_raw)
        if target == title_norm:
            return True, f"alert_rule_exists: {title_raw}"
    return False, "alert_rule_not_found"

def check_grafana_alert_firing(rule_name: str):
    """
    Cek apakah alert rule tertentu sedang dalam status firing/aktif.
    Di Grafana Alertmanager v2, UI 'Firing' biasanya muncul sebagai state 'active'.
    """
    data, err = grafana_request("GET", "/api/alertmanager/grafana/api/v2/alerts")
    if err:
        return False, err

    target = _normalize(rule_name)
    found = False
    found_status = None

    for alert in data:
        status = (alert.get("status") or {}).get("state") or (alert.get("status") or {}).get("status")
        labels = alert.get("labels") or {}
        annotations = alert.get("annotations") or {}

        # JSON kamu: nama rule ada di labels.alertname dan summary
        candidates = [
            labels.get("alertname", ""),
            annotations.get("summary", ""),
            annotations.get("description", ""),
        ]

        for c in candidates:
            if not c:
                continue
            if target == _normalize(c):     # pakai == biar jelas
                found = True
                found_status = status
                # Di API ini, state 'active' = alert sedang firing
                if status in ("active", "firing"):
                    return True, "alert_firing"

    if found:
        return False, f"alert_found_but_state_{found_status or 'unknown'}"
    return False, "alert_not_found"

def grafana_annotations(params=None):
    session = get_grafana_session()
    if not session:
        return None, "login_failed"

    url = f"{GRAFANA_URL.rstrip('/')}/api/annotations"
    try:
        r = session.get(url, params=params, timeout=10)
        print("DEBUG grafana annotations:", r.status_code, r.url)
        if r.status_code == 200:
            return r.json(), None
        return None, f"error_{r.status_code}"
    except Exception as e:
        return None, str(e)

def check_grafana_alert_history(rule_name: str, hours: int = 24):
    """
    Cek apakah alert dengan ruleTitle/alertname tertentu PERNAH berada di state Alerting
    dalam X jam terakhir (default 24 jam), pakai /api/v1/rules/history.
    """
    now_sec = int(time.time())
    from_sec = now_sec - hours * 3600

    data, err = grafana_request(
        "GET",
        "/api/v1/rules/history",
        params={
            "from": from_sec,
            "to": now_sec,
            "limit": 5000,
        },
    )
    if err:
        return False, err

    target = _normalize(rule_name)
    frame = data.get("data", {}).get("values") or []
    if len(frame) < 2:
        return False, "no_history_data"

    times = frame[0]
    lines = frame[1]

    for t, line in zip(times, lines):
        rt = line.get("ruleTitle", "")
        lbls = line.get("labels") or {}
        alertname = lbls.get("alertname", "")
        current = line.get("current", "")

        if target == _normalize(rt) or target == _normalize(alertname):
            if current == "Alerting":
                return True, "alert_fired_recently"

    return False, "alert_not_found_in_history"
