#!/usr/bin/env python3
import re
import json
import hashlib
import requests
import asyncio
import argparse
import os
from datetime import datetime, timedelta
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse
from playwright.async_api import async_playwright

from db import db, merchants, merchant_fingerprints, fingerprint_history, playwright_runs
from main_scraper import PlaywrightDetector, HEADERS, normalize_url

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

def send_slack_notification(domain: str, old_provider: str, new_provider: str, reason: str):
    webhook_url = os.getenv("SLACK_WEBHOOK_URL")
    if not webhook_url:
        print("[Slack Warning] SLACK_WEBHOOK_URL is not set. Skipping Slack notification.")
        return
        
    old_display = old_provider if old_provider else "None"
    new_display = new_provider if new_provider else "None"
    
    is_flexype_removed = old_provider and old_provider.lower() == "flexype" and (not new_provider or new_provider.lower() != "flexype")
    if is_flexype_removed:
        emoji = "🚨 *CRITICAL ALERT: Merchant left FlexyPe*"
    else:
        emoji = "🔄 *Checkout Provider Changed*"
        
    text = (
        f"{emoji} for *{domain}*\n"
        f"• *Old Provider:* `{old_display}`\n"
        f"• *New Provider:* `{new_display}`\n"
        f"• *Trigger Reason:* {reason}\n"
    )
    
    payload = {"text": text}
    try:
        response = requests.post(webhook_url, json=payload, timeout=10)
        if response.status_code == 200:
            print(f"[Slack Success] Sent checkout change notification for {domain}")
        else:
            print(f"[Slack Error] Failed to send notification. Status code: {response.status_code}, Response: {response.text}")
    except Exception as e:
        print(f"[Slack Error] Exception while sending notification: {e}")

def cleanup_old_records():
    """Delete database history records older than RETENTION_DAYS."""
    try:
        retention_days = int(os.getenv("RETENTION_DAYS", "10"))
        cutoff = datetime.utcnow() - timedelta(days=retention_days)
        
        # Delete old fingerprint change logs
        hist_res = fingerprint_history.delete_many({"timestamp": {"$lt": cutoff}})
        # Delete old playwright verification run logs
        run_res = playwright_runs.delete_many({"timestamp": {"$lt": cutoff}})
        
        if hist_res.deleted_count > 0 or run_res.deleted_count > 0:
            print(f"[Retention Cleanup] Cleaned up older records (older than {retention_days} days). "
                  f"Deleted {hist_res.deleted_count} history entries and {run_res.deleted_count} Playwright run entries.")
    except Exception as e:
        print(f"[Retention Error] Failed to clean up old records: {e}")

async def trigger_playwright_check(domain: str, trigger_reason: str):

    """Run Playwright live verification for the merchant."""
    domain = canonical_domain(domain)
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
                "live_confidence": result.get("live_confidence", 0),
                "live_evidence": result.get("live_evidence", []),
                "checkout_scan_stages": result.get("checkout_scan_stages", []),
                "provider_candidates": result.get("provider_candidates", []),
                "timestamp": datetime.utcnow()
            }
            playwright_runs.insert_one(run_doc)
            
            # Update the merchant's profile status in DB if checkout was broken/changed
            merchant_doc = merchants.find_one({"domain": domain})
            if merchant_doc:
                old_live_checkout = merchant_doc.get("live_checkout")
                new_live_checkout = result["live_checkout"]

                # Update checkout details
                merchant_doc["live_checkout"] = result["live_checkout"]
                merchant_doc["live_confidence"] = result.get("live_confidence", 0)
                merchant_doc["live_evidence"] = result.get("live_evidence", [])
                merchant_doc["has_kwikpass"] = result.get("has_kwikpass", False)
                merchant_doc["kwikpass_evidence"] = result.get("kwikpass_evidence", [])
                
                # Recalculate lead score and priority
                from main_scraper import ScoringEngine
                merchant_doc["lead_score"] = ScoringEngine.calculate_score(merchant_doc)
                merchant_doc["priority"] = ScoringEngine.get_priority(merchant_doc["lead_score"])
                merchant_doc["last_scan"] = datetime.utcnow().strftime('%Y-%m-%d')
                
                merchants.update_one(
                    {"domain": domain},
                    {"$set": {
                        "live_checkout": merchant_doc["live_checkout"],
                        "live_confidence": merchant_doc["live_confidence"],
                        "live_evidence": merchant_doc["live_evidence"],
                        "has_kwikpass": merchant_doc["has_kwikpass"],
                        "kwikpass_evidence": merchant_doc["kwikpass_evidence"],
                        "lead_score": merchant_doc["lead_score"],
                        "priority": merchant_doc["priority"],
                        "last_scan": merchant_doc["last_scan"]
                    }}
                )
                print(f"[Database Sync] Updated {domain} in merchants collection. Live checkout: {result['live_checkout']}. Score: {merchant_doc['lead_score']}, Priority: {merchant_doc['priority']}")

                if old_live_checkout != new_live_checkout:
                    send_slack_notification(domain, old_live_checkout, new_live_checkout, trigger_reason)
                    # Log all live_checkout changes to fingerprint_history so they persist until acknowledged
                    fingerprint_history.insert_one({
                        "merchant": domain,
                        "old_hash": "",
                        "new_hash": "",
                        "changes": {
                            "live_checkout": {
                                "old": old_live_checkout if old_live_checkout else "None",
                                "new": new_live_checkout if new_live_checkout else "None"
                            }
                        },
                        "timestamp": datetime.utcnow()
                    })

                    # Update historical checkouts if old_live_checkout is a valid provider and not already in list
                    if old_live_checkout and old_live_checkout.lower() not in ["none", "unknown"]:
                        hist = merchant_doc.get("historical_checkouts", [])
                        if old_live_checkout not in hist:
                            hist.append(old_live_checkout)
                            merchants.update_one(
                                {"domain": domain},
                                {"$set": {"historical_checkouts": hist}}
                            )

            if not result["live_checkout"]:
                stages = ", ".join(result.get("checkout_scan_stages", [])) or "none"
                print(
                    f"[Warning] Playwright did not detect a live checkout for {domain}! "
                    f"Stages: {stages}"
                )
            else:
                print(
                    f"[Success] Playwright verified checkout for {domain} -> "
                    f"{result['live_checkout']} ({result.get('live_confidence', 0)}%)"
                )
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
    """Run Lite Fingerprint Scan for all merchants in the DB."""
    cleanup_old_records()
    all_merchants = list(merchants.find({}, {"domain": 1}))
    total_count = len(all_merchants)
    print(f"Starting Lite Fingerprint Scan for {total_count} merchants...")
    
    scanner = CheapScanner()
    success_count = 0
    failed_count = 0
    changed_count = 0
    escalated_count = 0
    changes_list = []
    
    for m in all_merchants:
        domain = m["domain"]
        fp = scanner.scan_merchant(domain)
        if not fp:
            print(f"[{domain}] Fetch failed during fingerprint scan.")
            failed_count += 1
            continue
            
        success_count += 1
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
                
                # Note: We do not log cheap scan changes (themes, scripts, signatures) to fingerprint_history anymore.
                # Only Playwright-verified live_checkout changes are written to the database.
                
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
                changed_count += 1
                changes_list.append(domain)
                
                # Escalate if required
                if triggers:
                    escalated_count += 1
                    reason = ", ".join(triggers)
                    await trigger_playwright_check(domain, reason)

    # Send Slack notification with summary
    webhook_url = os.getenv("SLACK_WEBHOOK_URL")
    if webhook_url:
        summary_text = (
            f"✅ *Lite Fingerprint Scan Completed Summary*\n"
            f"• *Total Merchants:* {total_count}\n"
            f"• *Successful Scans:* {success_count}\n"
            f"• *Failed Fetches:* {failed_count}\n"
            f"• *Fingerprint Changes:* {changed_count}\n"
            f"• *Escalations Triggered:* {escalated_count}\n"
        )
        if changes_list:
            formatted_changes = ", ".join(f"`{d}`" for d in changes_list[:10])
            summary_text += f"• *Changes List:* {formatted_changes}"
            if len(changes_list) > 10:
                summary_text += f" and {len(changes_list) - 10} more"
        try:
            requests.post(webhook_url, json={"text": summary_text}, timeout=10)
            print("[Slack Success] Sent Lite Scan summary notification.")
        except Exception as e:
            print(f"[Slack Error] Failed to send summary: {e}")


async def run_daily_playwright_verification():
    """Run full Playwright validation for all merchants once every 24 hours."""
    cleanup_old_records()
    all_merchants = list(merchants.find({}, {"domain": 1}))
    total_count = len(all_merchants)
    print(f"Starting daily full Playwright verification for {total_count} merchants...")
    
    success_count = 0
    failed_count = 0
    changes_count = 0
    changes_list = []
    
    for m in all_merchants:
        domain = m["domain"]
        merchant_before = merchants.find_one({"domain": domain})
        old_checkout = merchant_before.get("live_checkout") if merchant_before else None
        
        try:
            await trigger_playwright_check(domain, "daily_verification")
            success_count += 1
            
            # Check if it changed
            merchant_after = merchants.find_one({"domain": domain})
            new_checkout = merchant_after.get("live_checkout") if merchant_after else None
            if old_checkout != new_checkout:
                changes_count += 1
                changes_list.append(f"`{domain}` (`{old_checkout}` -> `{new_checkout}`)")
        except Exception:
            failed_count += 1
            
    # Send Slack notification with summary
    webhook_url = os.getenv("SLACK_WEBHOOK_URL")
    if webhook_url:
        summary_text = (
            f"🚀 *Full Playwright Test Completed Summary*\n"
            f"• *Total Merchants:* {total_count}\n"
            f"• *Successful Runs:* {success_count}\n"
            f"• *Failed Runs:* {failed_count}\n"
            f"• *Checkout Provider Changes:* {changes_count}\n"
        )
        if changes_list:
            summary_text += f"• *Changes List:* {', '.join(changes_list[:10])}"
            if len(changes_list) > 10:
                summary_text += f" and {len(changes_list) - 10} more"
        try:
            requests.post(webhook_url, json={"text": summary_text}, timeout=10)
        except Exception as e:
            print(f"[Slack Error] Failed to send summary: {e}")

def main():
    parser = argparse.ArgumentParser(description="FlexyPe Outreach Merchant Monitoring System")
    parser.add_argument("--test", help="Test cheap scan on a single domain")
    parser.add_argument("--cron-12h", action="store_true", help="Trigger 12-hour cheap fingerprint check")
    parser.add_argument("--cron-24h", action="store_true", help="Trigger 24-hour Playwright validation check")
    args = parser.parse_args()
    
    if args.test:
        scanner = CheapScanner()
        print(f"Scanning domain: {args.test}...")
        fp = scanner.scan_merchant(args.test)
        print("Extracted Fingerprint:")
        print(json.dumps(fp, indent=2))
        print("Fingerprint Hash:", calculate_hash(fp))
    elif args.cron_12h:
        asyncio.run(scan_all_merchants_fingerprint())
    elif args.cron_24h:
        asyncio.run(run_daily_playwright_verification())
    else:
        parser.print_help()

if __name__ == "__main__":
    main()
