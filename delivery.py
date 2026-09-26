"""Email delivery for preset purchases via Gmail API."""

import os
import base64
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders

PRESET_DIR = os.path.join(os.path.dirname(__file__), "preset-packs")


def send_preset_email(to_email, product_name, filename):
    """Send the preset ZIP to the customer via Gmail."""
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build

    # Uses the same Gmail connection as the main agent
    # Credentials are loaded from the environment / secure store
    creds_data = os.environ.get("GMAIL_CREDENTIALS_JSON")
    if not creds_data:
        raise RuntimeError("Gmail credentials not configured")

    import json
    creds = Credentials.from_authorized_user_info(json.loads(creds_data))
    service = build("gmail", "v1", credentials=creds)

    msg = MIMEMultipart()
    msg["To"] = to_email
    msg["From"] = "booking@sutejpannu.com"
    msg["Subject"] = f"Your {product_name} is here! 🎁"

    body = f"""Hi there,

Thank you for your purchase!

Your {product_name} is attached to this email. Please download it and keep a backup somewhere safe so you always have it.

Enjoy the presets, and thank you for your support!

Warmly,
Sutej
"""
    msg.attach(MIMEText(body, "plain"))

    filepath = os.path.join(PRESET_DIR, filename)
    if os.path.exists(filepath):
        with open(filepath, "rb") as f:
            part = MIMEBase("application", "zip")
            part.set_payload(f.read())
        encoders.encode_base64(part)
        part.add_header("Content-Disposition", f"attachment; filename={filename}")
        msg.attach(part)

    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    service.users().messages().send(userId="me", body={"raw": raw}).execute()
