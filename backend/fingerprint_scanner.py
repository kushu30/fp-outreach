#!/usr/bin/env python3
import re
import json
import hashlib
import requests
import asyncio
import argparse
import os
import random
from datetime import datetime, timedelta
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse
from playwright.async_api import async_playwright

from db import db, merchants, merchant_fingerprints, fingerprint_history, playwright_runs
# Single source of truth: import the hardened helpers AND the shared checkout
# arbiter / dead-store handling from main_scraper. This file no longer makes its
# own checkout-change decisions — it produces a reading and hands it to the same
# state machine main_scraper uses.
from main_scraper import (
    PlaywrightDetector,
    SourceDetector,
    ScoringEngine,
    HEADERS,
    normalize_url,
    HISTORICAL_PATTERNS,
    canonical_domain,
    domain_resolves,
    load_mock_config,
    DOMAIN_HARD_CAP,
    persist_single_reading,
    scan_domain,
    _maybe_recheck,
    active_merchant_filter,
    mark_dead_reading,
    mark_active,
    ABANDON_AFTER,
    THROTTLE,                 # shared global throttle (same instance every file uses)
    RATE_LIMITED_STATUS,      # sentinel returned by fetch_with_fallbacks when blocked
    send_slack_notification as _ms_slack,
)

# ── Monitor concurrency / pacing ──
# Lowered from 8/6 → 4/3. The global THROTTLE now enforces the real request rate;
# the semaphores only bound simultaneous open sockets. 4 cheap workers × the
# throttle's per-request pacing is far gentler on shared Shopify/Cloudflare edge
# than the old 8-wide burst that produced 755/907 rate-limited.
CHEAP_CONCURRENCY      = int(os.getenv("CHEAP_CONCURRENCY", "4"))
PLAYWRIGHT_CONCURRENCY = int(os.getenv("PLAYWRIGHT_CONCURRENCY", "3"))
BROWSER_RECYCLE_EVERY  = int(os.getenv("BROWSER_RECYCLE_EVERY", "500"))

# Per-domain rate-limit retry budget. A throttled domain is RE-QUEUED with
# exponential backoff instead of being silently dropped (the old bug).
RL_MAX_RETRIES         = int(os.getenv("RL_MAX_RETRIES", "4"))
RL_BACKOFF_BASE        = float(os.getenv("RL_BACKOFF_BASE", "4.0"))
RL_BACKOFF_CAP         = float(os.getenv("RL_BACKOFF_CAP", "90.0"))

CHECKOUT_KEYWORDS = [
    "gokwik", "shopflo", "fastrr", "shiprocket", "kwikpass",
    "razorpay", "magiccheckout", "cashfree", "simpl", "antigravity"
]


def safe_soup(html: str) -> BeautifulSoup:
    if not html:
        return BeautifulSoup("", "html.parser")
    try:
        return BeautifulSoup(html, "html.parser")
    except Exception:
        return BeautifulSoup("", "html.parser")


def extract_theme_info(html: str):
    theme_id = None
    theme_family = None
    match = re.search(r'Shopify\.theme\s*=\s*({[^}]+})', html)
    if match:
        try:
            theme_data = json.loads(match.group(1))
            theme_id = str(theme_data.get("id", ""))
            theme_family = str(theme_data.get("name", "")).lower()
        except Exception:
            pass
    if not theme_id:
        meta_match = re.search(r'name=["\']shopify-theme-id["\']\s+content=["\'](\d+)["\']', html)
        if meta_match:
            theme_id = meta_match.group(1)
    if not theme_id:
        style_match = re.search(r'assets/theme.*\.css\?id=(\d+)', html)
        if style_match:
            theme_id = style_match.group(1)
    return theme_id or "unknown", theme_family or "unknown"


def clean_script_url(url: str) -> str:
    return urlparse(url).path.split("/")[-1]


def extract_fingerprint_from_html(html: str, base_url: str) -> dict:
    soup = safe_soup(html)
    theme_id, theme_family = extract_theme_info(html)

    scripts = []
    for s in soup.find_all("script", src=True):
        scripts.append(urljoin(base_url, s["src"].lower()))

    checkout_scripts = []
    for src in scripts:
        filename = clean_script_url(src)
        if any(kw in filename for kw in CHECKOUT_KEYWORDS):
            checkout_scripts.append(filename)

    checkout_providers = []
    corpus = html.lower() + " " + " ".join(scripts).lower()
    for provider, patterns in HISTORICAL_PATTERNS.items():
        if any(p.lower() in corpus for p in patterns):
            checkout_providers.append(provider.lower())

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


def sync_domains_from_csv():
    """Read domains from ../data/domains.csv and add any new ones to DB."""
    try:
        import pandas as pd
        csv_path = "../data/domains.csv"
        if not os.path.exists(csv_path):
            csv_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "domains.csv")
        if os.path.exists(csv_path):
            df = pd.read_csv(csv_path)
            if 'domain' in df.columns:
                domains = df['domain'].dropna().tolist()
                new_count = 0
                for d in domains:
                    canonical = canonical_domain(d)
                    if not canonical:
                        continue
                    if not merchants.find_one({"domain": canonical}):
                        merchants.insert_one({
                            "domain": canonical,
                            "shopify": True,
                            "active": True,
                            "last_scan": datetime.utcnow().strftime('%Y-%m-%d %H:%M')
                        })
                        print(f"[CSV Sync] Added new domain: {canonical}")
                        new_count += 1
                if new_count > 0:
                    print(f"[CSV Sync] Added {new_count} new domains.")
    except Exception as e:
        print(f"[CSV Sync Error] {e}")


class CheapScanner:
    def __init__(self):
        # Shares the process-wide THROTTLE via SourceDetector's default.
        self.detector = SourceDetector()

    def fetch_html(self, url: str) -> str:
        html, _, _ = self.detector.fetch_html(url)
        return html

    def get_product_page_url(self, homepage_html: str, base_url: str) -> str:
        soup = safe_soup(homepage_html)
        for a in soup.find_all("a", href=True):
            href = a["href"].lower()
            if "/products/" in href:
                return urljoin(base_url, a["href"])
        return urljoin(base_url, "/products")

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

        home_html, final_url, status_code = self.detector.fetch_with_fallbacks(domain)

        # Rate-limited (sentinel from fetch_with_fallbacks): bubble up so the
        # caller can re-queue with backoff. NEVER counted as dead.
        if status_code == RATE_LIMITED_STATUS:
            return {"rate_limited": True}

        # Dead-store statuses → typed signal; caller decides whether to count a
        # dead reading (never delete here, never on a single read).
        if status_code in [402, 423, 404]:
            reason = {402: "Payment Required (402)",
                      423: "Closed/Locked (423)",
                      404: "Not Found (404)"}[status_code]
            return {"dead": True, "reason": reason}
        if status_code in [429, 430]:
            # Belt-and-suspenders: shouldn't normally reach here because the
            # detector converts these to RATE_LIMITED_STATUS, but handle anyway.
            return {"rate_limited": True}
        if not home_html:
            return {}

        home_fp = extract_fingerprint_from_html(home_html, final_url)

        # Secondary fetches now reuse the throttled fetch_html. If either gets
        # blocked it simply yields empty and we fall back to the homepage fp —
        # we do NOT fail the whole merchant on a secondary-page block.
        prod_url = self.get_product_page_url(home_html, final_url)
        prod_html = self.fetch_html(prod_url)
        prod_fp = extract_fingerprint_from_html(prod_html, prod_url) if prod_html else {}
        cart_fp = {}

        merged = {
            "theme_id": home_fp.get("theme_id") or prod_fp.get("theme_id") or cart_fp.get("theme_id") or "unknown",
            "theme_family": home_fp.get("theme_family") or prod_fp.get("theme_family") or cart_fp.get("theme_family") or "unknown",
            "checkout_providers": sorted(list(set(
                home_fp.get("checkout_providers", []) +
                prod_fp.get("checkout_providers", []) +
                cart_fp.get("checkout_providers", [])))),
            "checkout_scripts": sorted(list(set(
                home_fp.get("checkout_scripts", []) +
                prod_fp.get("checkout_scripts", []) +
                cart_fp.get("checkout_scripts", [])))),
            "app_signatures": sorted(list(set(
                home_fp.get("app_signatures", []) +
                prod_fp.get("app_signatures", []) +
                cart_fp.get("app_signatures", [])))),
        }

        try:
            soup = safe_soup(home_html)
            title = soup.find('title')
            title_text = title.text.strip() if title else ''
            meta = soup.find('meta', attrs={'name': 'description'})
            desc = meta.get('content', '') if meta else ''
            is_hot = ScoringEngine.detect_hot_brand({"title": title_text, "description": desc}, home_html)
            merchants.update_one({"domain": domain}, {"$set": {"hot_brand": is_hot}})
        except Exception as e:
            print(f"[Warn] hot-brand detect failed for {domain}: {e}")

        return merged


def calculate_hash(fp: dict) -> str:
    return hashlib.sha256(json.dumps(fp, sort_keys=True).encode("utf-8")).hexdigest()[:16]


def cleanup_old_records():
    try:
        retention_days = int(os.getenv("RETENTION_DAYS", "10"))
        cutoff = datetime.utcnow() - timedelta(days=retention_days)
        hist_res = fingerprint_history.delete_many({"timestamp": {"$lt": cutoff}})
        run_res = playwright_runs.delete_many({"timestamp": {"$lt": cutoff}})
        if hist_res.deleted_count or run_res.deleted_count:
            print(f"[Retention Cleanup] Deleted {hist_res.deleted_count} history + "
                  f"{run_res.deleted_count} run entries (>{retention_days}d).")
    except Exception as e:
        print(f"[Retention Error] {e}")


# ─────────────────────────────────────────────
#  PLAYWRIGHT ESCALATION  (capped → shared arbiter)
# ─────────────────────────────────────────────

async def _verified_live_scan(detector: PlaywrightDetector, domain: str) -> dict:
    if not await asyncio.to_thread(domain_resolves, domain):
        print(f"[Skip] {domain}: DNS does not resolve.")
        return None
    try:
        return await asyncio.wait_for(detector.scan(domain), timeout=DOMAIN_HARD_CAP)
    except asyncio.TimeoutError:
        print(f"[Cap] {domain}: hit {DOMAIN_HARD_CAP}s hard cap.")
        return None


async def trigger_playwright_check(domain: str, trigger_reason: str, browser):
    domain = canonical_domain(domain)
    print(f"[Escalation] {domain} — {trigger_reason}")

    sem = asyncio.Semaphore(1)
    try:
        reading = await scan_domain(browser, domain, sem)
        reading = await _maybe_recheck(browser, reading, exclude_master=False)
    except Exception as e:
        print(f"[Escalation Error] {domain}: {e}")
        playwright_runs.insert_one({
            "merchant": domain, "trigger": trigger_reason,
            "status": "failed", "error": str(e), "timestamp": datetime.utcnow()
        })
        return

    playwright_runs.insert_one({
        "merchant": domain, "trigger": trigger_reason,
        "status": "success" if reading.get("live_checkout") else "no_checkout",
        "checkout_detected": reading.get("live_checkout") is not None,
        "detected_provider": reading.get("live_checkout"),
        "live_confidence": reading.get("live_confidence", 0),
        "live_evidence": reading.get("live_evidence", []),
        "timestamp": datetime.utcnow()
    })

    persist_single_reading(reading, exclude_master=False)

    lc = reading.get("live_checkout")
    print(f"[Done] {domain} -> {lc or 'none'} ({reading.get('live_confidence', 0)}%)")


def analyze_diff_and_escalate(domain: str, old_fp: dict, new_fp: dict) -> list:
    triggers = []
    if old_fp.get("theme_id") != new_fp.get("theme_id"):
        triggers.append("theme_id_change")
    if old_fp.get("theme_family") != new_fp.get("theme_family"):
        triggers.append("theme_architecture_change")
    old_scripts = old_fp.get("checkout_scripts", [])
    new_scripts = new_fp.get("checkout_scripts", [])
    if any("antigravity" in s for s in old_scripts) and not any("antigravity" in s for s in new_scripts):
        triggers.append("antigravity_script_removed")
    old_sigs = old_fp.get("app_signatures", [])
    new_sigs = new_fp.get("app_signatures", [])
    if "antigravity" in old_sigs and "antigravity" not in new_sigs:
        triggers.append("antigravity_signature_removed")
    if old_fp.get("checkout_providers") != new_fp.get("checkout_providers"):
        triggers.append("checkout_providers_changed")
    competitors_new = [s for s in new_scripts if s not in old_scripts]
    if competitors_new:
        triggers.append(f"new_checkout_script_detected ({', '.join(competitors_new)})")
    return triggers


def involves_flexype_churn(old_fp: dict, new_fp: dict) -> bool:
    old_scripts = old_fp.get("checkout_scripts", []) or []
    new_scripts = new_fp.get("checkout_scripts", []) or []
    old_providers = old_fp.get("checkout_providers", []) or []
    new_providers = new_fp.get("checkout_providers", []) or []
    had = (any("flexype" in str(s).lower() for s in old_scripts) or
           any("flexype" in str(p).lower() for p in old_providers))
    has = (any("flexype" in str(s).lower() for s in new_scripts) or
           any("flexype" in str(p).lower() for p in new_providers))
    return had != has


# ─────────────────────────────────────────────
#  BROWSER POOL (recyclable)
# ─────────────────────────────────────────────

class BrowserPool:
    def __init__(self, recycle_every: int = BROWSER_RECYCLE_EVERY):
        self._p = None
        self._browser = None
        self._count = 0
        self._recycle_every = recycle_every

    async def start(self):
        self._p = await async_playwright().start()
        self._browser = await self._launch()

    async def _launch(self):
        return await self._p.chromium.launch(
            headless=True, args=['--disable-dev-shm-usage', '--no-sandbox'])

    async def get(self):
        if self._browser is None:
            self._browser = await self._launch()
        return self._browser

    async def tick(self):
        self._count += 1
        if self._count % self._recycle_every == 0:
            print(f"[Browser] Recycling after {self._count} scans.")
            try:
                await self._browser.close()
            except Exception:
                pass
            self._browser = await self._launch()

    async def stop(self):
        try:
            if self._browser:
                await self._browser.close()
        finally:
            if self._p:
                await self._p.stop()


# ─────────────────────────────────────────────
#  12H CHEAP SCAN
# ─────────────────────────────────────────────

async def scan_all_merchants_fingerprint():
    sync_domains_from_csv()
    cleanup_old_records()

    all_merchants = list(merchants.find(active_merchant_filter(), {"domain": 1}))
    all_merchants = [m for m in all_merchants if "test-store-1.com" not in m["domain"]]
    total_count = len(all_merchants)
    print(f"Starting Lite Fingerprint Scan for {total_count} active merchants "
          f"(concurrency={CHEAP_CONCURRENCY}, throttle_base={THROTTLE.base_rate}/s)...")

    webhook_url = os.getenv("SLACK_WEBHOOK_URL")
    if webhook_url:
        try:
            requests.post(webhook_url, json={"text": f"🔄 *Starting Lite Fingerprint Scan* for {total_count} merchants..."}, timeout=10)
        except Exception:
            pass

    scanner = CheapScanner()
    existing_map = {d["merchant"]: d for d in merchant_fingerprints.find({})}

    sem = asyncio.Semaphore(CHEAP_CONCURRENCY)
    counters = {"success": 0, "failed": 0, "changed": 0, "escalated": 0,
                "rate_limited": 0, "rl_recovered": 0, "deactivated": 0, "processed": 0}
    changes_list = []
    escalation_queue = []
    lock = asyncio.Lock()

    async def handle_fp(domain, fp):
        """Process a GOOD fingerprint dict (post rate-limit/dead handling)."""
        await asyncio.to_thread(mark_active, domain)
        async with lock:
            counters["success"] += 1

        new_hash = calculate_hash(fp)
        existing = existing_map.get(domain)

        if not existing:
            await asyncio.to_thread(
                merchant_fingerprints.update_one,
                {"merchant": domain},
                {"$set": {
                    "merchant": domain,
                    "fingerprint_hash": new_hash,
                    "checkout_fp_hash": new_hash,
                    "theme_id": fp["theme_id"], "theme_family": fp["theme_family"],
                    "checkout_providers": fp["checkout_providers"],
                    "checkout_scripts": fp["checkout_scripts"],
                    "app_signatures": fp["app_signatures"],
                    "last_scanned": datetime.utcnow()
                }}, upsert=True)
        else:
            old_hash = existing.get("fingerprint_hash") or existing.get("checkout_fp_hash") or ""
            if old_hash == new_hash:
                await asyncio.to_thread(
                    merchant_fingerprints.update_one,
                    {"merchant": domain},
                    {"$set": {"last_scanned": datetime.utcnow()}})
            else:
                old_fp = {
                    "theme_id": existing.get("theme_id", "unknown"),
                    "theme_family": existing.get("theme_family", "unknown"),
                    "checkout_providers": existing.get("checkout_providers", []),
                    "checkout_scripts": existing.get("checkout_scripts", []),
                    "app_signatures": existing.get("app_signatures", [])
                }
                if involves_flexype_churn(old_fp, fp):
                    async with lock:
                        counters["changed"] += 1
                        changes_list.append(domain)
                    triggers = analyze_diff_and_escalate(domain, old_fp, fp)
                    if triggers:
                        async with lock:
                            counters["escalated"] += 1
                        escalation_queue.append((domain, ", ".join(triggers)))

                await asyncio.to_thread(
                    merchant_fingerprints.update_one,
                    {"merchant": domain},
                    {"$set": {
                        "fingerprint_hash": new_hash,
                        "checkout_fp_hash": new_hash,
                        "theme_id": fp["theme_id"], "theme_family": fp["theme_family"],
                        "checkout_providers": fp["checkout_providers"],
                        "checkout_scripts": fp["checkout_scripts"],
                        "app_signatures": fp["app_signatures"],
                        "last_scanned": datetime.utcnow()
                    }})

    async def scan_single(domain):
        await asyncio.sleep(random.uniform(0.2, 1.5))  # light initial spread; THROTTLE does the real pacing

        # DNS pre-flight: dead-DNS records a soft dead reading, never a delete.
        if not load_mock_config(domain) and not await asyncio.to_thread(domain_resolves, domain):
            deact = await asyncio.to_thread(mark_dead_reading, domain, "DNS does not resolve")
            async with lock:
                if deact:
                    counters["deactivated"] += 1
                counters["processed"] += 1
            return

        rl_hit = False
        try:
            # Rate-limit retry loop: re-queue this ONE domain with exponential
            # backoff instead of dropping it. The global THROTTLE has already
            # widened the gap for everyone, so these retries land softer.
            for attempt in range(RL_MAX_RETRIES + 1):
                async with sem:
                    fp = await asyncio.to_thread(scanner.scan_merchant, domain)

                if fp and fp.get("rate_limited"):
                    rl_hit = True
                    if attempt < RL_MAX_RETRIES:
                        backoff = min(RL_BACKOFF_CAP, RL_BACKOFF_BASE * (2 ** attempt)) + random.uniform(0.5, 3.0)
                        await asyncio.sleep(backoff)
                        continue
                    # Exhausted retries — count it, don't fail the run.
                    async with lock:
                        counters["rate_limited"] += 1
                        counters["processed"] += 1
                    return

                if fp and fp.get("dead"):
                    deact = await asyncio.to_thread(mark_dead_reading, domain, fp["reason"])
                    async with lock:
                        if deact:
                            counters["deactivated"] += 1
                        counters["processed"] += 1
                    return

                if not fp:
                    deact = await asyncio.to_thread(mark_dead_reading, domain, "empty fetch")
                    async with lock:
                        if deact:
                            counters["deactivated"] += 1
                        counters["processed"] += 1
                    return

                # Good fingerprint.
                if rl_hit:
                    async with lock:
                        counters["rl_recovered"] += 1
                await handle_fp(domain, fp)
                break

            async with lock:
                counters["processed"] += 1
                if counters["processed"] % 25 == 0 or counters["processed"] == total_count:
                    pct = counters["processed"] / total_count * 100
                    print(f"Lite Scan Progress: {pct:.1f}% ({counters['processed']}/{total_count})  "
                          f"[rl={counters['rate_limited']} recovered={counters['rl_recovered']} "
                          f"rate={THROTTLE.rate:.1f}/s]")
        except Exception as e:
            print(f"\n[Error] {domain}: {e}")
            async with lock:
                counters["processed"] += 1

    await asyncio.gather(*[scan_single(m["domain"]) for m in all_merchants])

    if escalation_queue:
        print(f"\nEscalating {len(escalation_queue)} FlexyPe-churn merchants to Playwright...")
        pool = BrowserPool()
        await pool.start()
        psem = asyncio.Semaphore(PLAYWRIGHT_CONCURRENCY)

        async def escalate(domain, reason):
            async with psem:
                browser = await pool.get()
                await trigger_playwright_check(domain, reason, browser=browser)
                await pool.tick()

        try:
            await asyncio.gather(*[escalate(d, r) for d, r in escalation_queue])
        finally:
            await pool.stop()

    summary_text = (
        f"✅ *Lite Fingerprint Scan Completed*\n"
        f"• *Total Active:* {total_count}\n"
        f"• *Successful:* {counters['success']}\n"
        f"• *Rate-Limited (gave up after retries):* {counters['rate_limited']}\n"
        f"• *Rate-Limited then Recovered:* {counters['rl_recovered']}\n"
        f"• *Deactivated (confirmed dead):* {counters['deactivated']}\n"
        f"• *FlexyPe Fingerprint Changes:* {counters['changed']}\n"
        f"• *Escalations:* {counters['escalated']}\n"
    )
    if changes_list:
        formatted = ", ".join(f"`{d}`" for d in changes_list[:10])
        summary_text += f"• *Changes:* {formatted}"
        if len(changes_list) > 10:
            summary_text += f" and {len(changes_list) - 10} more"
    print("\n" + summary_text.replace("*", ""))
    if webhook_url:
        try:
            requests.post(webhook_url, json={"text": summary_text}, timeout=10)
        except Exception as e:
            print(f"[Slack Error] {e}")

    sheet_id = os.getenv("GOOGLE_SHEET_ID")
    if sheet_id:
        try:
            from sync_to_sheets import sync_merchants
            print("\n[Google Sheets] Starting auto-sync...")
            sync_merchants()
        except Exception as e:
            print(f"\n[Google Sheets Error] Auto-sync failed: {e}")


# ─────────────────────────────────────────────
#  24H FULL PLAYWRIGHT SWEEP
# ─────────────────────────────────────────────

async def run_daily_playwright_verification():
    cleanup_old_records()
    all_merchants = list(merchants.find(active_merchant_filter(), {"domain": 1}))
    total_count = len(all_merchants)
    print(f"Starting daily full Playwright verification for {total_count} active merchants "
          f"(concurrency={PLAYWRIGHT_CONCURRENCY})...")

    pool = BrowserPool()
    await pool.start()
    psem = asyncio.Semaphore(PLAYWRIGHT_CONCURRENCY)
    counters = {"success": 0, "failed": 0}

    async def verify(m):
        domain = m["domain"]
        async with psem:
            browser = await pool.get()
            try:
                await trigger_playwright_check(domain, "daily_verification", browser=browser)
                counters["success"] += 1
            except Exception:
                counters["failed"] += 1
            finally:
                await pool.tick()

    try:
        await asyncio.gather(*[verify(m) for m in all_merchants])
    finally:
        await pool.stop()

    webhook_url = os.getenv("SLACK_WEBHOOK_URL")
    if webhook_url:
        summary_text = (
            f"🚀 *Full Playwright Sweep Completed*\n"
            f"• *Total Active:* {total_count}\n"
            f"• *Successful Runs:* {counters['success']}\n"
            f"• *Failed Runs:* {counters['failed']}\n"
            f"_(Confirmed checkout changes are alerted individually by the state machine.)_"
        )
        try:
            requests.post(webhook_url, json={"text": summary_text}, timeout=10)
        except Exception as e:
            print(f"[Slack Error] {e}")

    sheet_id = os.getenv("GOOGLE_SHEET_ID")
    if sheet_id:
        try:
            from sync_to_sheets import sync_merchants
            print("\n[Google Sheets] Starting auto-sync...")
            sync_merchants()
        except Exception as e:
            print(f"\n[Google Sheets Error] Auto-sync failed: {e}")


def main():
    parser = argparse.ArgumentParser(description="FlexyPe Outreach Merchant Monitoring System")
    parser.add_argument("--test", help="Test cheap scan on a single domain")
    parser.add_argument("--cron-12h", action="store_true", help="12-hour cheap fingerprint check")
    parser.add_argument("--cron-24h", action="store_true", help="24-hour Playwright validation check")
    args = parser.parse_args()

    if args.test:
        scanner = CheapScanner()
        print(f"Scanning domain: {args.test}...")
        fp = scanner.scan_merchant(args.test)
        print("Extracted Fingerprint:")
        print(json.dumps(fp, indent=2))
        if fp and not fp.get("dead") and not fp.get("rate_limited"):
            print("Fingerprint Hash:", calculate_hash(fp))
    elif args.cron_12h:
        asyncio.run(scan_all_merchants_fingerprint())
    elif args.cron_24h:
        asyncio.run(run_daily_playwright_verification())
    else:
        parser.print_help()


if __name__ == "__main__":
    main()