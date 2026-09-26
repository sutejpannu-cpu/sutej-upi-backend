"""Email delivery for purchased presets via SMTP.


Sends a secure, time-limited download link instead of attaching the ZIP
(Gmail blocks attachments over ~25MB; packs are 23-84MB).
"""
import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText


SMTP_HOST = os.environ.get("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USER = os.environ.get("SMTP_USER", "")
SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD", "")



def send_preset_email(to_email, product_name, download_url):
    """Email the customer their preset download link. Returns True on success.


    NOTE: the exact body text must be approved by Sutej before enabling
    automatic sending. Do not change the wording without his approval.
    """
    if not SMTP_USER or not SMTP_PASSWORD:
        raise RuntimeError("SMTP credentials not configured")


    msg = MIMEMultipart()
    msg["From"] = f"Sutej Pannu <{SMTP_USER}>"
    msg["To"] = to_email
    msg["Subject"] = f"Your {product_name} is here! 🎁"


    # --- APPROVED TEMPLATE (do not edit without Sutej's approval) ---
    body = f"""Hi there,


Thank you so much for your purchase! Your {product_name} is ready to download.


DOWNLOAD YOUR PRESET:
{download_url}


This link is valid for 7 days and is just for you — please don't share it.


HOW TO INSTALL:
1. Tap the link above to download the ZIP file
2. Unzip it on your computer or phone
3. Open Lightroom and import the presets
4. Start creating!


Enjoy!
- Sutej Pannu
https://sutejpannu.com
"""
    # --- END APPROVED TEMPLATE ---


    msg.attach(MIMEText(body, "plain"))


    # timeout: fail fast (and let the webhook 500 + retry) instead of hanging
    # forever and getting the worker killed, which silently drops delivery.
    with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=15) as server:
        server.starttls()
        server.login(SMTP_USER, SMTP_PASSWORD)
        server.send_message(msg)


    return True
