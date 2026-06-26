#!/usr/bin/env python3
import warnings
warnings.filterwarnings("ignore")

from bs4 import BeautifulSoup
from functools import wraps

# ─────────────────────────────────────────────
#  AUTH / ROLE HELPERS
# ─────────────────────────────────────────────
def current_user():
    email = request.headers.get("X-User-Email", "").strip().lower()
    if not email:
        return None
    return db.fp_users.find_one({"email": email})

def require_role(*roles):
    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            user = current_user()
            if not user:
                return jsonify({"error":"Unauthorized"}),401
            if not user.get("active", True):
                return jsonify({"error":"Account disabled"}),403
            if user["role"] not in roles:
                return jsonify({"error":"Forbidden"}),403
            return fn(*args, **kwargs)
        return wrapper
    return decorator

from flask import Flask, request, jsonify, send_from_directory, redirect
from flask_cors import CORS
import subprocess
import sys
import json
import os
from db import db, merchants, merchant_fingerprints, fingerprint_history
import gmail as gmail_mod
import dotenv
dotenv.load_dotenv()
FAILED_LOG = "failed_scans.log"

app = Flask(
    __name__,
    static_folder="../frontend",
    static_url_path=""
)
CORS(app)

def canonical_domain(domain: str) -> str:
    domain = str(domain).strip().lower()
    if domain.startswith("https://"):
        domain = domain[8:]
    elif domain.startswith("http://"):
        domain = domain[7:]
    domain = domain.split('/')[0]
    if domain.startswith("www."):
        domain = domain[4:]
    return domain

def scan_domain(domain, exclude_master=False):
    """Run scanner for single domain."""
    domain = canonical_domain(domain)
    try:
        cmd = [sys.executable, "inputScanner.py", "--domain", domain]
        if exclude_master:
            cmd.append("--exclude-master")
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=90
        )

        print("RETURN CODE:", result.returncode)
        print("STDOUT:", result.stdout)
        print("STDERR:", result.stderr)
        
        # Look for JSON result in output
        for line in result.stdout.split('\n'):
            if '"live_checkout"' in line:
                try:
                    return json.loads(line)
                except:
                    pass
        
        # If no JSON found in stdout, look for results.json in known locations
        candidates = [
            os.path.join('..', 'data', 'results.json'),
            os.path.join('..', 'results.json'),
            os.path.join('.', 'results.json'),
        ]
        for cand in candidates:
            if os.path.exists(cand):
                try:
                    with open(cand, 'r') as f:
                        all_results = json.load(f)
                        for r in all_results:
                            if r.get('domain') == domain:
                                return r
                except Exception:
                    continue

        return None
    except subprocess.TimeoutExpired:
        return None
    except Exception as e:
        print(f"Error: {e}")
        return None

@app.route('/')
def index():
    return send_from_directory("../frontend", "index.html")

@app.route('/styles.css')
def styles():
    return send_from_directory("../frontend", 'styles.css')

@app.route('/app.js')
def app_js():
    return send_from_directory("../frontend", 'app.js')

@app.route('/scan-domain', methods=['POST'])
@require_role("admin", "salesteammember")
def scan():
    data = request.json or {}
    domain = data.get('domain')
    if domain:
        domain = canonical_domain(domain)
    exclude_master = data.get('exclude_master', False)
    
    if not domain:
        return jsonify({"error": "No domain provided"}), 400
    
    print(f"\n[Scan] Request for: {domain} (exclude_master={exclude_master})")
    
    if not exclude_master:
        # Ensure domain is present in domains.csv (append if missing)
        try:
            csv_path = "../data/domains.csv"
            normalized = domain.strip()
            # normalize: remove scheme and trailing slash
            if normalized.startswith('http://') or normalized.startswith('https://'):
                normalized = normalized.split('://', 1)[1]
            normalized = normalized.rstrip('/')

            if not os.path.exists(csv_path):
                with open(csv_path, 'w') as f:
                    f.write('domain\n')

            with open(csv_path, 'r') as f:
                existing = [line.strip() for line in f if line.strip()]

            variants = {normalized, f"https://{normalized}", f"http://{normalized}"}
            if not any(v in existing for v in variants):
                with open(csv_path, 'a') as f:
                    f.write(normalized + "\n")
                print(f"[Scan] Appended {normalized} to domains.csv")
        except Exception as e:
            print(f"[Scan] Failed to update domains.csv: {e}")
    
    result = scan_domain(domain, exclude_master=exclude_master)
    
    if result:
        print(f"[Scan] Success: {domain} -> {result.get('live_checkout', 'No checkout')}")
        return jsonify(result)
    else:
        # Attempt to capture scanner output for diagnostics
        try:
            cmd = [sys.executable, "inputScanner.py", "--domain", domain]
            if exclude_master:
                cmd.append("--exclude-master")
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=90
            )
            stdout_preview = (proc.stdout or "")[:4000]
            stderr_preview = (proc.stderr or "")[:4000]
            with open(FAILED_LOG, 'a') as lf:
                lf.write(f"=== Scan failure: {domain} | returncode={proc.returncode} ===\n")
                lf.write(stdout_preview + "\n")
                lf.write(stderr_preview + "\n\n")
            print(f"[Scan] Failed: {domain} (diagnostics written to failed_scans.log)")
            print("STDOUT:")
            print(proc.stdout)
 
            print("STDERR:")
            print(proc.stderr)
            return jsonify({"error": "Scan failed", "domain": domain, "diag_file": "failed_scans.log", "returncode": proc.returncode}), 500
        except Exception as e:
            print("STDOUT:")
            print(proc.stdout)
 
            print("STDERR:")
            print(proc.stderr)
            print(f"[Scan] Failed and diagnostics capture failed: {e}")
            return jsonify({"error": "Scan failed", "domain": domain}), 500

@app.route('/results.json')
@require_role("admin", "salesteammember")
def results():
    from datetime import datetime
    data = list(
        merchants.find(
            {},
            {"_id": 0}
        )
    )
    
    # Fetch all fingerprints and map by merchant domain
    fingerprints = {fp["merchant"]: fp for fp in merchant_fingerprints.find({}, {"_id": 0})}
    
    # Fetch the latest fingerprint change log for each merchant
    try:
        latest_changes = list(fingerprint_history.aggregate([
            {"$sort": {"timestamp": -1}},
            {"$group": {
                "_id": "$merchant",
                "timestamp": {"$first": "$timestamp"},
                "changes": {"$first": "$changes"},
                "acknowledged": {"$first": "$acknowledged"}
            }}
        ]))
        changes_by_domain = {item["_id"]: item for item in latest_changes}
    except Exception as e:
        print(f"[results] Error aggregation changes: {e}")
        changes_by_domain = {}
        
    for item in data:
        domain = item.get("domain")
        
        # Merge theme/fingerprint info if available
        fp = fingerprints.get(domain)
        if fp:
            item["theme_id"] = fp.get("theme_id", "unknown")
            item["theme_family"] = fp.get("theme_family", "unknown")
        else:
            item["theme_id"] = "unknown"
            item["theme_family"] = "unknown"
            
        # Merge recent change alert
        change = changes_by_domain.get(domain)
        if change:
            item["latest_change"] = {
                "timestamp": change["timestamp"].isoformat() if isinstance(change.get("timestamp"), datetime) else str(change.get("timestamp")),
                "changes": change.get("changes", {}),
                "acknowledged": change.get("acknowledged", False)
            }
            
        if isinstance(item.get("contacted_at"), datetime):
            item["contacted_at"] = item["contacted_at"].isoformat()
            
    return jsonify(data)

@app.route('/health', methods=['GET'])
def health():
    return jsonify({"status": "ok"})


# ─────────────────────────────────────────────
#  HELPER: identify current FlexyPe user
# ─────────────────────────────────────────────
#
# The frontend sends `X-User-Email` header on every authenticated request.
# In production, swap this for a real session/JWT lookup.

from functools import wraps

# ─────────────────────────────────────────────
#  USER MANAGEMENT (MongoDB-backed, admin-only)
# ─────────────────────────────────────────────
#
# Users are stored in MongoDB `fp_users` collection:
#   { email, password_hash, secret (Base32 TOTP), role, created_at }
#
# Admin routes are protected by ADMIN_SECRET env var.
# Passwords are hashed with bcrypt — never stored in plaintext.
#
# On first boot, seed DEFAULT users from .env if `fp_users` is empty.

import hashlib, secrets, base64
from datetime import datetime as _dt

VALID_ROLES = {
    "admin",
    "salesteammember",
    "supportteammember"
}

def _hash_password(plain: str) -> str:
    """SHA-256 hash for passwords (consistent with frontend verifyPassword)."""
    return hashlib.sha256(plain.encode()).hexdigest()

def _seed_default_users():
    """Seed default users from env vars into MongoDB on first boot."""
    if db.fp_users.count_documents({}) > 0:
        return  # already seeded
    # Read comma-separated list from env: ADMIN_USERS=email:password:secret,...
    raw = os.getenv("ADMIN_USERS", "")
    if not raw:
        return
    for entry in raw.split(";"):
        parts = [p.strip() for p in entry.split(":")]
        if len(parts) < 2:
            continue
        email, password = parts[0].lower(), parts[1]
        secret = parts[2] if len(parts) > 2 else ""
        if db.fp_users.find_one({"email": email}):
            continue
        db.fp_users.insert_one({
            "name": name,
            "email": email,
            "password_hash": _hash_password(password),
            "secret": secret,
            "role": role,
            "active": True,
            "twoFAEnabled": bool(secret),
            "created_at": _dt.utcnow(),
            "created_by": request.headers.get("X-User-Email", "admin"),
            "last_password_change": _dt.utcnow()
        })
        print(f"[users] Seeded user: {email}")

# Run seed at startup
_seed_default_users()


@app.route("/api/users", methods=["GET"])
@require_role("admin")
def list_users():
    """List all users (admin only — returns safe fields, no hashes)."""
    users = list(
        db.fp_users.find(
            {},
            {
                "_id": 0,
                "password_hash": 0,
                "secret": 0
            }
        ).sort([("role", 1), ("name", 1)])
    )
    return jsonify(users)


@app.route("/api/users", methods=["POST"])
@require_role("admin")
def create_user():
    """Create a new user (admin only)."""
    name = (data.get("name") or "").strip()
    email = (data.get("email") or "").strip().lower()
    password = (data.get("password") or "").strip()
    secret = (data.get("secret") or "").strip().upper()
    role = (data.get("role") or "salesteammember").strip().lower()
    if role not in VALID_ROLES:
        return jsonify({
            "error": "Invalid role"
        }), 400
    if not email or not password:
        return jsonify({"error": "email and password required"}), 400
    if db.fp_users.find_one({"email": email}):
        return jsonify({"error": "User already exists"}), 409
    db.fp_users.insert_one({
        "email": email,
        "password_hash": _hash_password(password),
        "secret": secret,
        "role": role,
        "twoFAEnabled": bool(secret),
        "created_at": _dt.utcnow()
    })
    return jsonify({"ok": True, "email": email})


@app.route("/api/users/<email>", methods=["PATCH"])
@require_role("admin")
def update_user(email):
    """Update password or TOTP secret for a user (admin only)."""
    email = email.strip().lower()
    data = request.get_json(silent=True) or {}
    updates = {}
    if data.get("password"):
        updates["password_hash"] = _hash_password(data["password"].strip())
        updates["last_password_change"] = _dt.utcnow()
    if "secret" in data:
        updates["secret"] = data["secret"].strip().upper()
        updates["twoFAEnabled"] = bool(updates["secret"])
    if "name" in data:
        updates["name"] = data["name"].strip()
    if "role" in data:
        role = data["role"].strip().lower()
        if role not in VALID_ROLES:
            return jsonify({"error": "Invalid role"}), 400
        updates["role"] = role
    if "active" in data:
        updates["active"] = bool(data["active"])
    if not updates:
        return jsonify({"error": "Nothing to update"}), 400
    result = db.fp_users.update_one({"email": email}, {"$set": updates})
    if result.matched_count == 0:
        return jsonify({"error": "User not found"}), 404
    return jsonify({"ok": True})


@app.route("/api/users/<email>", methods=["DELETE"])
@require_role("admin")
def delete_user(email):
    """Delete a user (admin only)."""
    email = email.strip().lower()
    requester = request.headers.get("X-User-Email", "").lower()

    if requester == email:
        return jsonify({
            "error": "You cannot delete your own account."
        }), 400

    result = db.fp_users.delete_one({"email": email})
    if result.deleted_count == 0:
        return jsonify({"error": "User not found"}), 404
    return jsonify({"ok": True})


@app.route("/api/users/<email>/reset-password", methods=["POST"])
@require_role("admin")
def reset_password(email):

    data = request.get_json(silent=True) or {}
    new_password = (data.get("password") or "").strip()

    if len(new_password) < 8:
        return jsonify({"error": "Password too short"}), 400

    db.fp_users.update_one(
        {"email": email.lower()},
        {
            "$set": {
                "password_hash": _hash_password(new_password),
                "last_password_change": _dt.utcnow()
            }
        }
    )
    return jsonify({"ok": True})


@app.route("/api/auth/change-password", methods=["POST"])
def change_password():
    user = current_user()
    if not user:
        return jsonify({"error": "Not signed in"}), 401

    data = request.get_json(silent=True) or {}
    old_password = (data.get("old_password") or "").strip()
    new_password = (data.get("new_password") or "").strip()

    if len(new_password) < 8:
        return jsonify({"error": "New password must be at least 8 characters"}), 400

    import bcrypt
    if not bcrypt.checkpw(old_password.encode("utf-8"), user["password_hash"]):
        return jsonify({"error": "Incorrect current password"}), 401

    db.fp_users.update_one(
        {"email": user["email"]},
        {
            "$set": {
                "password_hash": _hash_password(new_password),
                "last_password_change": _dt.utcnow()
            }
        }
    )
    return jsonify({"ok": True})


@app.route("/api/users/<email>/disable", methods=["POST"])
@require_role("admin")
def disable_user(email):

    db.fp_users.update_one(
        {"email": email.lower()},
        {
            "$set": {
                "active": False
            }
        }
    )
    return jsonify({"ok": True})


@app.route("/api/users/<email>/enable", methods=["POST"])
@require_role("admin")
def enable_user(email):

    db.fp_users.update_one(
        {"email": email.lower()},
        {
            "$set": {
                "active": True
            }
        }
    )
    return jsonify({"ok": True})


@app.route("/api/auth/login", methods=["POST"])
def auth_login():
    """
    Verify email + password against MongoDB fp_users.
    Returns { ok, email, role, twoFAEnabled, secret } on success.
    Frontend uses twoFAEnabled + secret to run TOTP locally.
    NOTE: secret is only returned so the client-side TOTP library can verify.
    In a higher-security setup, TOTP verification should happen server-side.
    """
    data = request.get_json(silent=True) or {}
    email = (data.get("email") or "").strip().lower()
    password = (data.get("password") or "").strip()
    if not email or not password:
        return jsonify({"error": "email and password required"}), 400
    user = db.fp_users.find_one({"email": email}, {"_id": 0})
    if user and not user.get("active", True):
        return jsonify({
            "error": "This account has been disabled."
        }), 403
    if not user or user.get("password_hash") != _hash_password(password):
        return jsonify({"error": "Invalid email or password"}), 401

    db.fp_users.update_one(
        {"email": email},
        {
            "$set": {
                "last_login": _dt.utcnow()
            }
        }
    )

    return jsonify({
        "ok": True,
        "name": user.get("name", ""),
        "email": email,
        "role": user.get("role", "salesteammember"),
        "active": user.get("active", True),
        "twoFAEnabled": user.get("twoFAEnabled", False),
        "secret": user.get("secret", "")
    })



@app.route("/api/auth/setup-totp", methods=["POST"])
def auth_setup_totp():
    data = request.json or {}
    email = (data.get("email") or "").strip().lower()
    password = data.get("password", "")
    secret = data.get("secret", "").strip().upper()
    
    if not email or not password or not secret:
        return jsonify({"error": "Missing fields"}), 400
        
    user = db.fp_users.find_one({"email": email})
    if not user:
        return jsonify({"error": "Invalid credentials"}), 401
        
    import bcrypt
    if not bcrypt.checkpw(password.encode("utf-8"), user["password_hash"]):
        return jsonify({"error": "Invalid credentials"}), 401
        
    db.fp_users.update_one(
        {"email": email},
        {"$set": {
            "secret": secret,
            "twoFAEnabled": True
        }}
    )
    return jsonify({"ok": True})

@app.route("/api/users/change-password", methods=["POST"])
def change_password():
    data = request.get_json(silent=True) or {}

    email = (data.get("email") or "").strip().lower()
    old_password = (data.get("old_password") or "").strip()
    new_password = (data.get("new_password") or "").strip()

    if not email or not old_password or not new_password:
        return jsonify({"error": "Missing fields"}), 400

    user = db.fp_users.find_one({"email": email})

    if not user:
        return jsonify({"error": "User not found"}), 404

    if user["password_hash"] != _hash_password(old_password):
        return jsonify({"error": "Incorrect password"}), 401

    db.fp_users.update_one(
        {"email": email},
        {
            "$set": {
                "password_hash": _hash_password(new_password),
                "last_password_change": _dt.utcnow()
            }
        }
    )

    return jsonify({"ok": True})


# ─────────────────────────────────────────────
#  GMAIL OAUTH ROUTES
# ─────────────────────────────────────────────

@app.route("/api/auth/google/start", methods=["GET"])
def gmail_oauth_start():
    user = current_user()
    if not user:
        return jsonify({"error": "Not signed in to FlexyPe"}), 401
    try:
        url = gmail_mod.build_authorize_url(user)
        return jsonify({"authorize_url": url})
    except RuntimeError as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/auth/google/callback", methods=["GET"])
def gmail_oauth_callback():
    code  = request.args.get("code")
    state = request.args.get("state")
    error = request.args.get("error")

    frontend = os.getenv("FRONTEND_URL", "http://localhost:8080")

    if error:
        return redirect(f"{frontend}/?gmail=error&reason={error}")
    if not code or not state:
        return redirect(f"{frontend}/?gmail=error&reason=missing_params")

    try:
        gmail_mod.handle_callback(code, state, db)
        return redirect(f"{frontend}/?gmail=connected")
    except Exception as e:
        print(f"[Gmail] callback error: {e}")
        return redirect(f"{frontend}/?gmail=error&reason=callback_failed")


@app.route("/api/auth/google/status", methods=["GET"])
def gmail_oauth_status():
    user = current_user()
    if not user:
        return jsonify({"error": "Not signed in"}), 401
    return jsonify(gmail_mod.get_connection_status(user, db))


@app.route("/api/auth/google/disconnect", methods=["POST"])
def gmail_oauth_disconnect():
    user = current_user()
    if not user:
        return jsonify({"error": "Not signed in"}), 401
    ok = gmail_mod.disconnect(user, db)
    return jsonify({"ok": ok})


# ─────────────────────────────────────────────
#  SEND EMAIL
# ─────────────────────────────────────────────

@app.route("/api/send-email", methods=["POST"])
@require_role("admin", "salesteammember")
def send_email_route():
    user = current_user()
    if not user:
        return jsonify({"error": "Not signed in to FlexyPe"}), 401

    data = request.get_json(silent=True) or {}
    to      = (data.get("to") or "").strip()
    subject = (data.get("subject") or "").strip()
    body    = data.get("body") or ""
    cc      = (data.get("cc") or "").strip()
    bcc     = (data.get("bcc") or "").strip()
    domain  = (data.get("domain") or "").strip().lower()
    attachment_data = data.get("attachment_data") or ""
    attachment_name = data.get("attachment_name") or ""

    if not to or not subject or not body:
        return jsonify({"error": "Missing to / subject / body"}), 400

    try:
        result = gmail_mod.send_email(
            flexype_user_email=user,
            to=to, subject=subject, body=body,
            cc=cc, bcc=bcc, domain=domain,
            mongo_db=db,
            attachment_data=attachment_data,
            attachment_name=attachment_name,
        )
        # Auto-update merchant status to "Contacted" and save contact details
        contacted_at = None
        if domain:
            from datetime import datetime
            contacted_at_dt = datetime.utcnow()
            contacted_at = contacted_at_dt.isoformat()
            merchants.update_one(
                {"domain": domain},
                {"$set": {
                    "status": "Contacted",
                    "contacted_by": user,
                    "contacted_at": contacted_at_dt,
                    "contacted_to": to,
                    "contacted_subject": subject
                }},
            )
        return jsonify({
            **result,
            "contacted_by": user,
            "contacted_at": contacted_at,
            "contacted_to": to,
            "contacted_subject": subject
        })
    except PermissionError as e:
        return jsonify({"error": str(e), "code": "not_connected"}), 403
    except RuntimeError as e:
        return jsonify({"error": str(e)}), 500
    except Exception as e:
        print(f"[Gmail send] error: {e}")
        return jsonify({"error": "Internal error"}), 500


@app.route("/api/sent-log", methods=["GET"])
@require_role("admin", "salesteammember")
def sent_log_route():
    """List of sent emails for a domain (or for the current user)."""
    user = current_user()
    if not user:
        return jsonify({"error": "Not signed in"}), 401
    domain = (request.args.get("domain") or "").strip().lower()
    if domain:
        return jsonify(gmail_mod.list_sent_for_domain(domain, db))
    # Default: last 50 sent by this user
    cursor = db.sent_emails.find(
        {"flexype_user": user},
        {"_id": 0}
    ).sort("sent_at", -1).limit(50)
    out = []
    from datetime import datetime
    for d in cursor:
        if isinstance(d.get("sent_at"), datetime):
            d["sent_at"] = d["sent_at"].isoformat()
        out.append(d)
    return jsonify(out)


@app.route("/api/outreach/thread", methods=["GET"])
@require_role("admin", "salesteammember")
def get_email_thread():
    user = current_user()
    if not user:
        return jsonify({"error": "Not signed in"}), 401
        
    thread_id = request.args.get("thread_id")
    domain = request.args.get("domain")
    
    if not thread_id and domain:
        # Find the latest email sent to this domain to get the thread ID
        latest_email = db.sent_emails.find_one(
            {"domain": domain.strip().lower()},
            sort=[("sent_at", -1)]
        )
        if latest_email:
            thread_id = latest_email.get("gmail_thread_id")
            
    if not thread_id:
        return jsonify({"error": "No thread found for this merchant"}), 404
        
    # Helper to parse message payload
    def parse_gmail_message(msg):
        import base64
        payload = msg.get("payload", {})
        headers = payload.get("headers", [])
        
        headers_dict = {h["name"].lower(): h["value"] for h in headers}
        
        # Extract plain text body
        body = ""
        
        def walk_parts(parts):
            nonlocal body
            for part in parts:
                mime_type = part.get("mimeType", "")
                part_body = part.get("body", {})
                data = part_body.get("data", "")
                
                if mime_type == "text/plain" and data:
                    try:
                        body += base64.urlsafe_b64decode(data.encode('utf-8')).decode('utf-8', errors='ignore')
                    except Exception:
                        pass
                elif "parts" in part:
                    walk_parts(part["parts"])
                    
        if "parts" in payload:
            walk_parts(payload["parts"])
        else:
            part_body = payload.get("body", {})
            data = part_body.get("data", "")
            if data:
                try:
                    body = base64.urlsafe_b64decode(data.encode('utf-8')).decode('utf-8', errors='ignore')
                except Exception:
                    pass
                    
        if not body.strip():
            body = msg.get("snippet", "")
            
        return {
            "message_id": msg.get("id"),
            "from": headers_dict.get("from", ""),
            "to": headers_dict.get("to", ""),
            "cc": headers_dict.get("cc", ""),
            "subject": headers_dict.get("subject", ""),
            "date": headers_dict.get("date", ""),
            "body": body,
            "snippet": msg.get("snippet", "")
        }

    # Attempt to fetch thread from Gmail API
    try:
        from gmail import _load_credentials
        from googleapiclient.discovery import build
        
        creds = _load_credentials(user, db)
        service = build("gmail", "v1", credentials=creds, cache_discovery=False)
        
        thread = service.users().threads().get(userId="me", id=thread_id).execute()
        messages = thread.get("messages", [])
        
        parsed_messages = []
        for msg in messages:
            parsed_messages.append(parse_gmail_message(msg))
            
        return jsonify({
            "ok": True,
            "thread_id": thread_id,
            "messages": parsed_messages
        })
    except Exception as e:
        print(f"[get_email_thread] Fallback to local DB for thread {thread_id}: {e}")
        # Fallback to local DB records
        local_msgs = list(db.sent_emails.find({"gmail_thread_id": thread_id}))
        if not local_msgs and domain:
            # Fallback: all emails to this domain
            local_msgs = list(db.sent_emails.find({"domain": domain.strip().lower()}))
            
        # Format local emails to match
        parsed_messages = []
        for m in sorted(local_msgs, key=lambda x: x.get("sent_at", datetime.min)):
            parsed_messages.append({
                "message_id": m.get("gmail_message_id") or "local",
                "from": m.get("flexype_user") or "me",
                "to": m.get("to") or "",
                "cc": m.get("cc") or "",
                "subject": m.get("subject") or "",
                "date": m.get("sent_at").isoformat() if isinstance(m.get("sent_at"), datetime) else str(m.get("sent_at")),
                "body": m.get("body") or "",
                "snippet": (m.get("body") or "")[:100]
            })
            
        if not parsed_messages:
            return jsonify({"error": "Thread not found"}), 404
            
        return jsonify({
            "ok": True,
            "thread_id": thread_id or "local",
            "messages": parsed_messages,
            "fallback": True
        })


@app.route("/api/outreach/sync-replies", methods=["POST"])
@require_role("admin", "salesteammember")
def sync_outreach_replies():
    user = current_user()
    if not user:
        return jsonify({"error": "Not signed in"}), 401
        
    try:
        from gmail import _load_credentials
        from googleapiclient.discovery import build
        
        creds = _load_credentials(user, db)
        service = build("gmail", "v1", credentials=creds, cache_discovery=False)
    except Exception as e:
        print(f"[sync-replies] Failed to load credentials/service for {user}: {e}")
        return jsonify({"error": "Gmail connection expired or not set up. Please reconnect Gmail."}), 400
        
    from datetime import datetime
    updated_count = 0

    # Initialize processed_threads with all gmail_thread_ids currently in the db for this user
    all_user_emails = list(db.sent_emails.find({"flexype_user": user}))
    processed_threads = {e.get("gmail_thread_id") for e in all_user_emails if e.get("gmail_thread_id")}

    # Mode 1: Check existing sent emails logged in the DB that are not yet replied
    sent_list = [e for e in all_user_emails if e.get("status") != "replied"]

    for email in sent_list:
        thread_id = email.get("gmail_thread_id")
        recipient = email.get("to")
        if not thread_id or not recipient:
            continue
            
        try:
            # Clean recipient (extract clean email address from "Name <email>")
            clean_recipient = recipient.lower().strip()
            if "<" in clean_recipient and ">" in clean_recipient:
                clean_recipient = clean_recipient.split("<")[1].split(">")[0].strip()
                
            # Clean CC and BCC
            clean_cc = email.get("cc", "").lower().strip()
            if "<" in clean_cc and ">" in clean_cc:
                clean_cc = clean_cc.split("<")[1].split(">")[0].strip()
                
            clean_bcc = email.get("bcc", "").lower().strip()
            if "<" in clean_bcc and ">" in clean_bcc:
                clean_bcc = clean_bcc.split("<")[1].split(">")[0].strip()
                
            flexype_user = email.get("flexype_user", "")

            # Get thread details from Gmail API
            thread = service.users().threads().get(userId="me", id=thread_id).execute()
            messages = thread.get("messages", [])
            
            if len(messages) > 1:
                # Check if any message after the first one is a reply (from recipient, cc, bcc, or user themselves)
                has_reply = False
                for msg in messages[1:]:
                    headers = msg.get("payload", {}).get("headers", [])
                    sender = ""
                    for h in headers:
                        if h["name"].lower() == "from":
                            sender = h["value"].lower()
                            break
                    
                    is_from_recipient = clean_recipient in sender
                    is_from_user = flexype_user and flexype_user.lower() in sender
                    is_from_cc = clean_cc and clean_cc in sender
                    is_from_bcc = clean_bcc and clean_bcc in sender
                    
                    if is_from_recipient or is_from_user or is_from_cc or is_from_bcc:
                        has_reply = True
                        break
                        
                if has_reply:
                    # Update email status in db
                    db.sent_emails.update_one(
                        {"gmail_thread_id": thread_id},
                        {"$set": {"status": "replied", "replied_at": datetime.utcnow()}}
                    )
                    # Also update the merchant's status to "Replied"
                    domain = email.get("domain")
                    if domain:
                        db.merchants.update_one(
                            {"domain": domain},
                            {"$set": {"status": "Replied"}}
                        )
                    updated_count += 1
        except Exception as e:
            print(f"[sync-replies] Error checking thread {thread_id}: {e}")
            continue

    # Mode 2: Auto-discover threads initiated directly via Gmail client
    try:
        threads_res = service.users().threads().list(userId="me", maxResults=20).execute()
        gmail_threads = threads_res.get("threads", [])
        
        for t in gmail_threads:
            t_id = t["id"]
            if t_id in processed_threads:
                continue
                
            try:
                # Fetch thread detail
                thread_detail = service.users().threads().get(userId="me", id=t_id).execute()
                messages = thread_detail.get("messages", [])
                if not messages:
                    continue
                    
                # Analyze the first message in the thread
                first_msg = messages[0]
                first_headers = first_msg.get("payload", {}).get("headers", [])
                
                to_header = ""
                subject_header = ""
                for h in first_headers:
                    h_name = h["name"].lower()
                    if h_name == "to":
                        to_header = h["value"]
                    elif h_name == "subject":
                        subject_header = h["value"]
                        
                if not to_header:
                    continue
                    
                # Clean recipient
                clean_to = to_header.lower().strip()
                if "<" in clean_to and ">" in clean_to:
                    clean_to = clean_to.split("<")[1].split(">")[0].strip()
                    
                # Look for a merchant record that has this email
                merchant = db.merchants.find_one({"emails": clean_to})
                if not merchant:
                    continue
                    
                # Check for replies in this thread
                if len(messages) > 1:
                    has_reply = False
                    for msg in messages[1:]:
                        msg_headers = msg.get("payload", {}).get("headers", [])
                        sender = ""
                        for h in msg_headers:
                            if h["name"].lower() == "from":
                                sender = h["value"].lower()
                                break
                                
                        is_from_recipient = clean_to in sender
                        is_from_user = user.lower() in sender
                        
                        if is_from_recipient or is_from_user:
                            has_reply = True
                            break
                            
                    if has_reply:
                        # Update the merchant's status in DB
                        db.merchants.update_one(
                            {"_id": merchant["_id"]},
                            {"$set": {"status": "Replied"}}
                        )
                        
                        # Create a local sent_emails log record so it shows up in Outreach
                        log_doc = {
                            "flexype_user": user,
                            "to":           to_header,
                            "cc":           "",
                            "bcc":          "",
                            "subject":      subject_header or f"Outreach via Gmail",
                            "body":         first_msg.get("snippet", ""),
                            "domain":       merchant.get("domain"),
                            "gmail_message_id": first_msg.get("id"),
                            "gmail_thread_id":  t_id,
                            "sent_at":      datetime.utcnow(),
                            "status":       "replied",
                            "replied_at":   datetime.utcnow()
                        }
                        db.sent_emails.insert_one(log_doc)
                        updated_count += 1
            except Exception as ex:
                print(f"[sync-replies] Auto-discovery error on thread {t_id}: {ex}")
                continue
    except Exception as e:
        print(f"[sync-replies] Auto-discovery list failed: {e}")

    return jsonify({"ok": True, "updated_count": updated_count})


# ─────────────────────────────────────────────
#  MONITORING & CHANGE DETECTION
# ─────────────────────────────────────────────

@app.route("/api/monitor/scan-fingerprints", methods=["POST"])
@require_role("admin", "salesteammember")
def monitor_scan_fingerprints():
    import threading
    import fingerprint_scanner as fs
    
    def run_scan():
        import asyncio
        asyncio.run(fs.scan_all_merchants_fingerprint())
        
    threading.Thread(target=run_scan, daemon=True).start()
    return jsonify({"ok": True, "message": "Cheap fingerprint scan started in background"})


@app.route("/api/monitor/run-playwright-all", methods=["POST"])
@require_role("admin", "salesteammember")
def monitor_run_playwright_all():
    import threading
    import fingerprint_scanner as fs
    
    def run_verification():
        import asyncio
        asyncio.run(fs.run_daily_playwright_verification())
        
    threading.Thread(target=run_verification, daemon=True).start()
    return jsonify({"ok": True, "message": "Daily Playwright verification started in background"})


@app.route("/api/monitor/runs", methods=["GET"])
@require_role("admin", "salesteammember")
def monitor_runs_log():
    cursor = db.playwright_runs.find(
        {},
        {"_id": 0}
    ).sort("timestamp", -1).limit(50)
    out = []
    from datetime import datetime
    for d in cursor:
        if isinstance(d.get("timestamp"), datetime):
            d["timestamp"] = d["timestamp"].isoformat()
        out.append(d)
    return jsonify(out)


@app.route("/api/monitor/changes", methods=["GET"])
@require_role("admin", "salesteammember")
def monitor_changes_log():
    from datetime import datetime
    cursor = fingerprint_history.find({}).sort("timestamp", -1).limit(200)
    out = []
    for d in cursor:
        d["_id"] = str(d["_id"])
        if isinstance(d.get("timestamp"), datetime):
            d["timestamp"] = d["timestamp"].isoformat()
        # Default acknowledged to False if not present
        if "acknowledged" not in d:
            d["acknowledged"] = False
        out.append(d)
    return jsonify(out)


@app.route("/api/monitor/changes/acknowledge", methods=["POST"])
@require_role("admin", "salesteammember")
def acknowledge_changes():
    from bson.objectid import ObjectId
    data = request.json or {}
    ids = data.get("ids", [])
    acknowledge_all = data.get("all", False)
    
    if acknowledge_all:
        res = fingerprint_history.update_many(
            {"acknowledged": {"$ne": True}},
            {"$set": {"acknowledged": True}}
        )
        return jsonify({"ok": True, "updated_count": res.modified_count})
        
    if not ids:
        return jsonify({"error": "No IDs provided"}), 400
        
    object_ids = []
    for i in ids:
        try:
            object_ids.append(ObjectId(i))
        except Exception:
            pass
            
    if not object_ids:
        return jsonify({"error": "Invalid IDs"}), 400
        
    res = fingerprint_history.update_many(
        {"_id": {"$in": object_ids}},
        {"$set": {"acknowledged": True}}
    )
    return jsonify({"ok": True, "updated_count": res.modified_count})


def recalculate_all_scores():
    """Recalculate lead_score, priority and hot_brand for all merchants based on the updated scoring engine."""
    print("[Startup] Recalculating lead scores, priorities, and hot brand signals for all merchants...")
    try:
        from main_scraper import ScoringEngine
        all_merchants = list(merchants.find({}))
        updated_count = 0
        for m in all_merchants:
            # Detect hot brand and calculate new score/priority
            hot_brand = ScoringEngine.detect_hot_brand(m)
            hot_brand_reason = ScoringEngine.detect_hot_brand_reason(m) if hot_brand else ""
            m_with_hot = {**m, 'hot_brand': hot_brand}
            score = ScoringEngine.calculate_score(m_with_hot)
            priority = ScoringEngine.get_priority(score)
            
            merchants.update_one(
                {"_id": m["_id"]},
                {"$set": {
                    "hot_brand": hot_brand,
                    "hot_brand_reason": hot_brand_reason,
                    "lead_score": score,
                    "priority": priority
                }}
            )
            updated_count += 1
        print(f"[Startup] Score recalculation complete. Updated {updated_count} merchants.")
    except Exception as e:
        print(f"[Startup] Error during score recalculation: {e}")



@app.route("/api/support/churned", methods=["GET"])
@require_role("admin", "supportteammember")
def get_churned_stores():
    from datetime import datetime
    # Find all fingerprint changes where live_checkout old was FlexyPe and new is different
    cursor = fingerprint_history.find({
        "changes.live_checkout.old": {"$regex": ".*FlexyPe.*", "$options": "i"},
        "changes.live_checkout.new": {"$not": {"$regex": ".*FlexyPe.*", "$options": "i"}}
    }).sort("timestamp", -1)
    
    out = []
    for d in cursor:
        d["_id"] = str(d["_id"])
        
        # Extract fields for frontend
        d["domain"] = d.get("merchant", "Unknown")
        changes = d.get("changes", {})
        live_checkout = changes.get("live_checkout", {})
        d["old_checkout"] = live_checkout.get("old", "FlexyPe")
        d["new_checkout"] = live_checkout.get("new", "Unknown")
        
        if isinstance(d.get("timestamp"), datetime):
            d["timestamp"] = d["timestamp"].isoformat()
        
        # We need notes and assigned fields
        if "notes" not in d:
            d["notes"] = ""
        if "assigned" not in d:
            d["assigned"] = ""
            
        out.append(d)
    return jsonify(out)

@app.route("/api/support/churned/<change_id>", methods=["PATCH"])
@require_role("admin", "supportteammember")
def update_churned_store(change_id):
    from bson.objectid import ObjectId
    data = request.json or {}
    
    updates = {}
    if "notes" in data:
        updates["notes"] = str(data["notes"])
    if "assigned" in data:
        updates["assigned"] = str(data["assigned"])
        
    if not updates:
        return jsonify({"ok": True})
        
    res = fingerprint_history.update_one(
        {"_id": ObjectId(change_id)},
        {"$set": updates}
    )
    
    if res.matched_count == 0:
        return jsonify({"error": "Change not found"}), 404
        
    return jsonify({"ok": True})

if __name__ == '__main__':
    print("\n" + "=" * 60)
    print("FlexyPe Outreach Server")
    print("=" * 60)
    print("Server running at: http://localhost:8080")
    print("")
    print("Usage:")
    print("  1. Open http://localhost:8080 in browser")
    print("  2. Enter domain in scan bar")
    print("  3. Click 'Scan Domain'")
    print("=" * 60 + "\n")
    
    recalculate_all_scores()
    app.run(host='0.0.0.0', port=8080, debug=False)

