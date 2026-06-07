#!/usr/bin/env python3
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
import subprocess
import sys
import json
import os

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
            [sys.executable, "scanner_v2.py", "--domain", domain],
            capture_output=True,
            text=True,
            timeout=90
        )
        
        # Look for JSON result in output
        for line in result.stdout.split('\n'):
            if '"live_checkout"' in line:
                try:
                    return json.loads(line)
                except:
                    pass
        
        # If no JSON found, read from results.json
        if os.path.exists("results.json"):
            with open("results.json", "r") as f:
                all_results = json.load(f)
                for r in all_results:
                    if r["domain"] == domain:
                        return r
        
        return None
    except subprocess.TimeoutExpired:
        return None
    except Exception as e:
        print(f"Error: {e}")
        return None

@app.route('/')
def index():
    return send_from_directory("../frontend", "dashboard.html")

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
                [sys.executable, "scanner_v2.py", "--domain", domain],
                capture_output=True,
                text=True,
                timeout=90
            )
            stdout_preview = (proc.stdout or "")[:4000]
            stderr_preview = (proc.stderr or "")[:4000]
            with open('failed_scans.log', 'a') as lf:
                lf.write(f"=== Scan failure: {domain} | returncode={proc.returncode} ===\n")
                lf.write(stdout_preview + "\n")
                lf.write(stderr_preview + "\n\n")
            print(f"[Scan] Failed: {domain} (diagnostics written to failed_scans.log)")
            return jsonify({"error": "Scan failed", "domain": domain, "diag_file": "failed_scans.log", "returncode": proc.returncode}), 500
        except Exception as e:
            print(f"[Scan] Failed and diagnostics capture failed: {e}")
            return jsonify({"error": "Scan failed", "domain": domain}), 500

@app.route('/results.json')
def results():
    if os.path.exists("results.json"):
        return send_from_directory("../data", "results.json")
    return jsonify([])

@app.route('/health', methods=['GET'])
def health():
    return jsonify({"status": "ok"})

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