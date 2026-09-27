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
from datetime import datetime
from zoneinfo import ZoneInfo
from flask import Flask, request, jsonify, send_file
import requests
from requests.auth import HTTPBasicAuth
from datetime import datetime
from zoneinfo import ZoneInfo

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

# Set of processed Razorpay payment-link IDs (idempotency, in-memory).
_processed_payments = set()

# Persistent record of delivered payment-link IDs. Survives restarts within a
# container and ships with the repo (delivered.json). The /sweep job shares
# this with the webhook handler so a paid link is delivered exactly once, no
# matter which path gets there first.
_DELIVERED_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "delivered.json"
)


def _load_delivered():
    try:
        with open(_DELIVERED_FILE) as f:
            data = json.load(f)
            if isinstance(data, list):
                return set(data)
    except (OSError, ValueError):
        pass
    return set()


def _save_delivered(ids):
    try:
        with open(_DELIVERED_FILE, "w") as f:
            json.dump(sorted(ids), f)
    except OSError as e:
        app.logger.error("Could not save delivered.json: %s", e)


def _already_delivered(link_id):
    return link_id in _processed_payments or link_id in _load_delivered()


def _mark_delivered(link_id):
    _processed_payments.add(link_id)
    ids = _load_delivered()
    ids.add(link_id)
    _save_delivered(ids)

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
    "8081981702363": {"name": "Weddings Pack Bundle Updated", "amount": 11999, "file": "weddings-pack-bundle-updated.zip"},
    "8889955188955": {"name": "Weddings Pack Bundle", "amount": 5999, "file": "weddings-pack-bundle.zip"},
    "8890093863131": {"name": "Bundle & Save Updated", "amount": 14999, "file": "bundle-and-save-updated.zip"},
}








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
    phone_raw = str(data.get("phone", "") or "")

    if product_id not in PRODUCTS:
        return jsonify({"error": "Unknown product"}), 400
    if not email or "@" not in email:
        return jsonify({"error": "Valid email required"}), 400

    # Optional UPI contact number: normalize Indian 10-digit mobiles to +91.
    # Invalid input is ignored (never blocks the purchase).
    phone_digits = "".join(c for c in phone_raw if c.isdigit())
    phone = ""
    if len(phone_digits) == 10 and phone_digits[0] in "6789":
        phone = "+91" + phone_digits
    elif len(phone_digits) == 12 and phone_digits.startswith("91"):
        phone = "+" + phone_digits
    elif phone_raw.strip().startswith("+") and 7 <= len(phone_digits) <= 15:
        phone = "+" + phone_digits
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

    customer = {"email": email}
    if phone:
        customer["contact"] = phone

    payload = {
        "amount": amount_paise,
        "currency": "INR",
        "accept_partial": False,
        "description": description,
        "customer": customer,
        "notify": {"sms": True, "email": True},
        "reminder_enable": True,
        "notes": {
            "product_id": product_id,
            "product_name": product["name"],
            "coupon": coupon if (discount_pct or one_time_deal) else "",
            "discount_pct": str(discount_pct),
            "phone": phone,
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
        if _already_delivered(payment_id):
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
        _mark_delivered(payment_id)
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


# --- Tracker payments proxy ---
# Lets the Clicker App tracker read today's successful UPI payments without
# opening the Razorpay dashboard in a browser (which triggers a per-task
# approval prompt on Sutej's phone every 15 minutes). Read-only: proxies the
# Razorpay Payments + Payment Links APIs using the server's own API keys.
# Guarded by the TRACKER_KEY env var (shared secret with the tracker).
TRACKER_KEY = os.environ.get("TRACKER_KEY", "")
TRACKER_TZ = ZoneInfo("America/Los_Angeles")


@app.route("/tracker/payments", methods=["GET"])
def tracker_payments():
    key = request.args.get("key", "")
    if not TRACKER_KEY or not hmac.compare_digest(key, TRACKER_KEY):
        return jsonify({"error": "unauthorized"}), 401
    if not RAZORPAY_KEY_ID or not RAZORPAY_KEY_SECRET:
        return jsonify({"error": "payment service not configured"}), 502

    auth = HTTPBasicAuth(RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET)
    now = datetime.now(TRACKER_TZ)
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    day_start_ts = int(day_start.timestamp())
    now_ts = int(now.timestamp())
    week_ago_ts = day_start_ts - 6 * 86400  # catch links created earlier, paid today

    def _api_get(url, params):
        try:
            r = requests.get(url, auth=auth, params=params, timeout=30)
            r.raise_for_status()
            return r.json()
        except requests.RequestException as e:
            app.logger.error("Razorpay API failed %s: %s", url, e)
            return None

    # Map payment_id -> preset name + payer contact via recent payment links
    # (built by /generate-link). The contact is what the buyer typed on
    # Razorpay's payment page, right after our email box.
    pay_to_item = {}
    pay_to_contact = {}
    links_data = _api_get(
        "https://api.razorpay.com/v1/payment_links",
        {"from": week_ago_ts, "to": now_ts, "count": 100},
    )
    if links_data:
        for link in links_data.get("items", []):
            pay_ids = link.get("payments") or []
            detail = None
            if not pay_ids and link.get("status") == "paid" and link.get("id"):
                detail = _api_get(
                    "https://api.razorpay.com/v1/payment_links/" + link["id"], {}
                )
                pay_ids = (detail or {}).get("payments") or []
            notes = link.get("notes", {}) or {}
            name = notes.get("product_name") or link.get("description") or "Preset"
            cust = ((detail or {}).get("customer") or link.get("customer") or {}) or {}
            contact = cust.get("contact", "")
            for pid in pay_ids:
                pay_to_item[pid] = name
                if contact:
                    pay_to_contact[pid] = contact

    # Today's captured payments (mirrors the dashboard payments view).
    pays_data = _api_get(
        "https://api.razorpay.com/v1/payments",
        {"from": day_start_ts, "to": now_ts, "count": 100},
    )
    if pays_data is None:
        return jsonify({"error": "razorpay api unavailable"}), 502

    payments = []
    for p in pays_data.get("items", []):
        if p.get("status") != "captured":
            continue
        pid = p.get("id", "")
        created = int(p.get("created_at", 0))
        dt = datetime.fromtimestamp(created, TRACKER_TZ)
        payments.append({
            "time": dt.strftime("%H:%M"),
            "payment_id": pid,
            "item": pay_to_item.get(pid, "UPI payment"),
            "contact": pay_to_contact.get(pid, ""),
            "amount": (p.get("amount") or 0) // 100,
            "status": p.get("status", ""),
            "created_at": created,
        })

    payments.sort(key=lambda x: x["created_at"])
    return jsonify({
        "date": day_start.strftime("%Y-%m-%d"),
        "count": len(payments),
        "total_inr": sum(x["amount"] for x in payments),
        "payments": payments,
    })


# --- Payment reconciliation sweeper ---
# Safety net for the webhook: every few minutes this checks Razorpay for
# payment links that were PAID but never delivered (webhook never fired,
# failed, or the server was down). Any missed preset gets its download email
# here, so no paid order can silently go undelivered.
# Called by a scheduled job; guarded by the same TRACKER_KEY secret.
@app.route("/sweep", methods=["GET"])
def sweep():
    key = request.args.get("key", "")
    if not TRACKER_KEY or not hmac.compare_digest(key, TRACKER_KEY):
        return jsonify({"error": "unauthorized"}), 401
    if not RAZORPAY_KEY_ID or not RAZORPAY_KEY_SECRET:
        return jsonify({"error": "payment service not configured"}), 502

    auth = HTTPBasicAuth(RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET)
    now_ts = int(time.time())
    from_ts = now_ts - 48 * 3600  # only recent links; older ones were handled

    try:
        resp = requests.get(
            "https://api.razorpay.com/v1/payment_links",
            auth=auth,
            params={"from": from_ts, "to": now_ts, "count": 100},
            timeout=30,
        )
        resp.raise_for_status()
        links = resp.json().get("items", [])
    except requests.RequestException as e:
        app.logger.error("Sweep: Razorpay API error: %s", e)
        return jsonify({"error": "razorpay unavailable"}), 502

    from delivery import send_preset_email
    delivered, already_done = [], 0
    for link in links:
        if link.get("status") != "paid":
            continue
        link_id = link.get("id", "")
        if not link_id or _already_delivered(link_id):
            already_done += 1
            continue
        # The buyer types their contact on Razorpay's payment page (the step
        # right after our email box). Pull the full link detail so we capture
        # that contact alongside the email for tracking/reconciliation.
        customer = link.get("customer") or {}
        pay_ids = link.get("payments") or []
        try:
            d = requests.get(
                "https://api.razorpay.com/v1/payment_links/" + link_id,
                auth=auth, timeout=30,
            )
            detail = d.json() if d.ok else {}
        except requests.RequestException:
            detail = {}
        d_customer = detail.get("customer") or {}
        if d_customer.get("email"):
            customer = d_customer
        pay_ids = detail.get("payments") or pay_ids
        contact = d_customer.get("contact") or customer.get("contact") or ""
        # Last resort: the payment entity always carries the payer's contact.
        if not contact and pay_ids:
            try:
                p = requests.get(
                    "https://api.razorpay.com/v1/payments/" + pay_ids[0],
                    auth=auth, timeout=30,
                )
                if p.ok:
                    contact = p.json().get("contact") or ""
            except requests.RequestException:
                pass
        notes = link.get("notes") or {}
        product_id = str(notes.get("product_id", ""))
        customer_email = customer.get("email", "")
        if product_id not in PRODUCTS or not customer_email:
            app.logger.warning("Sweep: missing product/email on %s", link_id)
            continue  # not marked delivered; retried on the next sweep
        try:
            product = PRODUCTS[product_id]
            download_url = build_download_url(customer_email, product_id)
            send_preset_email(customer_email, product["name"], download_url)
            _mark_delivered(link_id)
            delivered.append({
                "link": link_id,
                "product": product["name"],
                "email": customer_email,
                "phone": contact or str(notes.get("phone", "")),
            })
            app.logger.info(
                "Sweep delivered %s to %s (%s)",
                product["name"], customer_email, link_id,
            )
        except Exception as e:
            app.logger.error("Sweep delivery failed for %s: %s", link_id, e)
    return jsonify({
        "status": "ok",
        "checked": len(links),
        "delivered": delivered,
        "already_done": already_done,
    })


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
    # Prefer R2: the Railway host has no preset ZIPs on local disk, so the
    # legacy /download/<token> links would 404 there. Fall back to the local
    # signed URL only for local dev (where preset-packs/ exists).
    r2_url = build_r2_presigned_url(product_id)
    if r2_url:
        return r2_url
    app.logger.warning(
        "R2 presigned URL unavailable for %s; falling back to local signed URL",
        product_id,
    )
    base = os.environ.get("PUBLIC_BASE_URL", "https://sutej-upi-backend-production.up.railway.app")
    token = generate_download_token(email, product_id)
    return f"{base}/download/{token}"


# --- R2 presigned downloads ---
# The private Cloudflare R2 bucket holds the preset ZIPs; the Railway server
# has none on local disk. Delivery links must therefore be time-limited
# presigned R2 URLs, not /download/<token> links.
# Required env vars: R2_ACCOUNT_ID, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY.
# Optional: R2_BUCKET (default "preset-packs"), R2_LINK_TTL_SECONDS
# (default 7 days, matching the delivery email's "valid for 7 days").
def _r2_client():
    account_id = os.environ.get("R2_ACCOUNT_ID", "")
    access_key = os.environ.get("R2_ACCESS_KEY_ID", "")
    secret_key = os.environ.get("R2_SECRET_ACCESS_KEY", "")
    if not (account_id and access_key and secret_key):
        return None
    import boto3
    return boto3.client(
        "s3",
        endpoint_url=f"https://{account_id}.r2.cloudflarestorage.com",
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        region_name="auto",
    )


def build_r2_presigned_url(product_id):
    """Return a presigned R2 GET URL for the product ZIP, or None when R2
    isn't configured or the object isn't in the bucket."""
    if product_id not in PRODUCTS:
        return None
    s3 = _r2_client()
    if s3 is None:
        return None
    bucket = os.environ.get("R2_BUCKET", "preset-packs")
    key = PRODUCTS[product_id]["file"]
    try:
        ttl = int(os.environ.get("R2_LINK_TTL_SECONDS", str(7 * 24 * 3600)))
    except ValueError:
        ttl = 7 * 24 * 3600
    try:
        s3.head_object(Bucket=bucket, Key=key)  # make sure the ZIP is there
    except Exception as e:
        app.logger.error("R2 object missing: %s/%s (%s)", bucket, key, e)
        return None
    try:
        return s3.generate_presigned_url(
            "get_object",
            Params={"Bucket": bucket, "Key": key},
            ExpiresIn=min(ttl, 7 * 24 * 3600),  # SigV4 presigns cap at 7 days
        )
    except Exception as e:
        app.logger.error("R2 presign failed for %s: %s", key, e)
        return None

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
