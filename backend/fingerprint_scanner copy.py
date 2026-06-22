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
from main_scraper import PlaywrightDetector, HEADERS, normalize_url, SourceDetector, domain_resolves

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

def safe_soup(html: str) -> BeautifulSoup:
    if not html:
        return BeautifulSoup("", "html.parser")
    try:
        return BeautifulSoup(html, "html.parser")
    except Exception:
        return BeautifulSoup("", "html.parser")

def extract_fingerprint_from_html(html: str, base_url: str) -> dict:
    """Scan HTML to extract theme details, scripts, and app signatures."""
    soup = safe_soup(html)
    
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

def load_mock_config(domain: str) -> dict:
    try:
        json_path = "/Users/kushagrashukla/coding/gokwik-leads/backend/mock_stores.json"
        if os.path.exists(json_path):
            with open(json_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
                return data.get(domain)
    except Exception:
        pass
    return None


def delete_abandoned_merchant(domain: str, reason: str):
    try:
        domain = canonical_domain(domain)
        # Check if it was active checkout before, so we can send a Slack notification
        merchant_doc = merchants.find_one({"domain": domain})
        old_live = None
        if merchant_doc:
            old_live = merchant_doc.get("live_checkout")
            
        # Delete from both collections
        merchants.delete_one({"domain": domain})
        merchant_fingerprints.delete_one({"merchant": domain})
        
        print(f"\n[{domain}] Removed abandoned/inactive merchant from DB: {reason}")
        if old_live:
            send_slack_notification(domain, old_live, None, f"Store Removed ({reason})")
    except Exception as e:
        print(f"[Error] Failed to remove abandoned store {domain}: {e}")

class CheapScanner:
    def __init__(self):
        self.detector = SourceDetector()
        
    def fetch_html(self, url: str) -> str:
        html, _, _ = self.detector.fetch_html(url)
        return html
        
    def get_product_page_url(self, homepage_html: str, base_url: str) -> str:
        """Parse homepage to find a valid product link."""
        soup = safe_soup(homepage_html)
        for a in soup.find_all("a", href=True):
            href = a["href"].lower()
            if "/products/" in href:
                return urljoin(base_url, a["href"])
        return urljoin(base_url, "/products") # Default fallback
 
    def scan_merchant(self, domain: str) -> dict:
        mock_cfg = load_mock_config(domain)
        if mock_cfg:
            return {
                "theme_id": mock_cfg.get("theme_id", "unknown"),
                "theme_family": mock_cfg.get("theme_family", "unknown"),
                "checkout_providers": mock_cfg.get("checkout_providers", []),
                "checkout_scripts": mock_cfg.get("checkout_scripts", []),
                "app_signatures": mock_cfg.get("app_signatures", [])
            }
 
        """Fetch Homepage, Product page, and Cart page to build combined fingerprint."""
        # Use main scraper SourceDetector fallback URL resolution (resolves www vs non-www redirection issues)
        home_html, final_url, status_code = self.detector.fetch_with_fallbacks(domain)
        
        # If status code indicates store locked, unpaid, or not found (abandoned):
        if status_code in [402, 423, 404]:
            reason = "Shopify Payment Required (402)" if status_code == 402 else ("Shopify Closed/Locked (423)" if status_code == 423 else "Not Found (404)")
            delete_abandoned_merchant(domain, reason)
            return {}

        if status_code == 429:
            return {"rate_limited": True}

        if not home_html:
            return {}
            
        home_fp = extract_fingerprint_from_html(home_html, final_url)
        
        # 2. Fetch product page
        prod_url = self.get_product_page_url(home_html, final_url)
        prod_html = self.fetch_html(prod_url)
        prod_fp = extract_fingerprint_from_html(prod_html, prod_url) if prod_html else {}
        
        # 3. Fetch cart page
        cart_url = urljoin(final_url, "/cart")
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
        # Check hot brand keywords on raw homepage html and update database
        try:
            from main_scraper import ScoringEngine
            soup = safe_soup(home_html)
            title = soup.find('title')
            title_text = title.text.strip() if title else ''
            meta = soup.find('meta', attrs={'name': 'description'})
            desc = meta.get('content', '') if meta else ''
            
            is_hot = ScoringEngine.detect_hot_brand({"title": title_text, "description": desc}, home_html)
            merchants.update_one({"domain": domain}, {"$set": {"hot_brand": is_hot}})
        except Exception as e:
            print(f"[Error] Failed to detect hot brand for {domain}: {e}")

        return merged

def calculate_hash(fp: dict) -> str:
    """Generate SHA-256 hash of the normalized fingerprint."""
    serialized = json.dumps(fp, sort_keys=True)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()[:16]

def send_slack_notification(domain: str, old_provider: str, new_provider: str, reason: str):
    old_lower = (old_provider or "").lower()
    new_lower = (new_provider or "").lower()
    
    is_left = (old_lower == "flexype" and new_lower != "flexype")
    is_joined = (new_lower == "flexype" and old_lower != "flexype")
    
    if not (is_left or is_joined):
        print(f"[Slack Skip] Checkout change for {domain} ({old_provider} -> {new_provider}) does not involve FlexyPe. Skipping Slack notification.")
        return

    webhook_url = os.getenv("SLACK_WEBHOOK_URL")
    if not webhook_url:
        print("[Slack Warning] SLACK_WEBHOOK_URL is not set. Skipping Slack notification.")
        return
        
    old_display = old_provider if old_provider else "None"
    new_display = new_provider if new_provider else "None"
    
    if is_left:
        text = f"🚨 *CRITICAL: {domain} left FlexyPe* (`{old_display}` ➔ `{new_display}`) [Reason: {reason}]"
    else:
        text = f"🎉 *SUCCESS: {domain} joined FlexyPe* (`{old_display}` ➔ `{new_display}`) [Reason: {reason}]"
        
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
            
            # Double-check if checkout changed
            merchant_doc = merchants.find_one({"domain": domain})
            if merchant_doc:
                old_live_checkout = merchant_doc.get("live_checkout")
                new_live_checkout = result.get("live_checkout")
                if old_live_checkout != new_live_checkout:
                    print(f"[Double-Check] Checkout changed for {domain} ({old_live_checkout} -> {new_live_checkout}). Running retry scan...")
                    retry_result = await detector.scan(domain)
                    if retry_result.get("live_checkout") == new_live_checkout:
                        print(f"[Double-Check] Confirmed checkout change for {domain}: {new_live_checkout}")
                        result = retry_result
                    else:
                        print(f"[Double-Check] Warning: Retry did not confirm the change for {domain}. Reverting to: {old_live_checkout}")
                        result["live_checkout"] = old_live_checkout
                        result["live_confidence"] = merchant_doc.get("live_confidence", 0)
                        result["live_evidence"] = merchant_doc.get("live_evidence", [])
                        result["has_kwikpass"] = merchant_doc.get("has_kwikpass", False)
                        result["kwikpass_evidence"] = merchant_doc.get("kwikpass_evidence", [])

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
                merchant_doc["last_scan"] = datetime.utcnow().strftime('%Y-%m-%d %H:%M')
                
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


def involves_flexype_churn(old_fp: dict, new_fp: dict) -> bool:
    """Check if FlexyPe script or provider was present in one but not the other."""
    old_scripts = old_fp.get("checkout_scripts", []) or []
    new_scripts = new_fp.get("checkout_scripts", []) or []
    old_providers = old_fp.get("checkout_providers", []) or []
    new_providers = new_fp.get("checkout_providers", []) or []
    
    had_flexype = (
        any("flexype" in str(s).lower() for s in old_scripts) or
        any("flexype" in str(p).lower() for p in old_providers)
    )
    has_flexype = (
        any("flexype" in str(s).lower() for s in new_scripts) or
        any("flexype" in str(p).lower() for p in new_providers)
    )
    return had_flexype != has_flexype


async def scan_all_merchants_fingerprint():
    """Run Lite Fingerprint Scan for all merchants in the DB."""
    cleanup_old_records()
    all_merchants = list(merchants.find({}, {"domain": 1}))
    total_count = len(all_merchants)
    print(f"Starting Lite Fingerprint Scan for {total_count} merchants...")
    
    # Send Slack start log
    webhook_url = os.getenv("SLACK_WEBHOOK_URL")
    if webhook_url:
        try:
            requests.post(webhook_url, json={"text": f"🔄 *Starting Lite Fingerprint Scan* for {total_count} merchants..."}, timeout=10)
        except Exception as e:
            print(f"[Slack Error] Failed to send start log: {e}")
            
    scanner = CheapScanner()
    success_count = 0
    failed_count = 0
    changed_count = 0
    escalated_count = 0
    changes_list = []
    
    # Filter out mock domains like test-store-1.com
    filtered_merchants = [m for m in all_merchants if "test-store-1.com" not in m["domain"]]
    total_count = len(filtered_merchants)

    import random
    sem = asyncio.Semaphore(4)
    processed_count = 0
    rate_limit_count = 0
    lock = asyncio.Lock()

    async def scan_single(domain):
        nonlocal success_count, failed_count, changed_count, escalated_count, processed_count, rate_limit_count
        # Polite delay/jitter to prevent hitting CDN rate limits
        await asyncio.sleep(random.uniform(0.1, 0.5))
        
        # 1. DNS pre-flight check: delete if domain is completely dead/abandoned
        if not load_mock_config(domain) and not domain_resolves(domain):
            await asyncio.to_thread(delete_abandoned_merchant, domain, "DNS does not resolve")
            async with lock:
                processed_count += 1
                if processed_count % 10 == 0 or processed_count == total_count:
                    percent = (processed_count / total_count) * 100
                    print(f"Lite Scan Progress: {percent:.1f}% ({processed_count}/{total_count} scanned)...")
            return

        try:
            async with sem:
                fp = await asyncio.to_thread(scanner.scan_merchant, domain)

                if fp and fp.get("rate_limited"):
                    async with lock:
                        rate_limit_count += 1
                        processed_count += 1
                        if processed_count % 10 == 0 or processed_count == total_count:
                            percent = (processed_count / total_count) * 100
                            print(f"Lite Scan Progress: {percent:.1f}% ({processed_count}/{total_count} scanned)...")
                    return

                if not fp:
                    merchant_doc = await asyncio.to_thread(merchants.find_one, {"domain": domain})
                    if merchant_doc and merchant_doc.get("live_checkout") == "FlexyPe":
                        async with lock:
                            failed_count += 1
                    async with lock:
                        processed_count += 1
                        if processed_count % 10 == 0 or processed_count == total_count:
                            percent = (processed_count / total_count) * 100
                            print(f"Lite Scan Progress: {percent:.1f}% ({processed_count}/{total_count} scanned)...")
                    return

                async with lock:
                    success_count += 1
                
                new_hash = calculate_hash(fp)
                
                existing = await asyncio.to_thread(merchant_fingerprints.find_one, {"merchant": domain})
                
                if not existing:
                    await asyncio.to_thread(
                        merchant_fingerprints.insert_one,
                        {
                            "merchant": domain,
                            "fingerprint_hash": new_hash,
                            "theme_id": fp["theme_id"],
                            "theme_family": fp["theme_family"],
                            "checkout_providers": fp["checkout_providers"],
                            "checkout_scripts": fp["checkout_scripts"],
                            "app_signatures": fp["app_signatures"],
                            "last_scanned": datetime.utcnow()
                        }
                    )
                    
                    has_flexy = (
                        any("flexype" in str(s).lower() for s in fp.get("checkout_scripts", [])) or
                        any("flexype" in str(p).lower() for p in fp.get("checkout_providers", []))
                    )
                    if has_flexy:
                        print(f"\n[{domain}] Seeded fingerprint (uses FlexyPe): {new_hash}")
                else:
                    old_hash = existing["fingerprint_hash"]
                    if old_hash == new_hash:
                        await asyncio.to_thread(
                            merchant_fingerprints.update_one,
                            {"merchant": domain},
                            {"$set": {"last_scanned": datetime.utcnow()}}
                        )
                    else:
                        old_fp = {
                            "theme_id": existing.get("theme_id", "unknown"),
                            "theme_family": existing.get("theme_family", "unknown"),
                            "checkout_providers": existing.get("checkout_providers", []),
                            "checkout_scripts": existing.get("checkout_scripts", []),
                            "app_signatures": existing.get("app_signatures", [])
                        }
                        
                        is_flexype_related = involves_flexype_churn(old_fp, fp)
                        
                        if is_flexype_related:
                            print(f"\n[{domain}] Fingerprint changed involving FlexyPe churn: {old_hash} -> {new_hash}")
                            async with lock:
                                changed_count += 1
                                changes_list.append(domain)
                            
                            triggers = analyze_diff_and_escalate(domain, old_fp, fp)
                            if triggers:
                                async with lock:
                                    escalated_count += 1
                                reason = ", ".join(triggers)
                                await trigger_playwright_check(domain, reason)
                        
                        await asyncio.to_thread(
                            merchant_fingerprints.update_one,
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

                async with lock:
                    processed_count += 1
                    if processed_count % 10 == 0 or processed_count == total_count:
                        percent = (processed_count / total_count) * 100
                        print(f"Lite Scan Progress: {percent:.1f}% ({processed_count}/{total_count} scanned)...")

        except Exception as e:
            print(f"\n[Error] Failed to scan {domain}: {e}")
            async with lock:
                processed_count += 1
                if processed_count % 10 == 0 or processed_count == total_count:
                    percent = (processed_count / total_count) * 100
                    print(f"Lite Scan Progress: {percent:.1f}% ({processed_count}/{total_count} scanned)...")

    tasks = [scan_single(m["domain"]) for m in filtered_merchants]
    await asyncio.gather(*tasks)

    print() # Newline to complete progress line

    # Print a local summary to the console:
    summary_text = (
        f"✅ *Lite Fingerprint Scan Completed Summary*\n"
        f"• *Total Merchants:* {total_count}\n"
        f"• *Successful Scans:* {success_count}\n"
        f"• *Failed FlexyPe Fetches:* {failed_count}\n"
        f"• *Rate-Limited Skips:* {rate_limit_count}\n"
        f"• *FlexyPe Fingerprint Changes:* {changed_count}\n"
        f"• *Escalations Triggered:* {escalated_count}\n"
    )
    if changes_list:
        formatted_changes = ", ".join(f"`{d}`" for d in changes_list[:10])
        summary_text += f"• *Changes List:* {formatted_changes}"
        if len(changes_list) > 10:
            summary_text += f" and {len(changes_list) - 10} more"

    print("\n" + summary_text.replace("*", ""))

    # Send Slack notification with summary
    webhook_url = os.getenv("SLACK_WEBHOOK_URL")
    if webhook_url:
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
    parser.add_argument("--test-slack", action="store_true", help="Trigger a test Slack notification representing FlexyPe transitions")
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
    elif args.test_slack:
        print("Sending test Slack notifications for FlexyPe transitions...")
        send_slack_notification("test-left.com", "FlexyPe", "Razorpay", "test_reason")
        send_slack_notification("test-joined.com", "GoKwik", "FlexyPe", "test_reason")
        send_slack_notification("test-other.com", "GoKwik", "Razorpay", "test_reason") # should be skipped
    elif args.cron_12h:
        asyncio.run(scan_all_merchants_fingerprint())
    elif args.cron_24h:
        asyncio.run(run_daily_playwright_verification())
    else:
        parser.print_help()

if __name__ == "__main__":
    main()
