import os
import requests

GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET")
GMAIL_REFRESH_TOKEN = os.getenv("GMAIL_MONITOR_REFRESH_TOKEN")

TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"


def get_gmail_access_token():
    if not (GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET and GMAIL_REFRESH_TOKEN):
        raise RuntimeError("Missing GOOGLE_CLIENT_ID/SECRET or GMAIL_MONITOR_REFRESH_TOKEN")

    data = {
        "client_id": GOOGLE_CLIENT_ID,
        "client_secret": GOOGLE_CLIENT_SECRET,
        "refresh_token": GMAIL_REFRESH_TOKEN,
        "grant_type": "refresh_token",
    }
    resp = requests.post(TOKEN_ENDPOINT, data=data, timeout=10)
    if resp.status_code != 200:
        raise RuntimeError(f"Failed to refresh token: {resp.status_code} {resp.text}")
    tokens = resp.json()
    return tokens["access_token"]

def check_alert_email_sent_any():
    """
    Cek apakah ada email dari monitoringsija@gmail.com
    (tanpa batas waktu, pakai pencarian biasa di Gmail).
    """
    access_token = get_gmail_access_token()

    # Tanpa newer_than → semua email yang match query
    q = "from:monitoringsija@gmail.com"

    url = "https://gmail.googleapis.com/gmail/v1/users/me/messages"
    params = {"q": q, "maxResults": 1}
    headers = {"Authorization": f"Bearer {access_token}"}

    resp = requests.get(url, headers=headers, params=params, timeout=10)
    if resp.status_code != 200:
        return False, f"gmail_list_failed_{resp.status_code}:{resp.text}"

    data = resp.json()
    messages = data.get("messages", [])
    if not messages:
        return False, "email_alert_not_found"

    return True, "email_alert_found"
