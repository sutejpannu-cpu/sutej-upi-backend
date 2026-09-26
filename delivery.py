"""Email delivery for purchased presets via SMTP (Gmail App Password)."""
import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.application import MIMEApplication

SMTP_HOST = os.environ.get("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USER = os.environ.get("SMTP_USER", "")
SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD", "")

PRESET_DIR = os.path.join(os.path.dirname(__file__), "preset-packs")


def send_preset_email(to_email, product_name, filename):
    """Send the preset ZIP to the customer via SMTP. Returns True on success."""
    if not SMTP_USER or not SMTP_PASSWORD:
        raise RuntimeError("SMTP credentials not configured")

    msg = MIMEMultipart()
    msg["From"] = f"Sutej Pannu <{SMTP_USER}>"
    msg["To"] = to_email
    msg["Subject"] = f"Your {product_name} is here! 🎁"

    body = f"""Hi there,

Thank you for your purchase! Your {product_name} is attached.

HOW TO INSTALL:
1. Download the ZIP file attached
2. Unzip it on your computer
3. Open Lightroom and import the presets

Need help? Just reply to this email.

Enjoy creating!
- Sutej Pannu
https://sutejpannu.com
"""
    msg.attach(MIMEText(body, "plain"))

    filepath = os.path.join(PRESET_DIR, filename)
    if not os.path.exists(filepath):
        raise RuntimeError(f"Preset file not found: {filename}")

    with open(filepath, "rb") as f:
        part = MIMEApplication(f.read(), Name=filename)
    part["Content-Disposition"] = f'attachment; filename="{filename}"'
    msg.attach(part)

    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
        server.starttls()
        server.login(SMTP_USER, SMTP_PASSWORD)
        server.send_message(msg)

    return True
