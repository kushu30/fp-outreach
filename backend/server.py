#!/usr/bin/env python3
from flask import Flask, request, jsonify, send_from_directory, redirect
from flask_cors import CORS
import subprocess
import sys
import json
import os
from db import db, merchants
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

def scan_domain(domain):
    """Run scanner for single domain."""
    try:
        result = subprocess.run(
            [sys.executable, "inputScanner.py", "--domain", domain],
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
def scan():
    data = request.json
    domain = data.get('domain')
    
    if not domain:
        return jsonify({"error": "No domain provided"}), 400
    
    print(f"\n[Scan] Request for: {domain}")
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
    
    result = scan_domain(domain)
    
    if result:
        print(f"[Scan] Success: {domain} -> {result.get('live_checkout', 'No checkout')}")
        return jsonify(result)
    else:
        # Attempt to capture scanner output for diagnostics
        try:
            proc = subprocess.run(
                [sys.executable, "inputScanner.py", "--domain", domain],
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
def results():
    from datetime import datetime
    data = list(
        merchants.find(
            {},
            {"_id": 0}
        )
    )
    for item in data:
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

def _current_user():
    email = request.headers.get("X-User-Email", "").strip().lower()
    if not email:
        return None
    return email


# ─────────────────────────────────────────────
#  GMAIL OAUTH ROUTES
# ─────────────────────────────────────────────

@app.route("/api/auth/google/start", methods=["GET"])
def gmail_oauth_start():
    user = _current_user()
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
    user = _current_user()
    if not user:
        return jsonify({"error": "Not signed in"}), 401
    return jsonify(gmail_mod.get_connection_status(user, db))


@app.route("/api/auth/google/disconnect", methods=["POST"])
def gmail_oauth_disconnect():
    user = _current_user()
    if not user:
        return jsonify({"error": "Not signed in"}), 401
    ok = gmail_mod.disconnect(user, db)
    return jsonify({"ok": ok})


# ─────────────────────────────────────────────
#  SEND EMAIL
# ─────────────────────────────────────────────

@app.route("/api/send-email", methods=["POST"])
def send_email_route():
    user = _current_user()
    if not user:
        return jsonify({"error": "Not signed in to FlexyPe"}), 401

    data = request.get_json(silent=True) or {}
    to      = (data.get("to") or "").strip()
    subject = (data.get("subject") or "").strip()
    body    = data.get("body") or ""
    cc      = (data.get("cc") or "").strip()
    bcc     = (data.get("bcc") or "").strip()
    domain  = (data.get("domain") or "").strip().lower()

    if not to or not subject or not body:
        return jsonify({"error": "Missing to / subject / body"}), 400

    try:
        result = gmail_mod.send_email(
            flexype_user_email=user,
            to=to, subject=subject, body=body,
            cc=cc, bcc=bcc, domain=domain,
            mongo_db=db,
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
def sent_log_route():
    """List of sent emails for a domain (or for the current user)."""
    user = _current_user()
    if not user:
        return jsonify({"error": "Not signed in"}), 401
    domain = (request.args.get("domain") or "").strip().lower()
    if domain:
        return jsonify(gmail_mod.list_sent_for_domain(domain, db))
    # Default: last 50 sent by this user
    cursor = db.sent_emails.find(
        {"flexype_user": user},
        {"_id": 0, "body": 0}
    ).sort("sent_at", -1).limit(50)
    out = []
    from datetime import datetime
    for d in cursor:
        if isinstance(d.get("sent_at"), datetime):
            d["sent_at"] = d["sent_at"].isoformat()
        out.append(d)
    return jsonify(out)


# ─────────────────────────────────────────────
#  MONITORING & CHANGE DETECTION
# ─────────────────────────────────────────────

@app.route("/api/monitor/scan-fingerprints", methods=["POST"])
def monitor_scan_fingerprints():
    import threading
    import fingerprint_scanner as fs
    
    def run_scan():
        import asyncio
        asyncio.run(fs.scan_all_merchants_fingerprint())
        
    threading.Thread(target=run_scan, daemon=True).start()
    return jsonify({"ok": True, "message": "Cheap fingerprint scan started in background"})


@app.route("/api/monitor/run-playwright-all", methods=["POST"])
def monitor_run_playwright_all():
    import threading
    import fingerprint_scanner as fs
    
    def run_verification():
        import asyncio
        asyncio.run(fs.run_daily_playwright_verification())
        
    threading.Thread(target=run_verification, daemon=True).start()
    return jsonify({"ok": True, "message": "Daily Playwright verification started in background"})


@app.route("/api/monitor/runs", methods=["GET"])
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
    
    app.run(host='0.0.0.0', port=8080, debug=False)