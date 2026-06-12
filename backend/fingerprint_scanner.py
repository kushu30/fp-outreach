#!/usr/bin/env python3
import re
import json
import hashlib
import requests
import asyncio
import argparse
from datetime import datetime
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse
from playwright.async_api import async_playwright

from db import db, merchants, merchant_fingerprints, fingerprint_history, playwright_runs
from main_scraper import PlaywrightDetector, HEADERS, normalize_url

# Keywords for detecting competitor checkout scripts
CHECKOUT_KEYWORDS = [
    "gokwik", "shopflo", "fastrr", "shiprocket", "kwikpass",
    "razorpay", "magiccheckout", "cashfree", "simpl", "antigravity"
]

def extract_theme_info(html: str):
    """Extract Shopify theme ID and name from HTML content."""
    theme_id = None
    theme_family = None
    
    # 1. Look for Shopify.theme JS block
    match = re.search(r'Shopify\.theme\s*=\s*({[^}]+})', html)
    if match:
        try:
            # Simple manual parse in case of invalid json, or json.loads
            # Clean matching group to make it valid JSON
            json_str = match.group(1)
            # Replace single quotes, trailing commas, etc if any, but standard Shopify is valid JSON
            theme_data = json.loads(json_str)
            theme_id = str(theme_data.get("id", ""))
            theme_family = str(theme_data.get("name", "")).lower()
        except Exception:
            pass
            
    # 2. Fallback to meta tag: name="shopify-theme-id"
    if not theme_id:
        meta_match = re.search(r'name=["\']shopify-theme-id["\']\s+content=["\'](\d+)["\']', html)
        if meta_match:
            theme_id = meta_match.group(1)
            
    # 3. Fallback to style asset paths containing theme id
    if not theme_id:
        style_match = re.search(r'assets/theme.*\.css\?id=(\d+)', html)
        if style_match:
            theme_id = style_match.group(1)
            
    return theme_id or "unknown", theme_family or "unknown"

def clean_script_url(url: str) -> str:
    """Normalize script URLs by removing query parameters (asset versions)."""
    parsed = urlparse(url)
    # Get just the path, remove everything after ? or #
    path = parsed.path
    # Return last part of path (filename)
    return path.split("/")[-1]

def extract_fingerprint_from_html(html: str, base_url: str) -> dict:
    """Scan HTML to extract theme details, scripts, and app signatures."""
    soup = BeautifulSoup(html, "html.parser")
    
    theme_id, theme_family = extract_theme_info(html)
    
    # Extract script sources
    scripts = []
    for s in soup.find_all("script", src=True):
        src = s["src"].lower()
        # Resolve relative URLs
        full_src = urljoin(base_url, src)
        scripts.append(full_src)
        
    # Detect checkout scripts matching keywords
    checkout_scripts = []
    for src in scripts:
        filename = clean_script_url(src)
        if any(kw in filename for kw in CHECKOUT_KEYWORDS):
            checkout_scripts.append(filename)
            
    # Detect checkout providers
    checkout_providers = []
    corpus = html.lower() + " " + " ".join(scripts).lower()
    
    # Simple check matching patterns
    from main_scraper import HISTORICAL_PATTERNS
    for provider, patterns in HISTORICAL_PATTERNS.items():
        if any(p.lower() in corpus for p in patterns):
            checkout_providers.append(provider.lower())
            
    # Detect app signatures
    app_signatures = []
    if "antigravity" in corpus:
        app_signatures.append("antigravity")
        
    return {
        "theme_id": theme_id,
        "theme_family": theme_family,
        "checkout_providers": sorted(list(set(checkout_providers))),
        "checkout_scripts": sorted(list(set(checkout_scripts))),
        "app_signatures": sorted(list(set(app_signatures)))
    }

class CheapScanner:
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update(HEADERS)
        
    def fetch_html(self, url: str) -> str:
        try:
            r = self.session.get(url, timeout=10, allow_redirects=True)
            if r.status_code < 400:
                return r.text
        except Exception:
            pass
        return ""
        
    def get_product_page_url(self, homepage_html: str, base_url: str) -> str:
        """Parse homepage to find a valid product link."""
        soup = BeautifulSoup(homepage_html, "html.parser")
        for a in soup.find_all("a", href=True):
            href = a["href"].lower()
            if "/products/" in href:
                return urljoin(base_url, a["href"])
        return urljoin(base_url, "/products") # Default fallback

    def scan_merchant(self, domain: str) -> dict:
        """Fetch Homepage, Product page, and Cart page to build combined fingerprint."""
        base_url = normalize_url(domain)
        
        # 1. Fetch homepage
        home_html = self.fetch_html(base_url)
        if not home_html:
            return {}
            
        home_fp = extract_fingerprint_from_html(home_html, base_url)
        
        # 2. Fetch product page
        prod_url = self.get_product_page_url(home_html, base_url)
        prod_html = self.fetch_html(prod_url)
        prod_fp = extract_fingerprint_from_html(prod_html, prod_url) if prod_html else {}
        
        # 3. Fetch cart page
        cart_url = urljoin(base_url, "/cart")
        cart_html = self.fetch_html(cart_url)
        cart_fp = extract_fingerprint_from_html(cart_html, cart_url) if cart_html else {}
        
        # Merge fingerprints
        merged = {
            "theme_id": home_fp.get("theme_id") or prod_fp.get("theme_id") or cart_fp.get("theme_id") or "unknown",
            "theme_family": home_fp.get("theme_family") or prod_fp.get("theme_family") or cart_fp.get("theme_family") or "unknown",
            "checkout_providers": sorted(list(set(
                home_fp.get("checkout_providers", []) +
                prod_fp.get("checkout_providers", []) +
                cart_fp.get("checkout_providers", [])
            ))),
            "checkout_scripts": sorted(list(set(
                home_fp.get("checkout_scripts", []) +
                prod_fp.get("checkout_scripts", []) +
                cart_fp.get("checkout_scripts", [])
            ))),
            "app_signatures": sorted(list(set(
                home_fp.get("app_signatures", []) +
                prod_fp.get("app_signatures", []) +
                cart_fp.get("app_signatures", [])
            )))
        }
        return merged

def calculate_hash(fp: dict) -> str:
    """Generate SHA-256 hash of the normalized fingerprint."""
    serialized = json.dumps(fp, sort_keys=True)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()[:16]

async def trigger_playwright_check(domain: str, trigger_reason: str):
    """Run Playwright live verification for the merchant."""
    print(f"[Escalation] Running Playwright verification for {domain} due to: {trigger_reason}")
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=['--disable-dev-shm-usage', '--no-sandbox']
        )
        try:
            detector = PlaywrightDetector(browser)
            result = await detector.scan(domain)
            
            # Log the run
            run_doc = {
                "merchant": domain,
                "trigger": trigger_reason,
                "status": "success" if result["live_checkout"] else "no_checkout",
                "checkout_detected": result["live_checkout"] is not None,
                "detected_provider": result["live_checkout"],
                "timestamp": datetime.utcnow()
            }
            playwright_runs.insert_one(run_doc)
            
            # Update the merchant's profile status in DB if checkout was broken/changed
            if not result["live_checkout"]:
                # If no checkout detected by Playwright, mark status as "Broken" or similar?
                # For now, let's log it and optionally update merchants
                print(f"[Warning] Playwright did not detect a live checkout for {domain}!")
            else:
                print(f"[Success] Playwright verified checkout for {domain} -> {result['live_checkout']}")
        except Exception as e:
            print(f"[Escalation Error] Playwright run failed for {domain}: {e}")
            run_doc = {
                "merchant": domain,
                "trigger": trigger_reason,
                "status": "failed",
                "error": str(e),
                "timestamp": datetime.utcnow()
            }
            playwright_runs.insert_one(run_doc)
        finally:
            await browser.close()

def analyze_diff_and_escalate(domain: str, old_fp: dict, new_fp: dict) -> list:
    """Compare fingerprints and return fields that trigger an escalation."""
    triggers = []
    
    # 1. Theme ID change
    if old_fp.get("theme_id") != new_fp.get("theme_id"):
        triggers.append("theme_id_change")
        
    # 2. Theme family/architecture change
    if old_fp.get("theme_family") != new_fp.get("theme_family"):
        triggers.append("theme_architecture_change")
        
    # 3. Antigravity script or signature removed
    old_scripts = old_fp.get("checkout_scripts", [])
    new_scripts = new_fp.get("checkout_scripts", [])
    if any("antigravity" in s for s in old_scripts) and not any("antigravity" in s for s in new_scripts):
        triggers.append("antigravity_script_removed")
        
    old_sigs = old_fp.get("app_signatures", [])
    new_sigs = new_fp.get("app_signatures", [])
    if "antigravity" in old_sigs and "antigravity" not in new_sigs:
        triggers.append("antigravity_signature_removed")
        
    # 4. Checkout providers list changed
    if old_fp.get("checkout_providers") != new_fp.get("checkout_providers"):
        triggers.append("checkout_providers_changed")
        
    # 5. New checkout script detected
    competitors_new = [s for s in new_scripts if s not in old_scripts]
    if competitors_new:
        triggers.append(f"new_checkout_script_detected ({', '.join(competitors_new)})")
        
    return triggers

async def scan_all_merchants_fingerprint():
    """Run cheap fingerprint scan for all merchants in the DB."""
    all_merchants = list(merchants.find({}, {"domain": 1}))
    print(f"Starting cheap fingerprint scan for {len(all_merchants)} merchants...")
    
    scanner = CheapScanner()
    
    for m in all_merchants:
        domain = m["domain"]
        fp = scanner.scan_merchant(domain)
        if not fp:
            print(f"[{domain}] Fetch failed during fingerprint scan.")
            continue
            
        new_hash = calculate_hash(fp)
        
        # Check against db
        existing = merchant_fingerprints.find_one({"merchant": domain})
        
        if not existing:
            # Seed fingerprint
            merchant_fingerprints.insert_one({
                "merchant": domain,
                "fingerprint_hash": new_hash,
                "theme_id": fp["theme_id"],
                "theme_family": fp["theme_family"],
                "checkout_providers": fp["checkout_providers"],
                "checkout_scripts": fp["checkout_scripts"],
                "app_signatures": fp["app_signatures"],
                "last_scanned": datetime.utcnow()
            })
            print(f"[{domain}] Seeded fingerprint hash: {new_hash}")
        else:
            old_hash = existing["fingerprint_hash"]
            if old_hash == new_hash:
                # No change
                merchant_fingerprints.update_one(
                    {"merchant": domain},
                    {"$set": {"last_scanned": datetime.utcnow()}}
                )
            else:
                # Hash changed! Calculate diff
                old_fp = {
                    "theme_id": existing.get("theme_id", "unknown"),
                    "theme_family": existing.get("theme_family", "unknown"),
                    "checkout_providers": existing.get("checkout_providers", []),
                    "checkout_scripts": existing.get("checkout_scripts", []),
                    "app_signatures": existing.get("app_signatures", [])
                }
                
                triggers = analyze_diff_and_escalate(domain, old_fp, fp)
                
                # Record change history
                diff = {}
                for k in ["theme_id", "theme_family", "checkout_providers", "checkout_scripts", "app_signatures"]:
                    if old_fp[k] != fp[k]:
                        diff[k] = {"old": old_fp[k], "new": fp[k]}
                        
                fingerprint_history.insert_one({
                    "merchant": domain,
                    "old_hash": old_hash,
                    "new_hash": new_hash,
                    "changes": diff,
                    "timestamp": datetime.utcnow()
                })
                
                # Update latest fingerprint in DB
                merchant_fingerprints.update_one(
                    {"merchant": domain},
                    {"$set": {
                        "fingerprint_hash": new_hash,
                        "theme_id": fp["theme_id"],
                        "theme_family": fp["theme_family"],
                        "checkout_providers": fp["checkout_providers"],
                        "checkout_scripts": fp["checkout_scripts"],
                        "app_signatures": fp["app_signatures"],
                        "last_scanned": datetime.utcnow()
                    }}
                )
                
                print(f"[{domain}] Fingerprint changed: {old_hash} -> {new_hash}")
                
                # Escalate if required
                if triggers:
                    reason = ", ".join(triggers)
                    await trigger_playwright_check(domain, reason)

async def run_daily_playwright_verification():
    """Run full Playwright validation for all merchants once every 24 hours."""
    all_merchants = list(merchants.find({}, {"domain": 1}))
    print(f"Starting daily full Playwright verification for {len(all_merchants)} merchants...")
    
    for m in all_merchants:
        domain = m["domain"]
        await trigger_playwright_check(domain, "daily_verification")

def main():
    parser = argparse.ArgumentParser(description="FlexyPe Outreach Merchant Monitoring System")
    parser.add_argument("--test", help="Test cheap scan on a single domain")
    parser.add_argument("--cron-6h", action="store_true", help="Trigger 6-hour cheap fingerprint check")
    parser.add_argument("--cron-24h", action="store_true", help="Trigger 24-hour Playwright validation check")
    args = parser.parse_args()
    
    if args.test:
        scanner = CheapScanner()
        print(f"Scanning domain: {args.test}...")
        fp = scanner.scan_merchant(args.test)
        print("Extracted Fingerprint:")
        print(json.dumps(fp, indent=2))
        print("Fingerprint Hash:", calculate_hash(fp))
    elif args.cron_6h:
        asyncio.run(scan_all_merchants_fingerprint())
    elif args.cron_24h:
        asyncio.run(run_daily_playwright_verification())
    else:
        parser.print_help()

if __name__ == "__main__":
    main()
