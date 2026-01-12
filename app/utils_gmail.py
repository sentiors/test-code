import os
import requests
import base64
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"

def get_gmail_access_token():
    # ✅ BAJA KAN ENV SETIAP PANGGIL (real-time)
    GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID")
    GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET")
    GMAIL_REFRESH_TOKEN = os.getenv("GMAIL_MONITOR_REFRESH_TOKEN")
    
    if not all([GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, GMAIL_REFRESH_TOKEN]):
        raise RuntimeError("Missing GOOGLE_CLIENT_ID/SECRET or GMAIL_MONITOR_REFRESH_TOKEN")

    data = {
        "client_id": GOOGLE_CLIENT_ID,
        "client_secret": GOOGLE_CLIENT_SECRET,
        "refresh_token": GMAIL_REFRESH_TOKEN,
        "grant_type": "refresh_token"
    }
    
    resp = requests.post(TOKEN_ENDPOINT, data=data, timeout=10)
    if resp.status_code != 200:
        raise RuntimeError(f"Failed to refresh: {resp.status_code} {resp.text}")
    
    return resp.json()["access_token"]

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

def send_otp_gmail(receiver_email, otp_code):
    try:
        access_token = get_gmail_access_token()
        url = "https://gmail.googleapis.com/gmail/v1/users/me/messages/send"

        # 1. Subject yang lebih formal
        subject = "Reset Your Password for GradingCTL"

        # 2. Body dalam format HTML agar terlihat profesional
        html_body = f"""
        <html>
        <body style="font-family: Arial, sans-serif; line-height: 1.6; color: #333;">
            <div style="max-width: 600px; margin: 0 auto; border: 1px solid #eee; padding: 20px; border-radius: 10px;">
                <h2 style="color: #000; text-align: center;">GradingCTL</h2>
                <hr style="border: 0; border-top: 1px solid #eee;">
                <p>Hello,</p>
                <p>We received a request to reset your account password. Please use the following One-Time Password (OTP) to proceed:</p>
                <div style="background: #f4f4f4; padding: 15px; text-align: center; font-size: 24px; font-weight: bold; letter-spacing: 5px; border-radius: 8px; margin: 20px 0; color: #000;">
                    {otp_code}
                </div>
                <p>This code is valid for <strong>20 minutes</strong>. If you did not request this change, please ignore this email or contact support if you have concerns.</p>
                <br>
                <p style="font-size: 12px; color: #888;">
                    This is an automated message, please do not reply.<br>
                    &copy; 2025 GradingCTL - Automatic Grading System.
                </p>
            </div>
        </body>
        </html>
        """

        # Menggunakan MIMEMultipart untuk mendukung HTML
        message = MIMEMultipart()
        message['to'] = receiver_email
        message['subject'] = subject
        message.attach(MIMEText(html_body, 'html'))

        raw_message = base64.urlsafe_b64encode(message.as_bytes()).decode()

        resp = requests.post(
            url,
            headers={"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"},
            json={"raw": raw_message},
            timeout=10
        )
        return resp.status_code == 200
    except Exception as e:
        print(f"Error sending email: {e}")
        return False
