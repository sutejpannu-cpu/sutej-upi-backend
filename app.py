"""
UPI Payment Backend for Sutej Pannu Presets
Generates fresh Razorpay payment links and auto-delivers presets via email.

Endpoints:
  POST /generate-link  {product_id, email} -> {payment_link}
  POST /webhook        Razorpay payment_link.paid events -> sends preset email
  GET  /download/<token>  time-limited secure preset download
  GET  /health
"""

import os
import hmac
import hashlib
import json
import base64
import time
from flask import Flask, request, jsonify, send_file
import requests
from requests.auth import HTTPBasicAuth"""
UPI Payment Backend for Sutej Pannu Presets
Generates fresh Razorpay payment links and auto-delivers presets via email.

Endpoints:
  POST /generate-link  {product_id, email} -> {payment_link}
  POST /webhook        Razorpay payment_link.paid events -> sends preset email
  GET  /download/<token>  time-limited secure preset download
  GET  /health
"""

import os
import hmac
import hashlib
import json
import base64
import time
from flask import Flask, request, jsonify, send_file
import requests
from requests.auth import HTTPBasicAuth

app = Flask(__name__)

# CORS: allow the Showit/Shopify storefront to call the API from the browser
@app.after_request
def _cors(response):
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
    return response

@app.route("/generate-link", methods=["OPTIONS"])
@app.route("/webhook", methods=["OPTIONS"])
def _cors_preflight():
    return ("", 204)

# Config from environment
RAZORPAY_KEY_ID = os.environ.get("RAZORPAY_KEY_ID", "")
RAZORPAY_KEY_SECRET = os.environ.get("RAZORPAY_KEY_SECRET", "")
RAZORPAY_WEBHOOK_SECRET = os.environ.get("RAZORPAY_WEBHOOK_SECRET", "")

# In-memory set of processed Razorpay payment-link IDs (idempotency).
# NOTE: resets on restart; use Redis/DB for multi-instance production.
_processed_payments = set()

# Product ID -> {name, amount_inr, file}
# Amounts in INR (must match Shopify India market prices)
PRODUCTS = {
    "7836454453467": {"name": "Earthy Tone Lightroom Preset", "amount": 1999, "file": "earthy-tone-presets.zip"},
    "7836516286683": {"name": "Soulful Lightroom Preset", "amount": 1999, "file": "soul-preset-pack.zip"},
    "7846204440795": {"name": "Filmic Lightroom Preset", "amount": 1999, "file": "filmic-preset-pack.zip"},
    "8058726056155": {"name": "Stoic Pack Preset", "amount": 3999, "file": "stoic-tone.zip"},
    "8058726351067": {"name": "Regal Tones Preset", "amount": 3999, "file": "regal-tones.zip"},
    "8081203396827": {"name": "Awaken Preset Pack", "amount": 2999, "file": "awaken-preset-pack.zip"},
    "8081981735131": {"name": "Portrait Pack Bundle", "amount": 2199, "file": "portrait-pack-bundle.zip"},
    "8889942376667": {"name": "Nostalgic Tones Lightroom Preset", "amount": 5999, "file": "nostalgic-tones-preset.zip"},
    "8889950961883": {"name": "Mystic Tones Lightroom Preset", "amount": 5999, "file": "mystic-tones-preset.zip"},
    "8081981702363": {"name": "Weddings Pack Bundle", "amount": 5999, "file": "weddings-pack-bundle.zip"},
    "8889955188955": {"name": "Weddings Pack Bundle Updated", "amount": 11999, "file": "weddings-pack-bundle-updated.zip"},
    "8890093863131": {"name": "Bundle & Save", "amount": 9999, "file": "bundle-and-save.zip"},








# Coupon codes: code -> discount percentage
# In production, these could come from environment variables or a database
# THANKYOU2026: keep until its Shopify expiry (Sept 27, 2026 ~9:30 AM PDT), then remove
# FALL10: fall promo, valid for the duration of the Sept 24 - Oct 4, 2026 ad run
}

PRESET_DIR = os.path.join(os.path.dirname(__file__), "preset-packs")



COUPONS = {
    "THANKYOU2026": 20, "FALL10": 10,
}
# One-time discount codes: code -> percent off. Each code works once.
# Consumed when a payment link is generated (in-memory; resets on redeploy).
# FILTERED2499: 37.5% off = Rs 2499 on the Stoic Pack Rs 3999 (issued Sept 26, 2026)
ONE_TIME_COUPONS = {
    "FILTERED2499": 37.5,
}
_used_one_time_coupons = set()





@app.route("/generate-link", methods=["POST"])
def generate_link():
    """Generate a fresh Razorpay payment link for a preset purchase."""
    data = request.get_json(force=True, silent=True) or {}






    product_id = str(data.get("product_id", ""))
    email = data.get("email", "").strip()
    coupon = str(data.get("coupon", "")).strip().upper()

    if product_id not in PRODUCTS:
        return jsonify({"error": "Unknown product"}), 400
    if not email or "@" not in email:
        return jsonify({"error": "Valid email required"}), 400
    if not RAZORPAY_KEY_ID or not RAZORPAY_KEY_SECRET:
        return jsonify({"error": "Payment service not configured"}), 500

    product = PRODUCTS[product_id]
    amount_inr = product["amount"]

    # Apply coupon discount if valid
    discount_pct = 0
    one_time_deal = False
    if coupon:
        if coupon in ONE_TIME_COUPONS:
            if coupon in _used_one_time_coupons:
                return jsonify({"error": "This code has already been used"}), 400
            discount_pct = ONE_TIME_COUPONS[coupon]
            amount_inr = int(round(amount_inr * (100 - discount_pct) / 100))
            one_time_deal = True
            _used_one_time_coupons.add(coupon)
        elif coupon in COUPONS:
            discount_pct = COUPONS[coupon]
            amount_inr = int(round(amount_inr * (100 - discount_pct) / 100))
        else:
            return jsonify({"error": "Invalid coupon code"}), 400

    amount_paise = amount_inr * 100

    description = product["name"] + " - Sutej Pannu"
    if one_time_deal:
        description += f" (one-time price with {coupon})"
    elif discount_pct:
        description += f" ({discount_pct}% off with {coupon})"

    payload = {
        "amount": amount_paise,
        "currency": "INR",
        "accept_partial": False,
        "description": description,
        "customer": {"email": email},
        "notify": {"sms": True, "email": True},
        "reminder_enable": True,
        "notes": {
            "product_id": product_id,
            "product_name": product["name"],
            "coupon": coupon if (discount_pct or one_time_deal) else "",
            "discount_pct": str(discount_pct),
        },
        "callback_url": "https://sutejpannu.com/shop",
        "callback_method": "get",
    }

    try:
        resp = requests.post(
            "https://api.razorpay.com/v1/payment_links",
            auth=HTTPBasicAuth(RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET),
            json=payload,
            timeout=30,
        )
        resp.raise_for_status()
        link_data = resp.json()
        short_url = link_data.get("short_url")
        if not short_url:
            return jsonify({"error": "No payment link returned"}), 500
        return jsonify({"payment_link": short_url})
    except requests.RequestException as e:
        app.logger.error("Razorpay API error: %s", e)
        return jsonify({"error": "Could not generate payment link"}), 500


@app.route("/webhook", methods=["POST"])
def webhook():
    """Handle Razorpay payment_link.paid events and deliver the preset."""
    # Verify webhook signature
    if RAZORPAY_WEBHOOK_SECRET:
        signature = request.headers.get("X-Razorpay-Signature", "")
        body = request.get_data()
        expected = hmac.new(
            RAZORPAY_WEBHOOK_SECRET.encode(), body, hashlib.sha256
        ).hexdigest()
        if not hmac.compare_digest(signature, expected):
            return jsonify({"error": "Invalid signature"}), 401

    event = request.get_json(force=True, silent=True) or {}
    if event.get("event") != "payment_link.paid":
        return jsonify({"status": "ignored"}), 200

    try:
        pl = event["payload"]["payment_link"]["entity"]
        notes = pl.get("notes", {})
        product_id = str(notes.get("product_id", ""))
        customer_email = pl.get("customer", {}).get("email", "")
        payment_id = pl.get("id", "")

        # Idempotency: skip if we've already processed this payment link
        if payment_id in _processed_payments:
            app.logger.info("Duplicate webhook ignored for %s", payment_id)
            return jsonify({"status": "duplicate_ignored"}), 200

        if product_id not in PRODUCTS or not customer_email:
            app.logger.error("Webhook missing product/email: %s", notes)
            return jsonify({"status": "missing_data"}), 200

        product = PRODUCTS[product_id]
        # Build a time-limited download link and email it
        from delivery import send_preset_email
        download_url = build_download_url(customer_email, product_id)
        send_preset_email(customer_email, product["name"], download_url)
        _processed_payments.add(payment_id)
        app.logger.info(
            "Delivered %s to %s (payment %s)",
            product["name"], customer_email, payment_id,
        )
        return jsonify({"status": "delivered"}), 200
    except Exception as e:
        app.logger.error("Webhook error: %s", e)
        return jsonify({"error": "Delivery failed"}), 500


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"})


# --- Secure download links ---
DOWNLOAD_LINK_TTL_SECONDS = 7 * 24 * 3600  # 7 days


def _b64url_encode(s: str) -> str:
    return base64.urlsafe_b64encode(s.encode()).decode().rstrip("=")


def _b64url_decode(s: str) -> str:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4)).decode()


def generate_download_token(email: str, product_id: str) -> str:
    expiry = int(time.time()) + DOWNLOAD_LINK_TTL_SECONDS
    payload = f"{email}.{product_id}.{expiry}"
    sig = hmac.new(
        RAZORPAY_KEY_SECRET.encode(), payload.encode(), hashlib.sha256
    ).hexdigest()
    return f"{_b64url_encode(email)}.{product_id}.{expiry}.{sig}"


def verify_download_token(token: str):
    """Return (email, product_id) if valid and not expired, else None."""
    try:
        email_b64, product_id, expiry_s, sig = token.split(".")
        email = _b64url_decode(email_b64)
        expiry = int(expiry_s)
    except Exception:
        return None
    if expiry < int(time.time()):
        return None
    payload = f"{email}.{product_id}.{expiry}"
    expected = hmac.new(
        RAZORPAY_KEY_SECRET.encode(), payload.encode(), hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(sig, expected):
        return None
    if product_id not in PRODUCTS:
        return None
    return email, product_id


@app.route("/download/<token>", methods=["GET"])
def download_preset(token):
    """Serve a preset ZIP via a time-limited signed token."""
    result = verify_download_token(token)
    if not result:
        return jsonify({"error": "Invalid or expired download link"}), 403
    email, product_id = result
    product = PRODUCTS[product_id]
    filepath = os.path.join(PRESET_DIR, product["file"])
    if not os.path.exists(filepath):
        app.logger.error("Preset file missing for download: %s", product["file"])
        return jsonify({"error": "File not available"}), 404
    app.logger.info("Download served: %s -> %s", product["file"], email)
    return send_file(filepath, as_attachment=True, download_name=product["file"])


def build_download_url(email: str, product_id: str) -> str:
    base = os.environ.get("PUBLIC_BASE_URL", "https://sutej-upi-backend-production.up.railway.app")
    token = generate_download_token(email, product_id)
    return f"{base}/download/{token}"


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)


app = Flask(__name__)

# CORS: allow the Showit/Shopify storefront to call the API from the browser
@app.after_request
def _cors(response):
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
    return response

@app.route("/generate-link", methods=["OPTIONS"])
@app.route("/webhook", methods=["OPTIONS"])
def _cors_preflight():
    return ("", 204)

# Config from environment
RAZORPAY_KEY_ID = os.environ.get("RAZORPAY_KEY_ID", "")
RAZORPAY_KEY_SECRET = os.environ.get("RAZORPAY_KEY_SECRET", "")
RAZORPAY_WEBHOOK_SECRET = os.environ.get("RAZORPAY_WEBHOOK_SECRET", "")

# In-memory set of processed Razorpay payment-link IDs (idempotency).
# NOTE: resets on restart; use Redis/DB for multi-instance production.
_processed_payments = set()

# Product ID -> {name, amount_inr, file}
# Amounts in INR (must match Shopify India market prices)
PRODUCTS = {
    "7836454453467": {"name": "Earthy Tone Lightroom Preset", "amount": 1999, "file": "earthy-tone-presets.zip"},
    "7836516286683": {"name": "Soulful Lightroom Preset", "amount": 1999, "file": "soul-preset-pack.zip"},
    "7846204440795": {"name": "Filmic Lightroom Preset", "amount": 1999, "file": "filmic-preset-pack.zip"},
    "8058726056155": {"name": "Stoic Pack Preset", "amount": 3999, "file": "stoic-tone.zip"},
    "8058726351067": {"name": "Regal Tones Preset", "amount": 3999, "file": "regal-tones.zip"},
    "8081203396827": {"name": "Awaken Preset Pack", "amount": 2999, "file": "awaken-preset-pack.zip"},
    "8081981735131": {"name": "Portrait Pack Bundle", "amount": 2199, "file": "portrait-pack-bundle.zip"},
    "8889942376667": {"name": "Nostalgic Tones Lightroom Preset", "amount": 5999, "file": "nostalgic-tones-preset.zip"},
    "8889950961883": {"name": "Mystic Tones Lightroom Preset", "amount": 5999, "file": "mystic-tones-preset.zip"},
    "8081981702363": {"name": "Weddings Pack Bundle", "amount": 5999, "file": "weddings-pack-bundle.zip"},
    "8889955188955": {"name": "Weddings Pack Bundle Updated", "amount": 11999, "file": "weddings-pack-bundle-updated.zip"},
    "8890093863131": {"name": "Bundle & Save", "amount": 9999, "file": "bundle-and-save.zip"},








# Coupon codes: code -> discount percentage
# In production, these could come from environment variables or a database
# THANKYOU2026: keep until its Shopify expiry (Sept 27, 2026 ~9:30 AM PDT), then remove
# FALL10: fall promo, valid for the duration of the Sept 24 - Oct 4, 2026 ad run
}

PRESET_DIR = os.path.join(os.path.dirname(__file__), "preset-packs")



COUPONS = {
    "THANKYOU2026": 20, "FALL10": 10,
}





@app.route("/generate-link", methods=["POST"])
def generate_link():
    """Generate a fresh Razorpay payment link for a preset purchase."""
    data = request.get_json(force=True, silent=True) or {}






    product_id = str(data.get("product_id", ""))
    email = data.get("email", "").strip()
    coupon = str(data.get("coupon", "")).strip().upper()

    if product_id not in PRODUCTS:
        return jsonify({"error": "Unknown product"}), 400
    if not email or "@" not in email:
        return jsonify({"error": "Valid email required"}), 400
    if not RAZORPAY_KEY_ID or not RAZORPAY_KEY_SECRET:
        return jsonify({"error": "Payment service not configured"}), 500

    product = PRODUCTS[product_id]
    amount_inr = product["amount"]

    # Apply coupon discount if valid
    discount_pct = 0
    if coupon:
        if coupon in COUPONS:
            discount_pct = COUPONS[coupon]
            amount_inr = int(round(amount_inr * (100 - discount_pct) / 100))
        else:
            return jsonify({"error": "Invalid coupon code"}), 400

    amount_paise = amount_inr * 100

    description = product["name"] + " - Sutej Pannu"
    if discount_pct:
        description += f" ({discount_pct}% off with {coupon})"

    payload = {
        "amount": amount_paise,
        "currency": "INR",
        "accept_partial": False,
        "description": description,
        "customer": {"email": email},
        "notify": {"sms": True, "email": True},
        "reminder_enable": True,
        "notes": {
            "product_id": product_id,
            "product_name": product["name"],
            "coupon": coupon if discount_pct else "",
            "discount_pct": str(discount_pct),
        },
        "callback_url": "https://sutejpannu.com/shop",
        "callback_method": "get",
    }

    try:
        resp = requests.post(
            "https://api.razorpay.com/v1/payment_links",
            auth=HTTPBasicAuth(RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET),
            json=payload,
            timeout=30,
        )
        resp.raise_for_status()
        link_data = resp.json()
        short_url = link_data.get("short_url")
        if not short_url:
            return jsonify({"error": "No payment link returned"}), 500
        return jsonify({"payment_link": short_url})
    except requests.RequestException as e:
        app.logger.error("Razorpay API error: %s", e)
        return jsonify({"error": "Could not generate payment link"}), 500


@app.route("/webhook", methods=["POST"])
def webhook():
    """Handle Razorpay payment_link.paid events and deliver the preset."""
    # Verify webhook signature
    if RAZORPAY_WEBHOOK_SECRET:
        signature = request.headers.get("X-Razorpay-Signature", "")
        body = request.get_data()
        expected = hmac.new(
            RAZORPAY_WEBHOOK_SECRET.encode(), body, hashlib.sha256
        ).hexdigest()
        if not hmac.compare_digest(signature, expected):
            return jsonify({"error": "Invalid signature"}), 401

    event = request.get_json(force=True, silent=True) or {}
    if event.get("event") != "payment_link.paid":
        return jsonify({"status": "ignored"}), 200

    try:
        pl = event["payload"]["payment_link"]["entity"]
        notes = pl.get("notes", {})
        product_id = str(notes.get("product_id", ""))
        customer_email = pl.get("customer", {}).get("email", "")
        payment_id = pl.get("id", "")

        # Idempotency: skip if we've already processed this payment link
        if payment_id in _processed_payments:
            app.logger.info("Duplicate webhook ignored for %s", payment_id)
            return jsonify({"status": "duplicate_ignored"}), 200

        if product_id not in PRODUCTS or not customer_email:
            app.logger.error("Webhook missing product/email: %s", notes)
            return jsonify({"status": "missing_data"}), 200

        product = PRODUCTS[product_id]
        # Build a time-limited download link and email it
        from delivery import send_preset_email
        download_url = build_download_url(customer_email, product_id)
        send_preset_email(customer_email, product["name"], download_url)
        _processed_payments.add(payment_id)
        app.logger.info(
            "Delivered %s to %s (payment %s)",
            product["name"], customer_email, payment_id,
        )
        return jsonify({"status": "delivered"}), 200
    except Exception as e:
        app.logger.error("Webhook error: %s", e)
        return jsonify({"error": "Delivery failed"}), 500


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"})


# --- Secure download links ---
DOWNLOAD_LINK_TTL_SECONDS = 7 * 24 * 3600  # 7 days


def _b64url_encode(s: str) -> str:
    return base64.urlsafe_b64encode(s.encode()).decode().rstrip("=")


def _b64url_decode(s: str) -> str:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4)).decode()


def generate_download_token(email: str, product_id: str) -> str:
    expiry = int(time.time()) + DOWNLOAD_LINK_TTL_SECONDS
    payload = f"{email}.{product_id}.{expiry}"
    sig = hmac.new(
        RAZORPAY_KEY_SECRET.encode(), payload.encode(), hashlib.sha256
    ).hexdigest()
    return f"{_b64url_encode(email)}.{product_id}.{expiry}.{sig}"


def verify_download_token(token: str):
    """Return (email, product_id) if valid and not expired, else None."""
    try:
        email_b64, product_id, expiry_s, sig = token.split(".")
        email = _b64url_decode(email_b64)
        expiry = int(expiry_s)
    except Exception:
        return None
    if expiry < int(time.time()):
        return None
    payload = f"{email}.{product_id}.{expiry}"
    expected = hmac.new(
        RAZORPAY_KEY_SECRET.encode(), payload.encode(), hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(sig, expected):
        return None
    if product_id not in PRODUCTS:
        return None
    return email, product_id


@app.route("/download/<token>", methods=["GET"])
def download_preset(token):
    """Serve a preset ZIP via a time-limited signed token."""
    result = verify_download_token(token)
    if not result:
        return jsonify({"error": "Invalid or expired download link"}), 403
    email, product_id = result
    product = PRODUCTS[product_id]
    filepath = os.path.join(PRESET_DIR, product["file"])
    if not os.path.exists(filepath):
        app.logger.error("Preset file missing for download: %s", product["file"])
        return jsonify({"error": "File not available"}), 404
    app.logger.info("Download served: %s -> %s", product["file"], email)
    return send_file(filepath, as_attachment=True, download_name=product["file"])


def build_download_url(email: str, product_id: str) -> str:
    base = os.environ.get("PUBLIC_BASE_URL", "https://sutej-upi-backend-production.up.railway.app")
    token = generate_download_token(email, product_id)
    return f"{base}/download/{token}"


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
