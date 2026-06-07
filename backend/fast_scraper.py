import re
import json
import asyncio
import pandas as pd
import time
import hashlib
import requests
import logging
from datetime import datetime
from bs4 import BeautifulSoup, Comment
from playwright.async_api import async_playwright
from asyncio import Semaphore
from urllib.parse import urljoin, urlparse
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import sys
import os

LOG_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'logs'))
os.makedirs(LOG_DIR, exist_ok=True)
log_filename = os.path.join(LOG_DIR, f'scan_{datetime.now().strftime("%Y%m%d_%H%M%S")}.log')
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)s | %(message)s',
    datefmt='%H:%M:%S',
    handlers=[
        logging.FileHandler(log_filename),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

INPUT_CSV = "../data/domains.csv"
OUTPUT_CSV = "../data/results.csv"
OUTPUT_JSON = "../data/results.json"
OUTPUT_XLSX = "../data/results.xlsx"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/137.0.0.0 Safari/537.36"
    )
}

LIVE_PROVIDER_PATTERNS = {
    "FlexyPe": {
        "patterns": [
            "grade.flexype.in/api/v1/metric",
            "static.flexype.in/scripts/flexype"
        ],
        "kwikpass_patterns": [],
        "required_for_live": ["grade.flexype.in/api/v1/metric"],
        "display_name": "FlexyPe",
        "score": 50
    },
    "GoKwik": {
        "patterns": [
            "hits.gokwik.co/api/v1/events",
            "pdp.gokwik.co/v4/build/gokwik.js",
            "pdp.gokwik.co/merchant-integration/build/merchant.integration.js"
        ],
        "kwikpass_patterns": ["gkx.gokwik.co", "kwikpass"],
        "required_for_live": ["hits.gokwik.co/api/v1/events"],
        "display_name": "GoKwik",
        "score": 45
    },
    "Shopflo": {
        "patterns": [
            "public.shopflo.com/heimdall/api/v1/shopflo-health",
            "shopflo.com/api/v1/spark",
            "bridge.shopflo.com/js/shopflo.bundle.js"
        ],
        "kwikpass_patterns": [],
        "required_for_live": ["public.shopflo.com/heimdall"],
        "display_name": "Shopflo",
        "score": 45
    },
    "Razorpay": {
        "patterns": [
            "lumberjack.razorpay.com/v2/m/track",
            "lumberjack.razorpay.com/v1/track",
            "magic-plugins.razorpay.com/shopify/magic-shopify.js"
        ],
        "kwikpass_patterns": [],
        "required_for_live": ["lumberjack.razorpay.com"],
        "display_name": "Razorpay",
        "score": 40
    },
    "Fastrr": {
        "patterns": [
            "uptime2.fastrr.com/fe2",
            "sr-cdn.shiprocket.in/sr-promise/static/iframe.html"
        ],
        "kwikpass_patterns": [],
        "required_for_live": ["uptime2.fastrr.com/fe2"],
        "display_name": "Fastrr",
        "score": 35
    },
    "ecom360": {
        "patterns": [
            "cashfree.js",
            "cashfree.com/web/v3/cashfree.js",
            "cashfree-sdk.js"
        ],
        "kwikpass_patterns": [],
        "required_for_live": ["cashfree.js"],
        "display_name": "ecom360",
        "score": 30
    }
}

HISTORICAL_PATTERNS = {
    "FlexyPe": ["flexype", "grade.flexype"],
    "GoKwik": ["gokwik", "kwikcheckout", "gokwik.co"],
    "Shopflo": ["shopflo", "checkout.shopflo.com"],
    "Razorpay": ["razorpay", "rzp", "magic.razorpay"],
    "Fastrr": ["fastrr", "sr-checkout", "shiprocket"],
    "ecom360": ["cashfree", "cashfree.js"],
    "Simpl": ["getsimpl", "simpl-checkout"],
    "Paytm": ["paytm.com/checkout"],
    "Instamojo": ["instamojo"]
}

TECH_STACK_PATTERNS = {
    "Klaviyo": ["klaviyo"],
    "Judge.me": ["judge.me"],
    "Yotpo": ["yotpo"],
    "Meta Pixel": ["fbq(", "connect.facebook.net"],
    "Google Analytics": ["gtag(", "google-analytics"],
    "Hotjar": ["hotjar"],
    "Microsoft Clarity": ["clarity.ms"],
    "Intercom": ["intercom"],
    "Gorgias": ["gorgias"],
    "Recharge": ["recharge"]
}

EMAIL_REGEX = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", re.IGNORECASE)
PRIORITY_EMAIL_PREFIXES = ["founder", "ceo", "partnerships", "business", "sales", "marketing", "growth", "hello", "care", "support", "info"]
BAD_EMAIL_PATTERNS = ["example.com", "test@", ".png", ".jpg", ".js", "noreply", "no-reply"]

def normalize_url(domain):
    domain = str(domain).strip().lower()
    if not domain.startswith(("http://", "https://")):
        return f"https://{domain}"
    return domain

def clean_emails(emails):
    cleaned = set()
    for email in emails:
        email = email.strip().lower()
        if not any(pattern in email for pattern in BAD_EMAIL_PATTERNS):
            if '@' in email and len(email) > 5 and '.' in email.split('@')[1]:
                cleaned.add(email)
    return cleaned

def extract_emails(html):
    emails = set()
    for email in EMAIL_REGEX.findall(html):
        emails.add(email.lower())
    
    soup = BeautifulSoup(html, "html.parser")
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if href.startswith("mailto:"):
            email = href.replace("mailto:", "").split("?")[0].strip().lower()
            emails.add(email)
    
    return clean_emails(emails)

def rank_emails(emails):
    emails_list = list(emails)
    ranked = []
    for prefix in PRIORITY_EMAIL_PREFIXES:
        for email in emails_list:
            if email.startswith(prefix + "@") and email not in ranked:
                ranked.append(email)
    for email in emails_list:
        if email not in ranked:
            ranked.append(email)
    return ranked[:5]

class ProgressBar:
    def __init__(self, total, width=40):
        self.total = total
        self.width = width
        self.start_time = time.time()
    
    def update(self, completed, live_count=0, historical_count=0):
        elapsed = time.time() - self.start_time
        sites_per_sec = completed / elapsed if elapsed > 0 else 0
        remaining = self.total - completed
        eta_seconds = remaining / sites_per_sec if sites_per_sec > 0 else 0
        
        percent = completed / self.total
        filled = int(self.width * percent)
        bar = '█' * filled + '░' * (self.width - filled)
        
        if eta_seconds < 60:
            eta_str = f"{eta_seconds:.0f}s"
        elif eta_seconds < 3600:
            eta_str = f"{eta_seconds/60:.1f}m"
        else:
            eta_str = f"{eta_seconds/3600:.1f}h"
        
        sys.stdout.write(f"\r[{bar}] {percent*100:5.1f}% | {completed:3}/{self.total} | "
                        f"Live: {live_count:2} | Hist: {historical_count:2} | "
                        f"{sites_per_sec:.2f}/s | ETA: {eta_str:>8}")
        sys.stdout.flush()

class SourceDetector:
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update(HEADERS)
        retry_strategy = Retry(total=2, backoff_factor=0.5, status_forcelist=[429, 500, 502, 503, 504])
        adapter = HTTPAdapter(pool_connections=10, pool_maxsize=10, max_retries=retry_strategy)
        self.session.mount("http://", adapter)
        self.session.mount("https://", adapter)
    
    def fetch_html(self, url, timeout=10):
        try:
            response = self.session.get(url, timeout=timeout, allow_redirects=True)
            if response.status_code < 400:
                return response.text, response.url
        except Exception as e:
            logger.debug(f"Fetch failed for {url}: {str(e)[:50]}")
        return "", url
    
    def detect_historical_providers(self, html, script_urls):
        detected = set()
        html_lower = html.lower()
        all_content = html_lower + " " + " ".join(script_urls).lower()
        
        for provider, patterns in HISTORICAL_PATTERNS.items():
            for pattern in patterns:
                if pattern.lower() in all_content:
                    detected.add(provider)
                    break
        return list(detected)
    
    def detect_tech_stack(self, html):
        detected = []
        html_lower = html.lower()
        for tech, patterns in TECH_STACK_PATTERNS.items():
            if any(p.lower() in html_lower for p in patterns):
                detected.append(tech)
        return detected
    
    def detect_social(self, html):
        social = {"linkedin": "", "instagram": "", "facebook": "", "twitter": "", "youtube": ""}
        soup = BeautifulSoup(html, "html.parser")
        
        for a in soup.find_all("a", href=True):
            href = a["href"].lower()
            if "linkedin.com/company" in href and not social["linkedin"]:
                social["linkedin"] = a["href"]
            elif "instagram.com" in href and not social["instagram"]:
                social["instagram"] = a["href"]
            elif "facebook.com" in href and not social["facebook"]:
                social["facebook"] = a["href"]
            elif ("twitter.com" in href or "x.com" in href) and not social["twitter"]:
                social["twitter"] = a["href"]
            elif "youtube.com" in href and not social["youtube"]:
                social["youtube"] = a["href"]
        
        return social
    
    def detect_shopify(self, html):
        html_lower = html.lower()
        return "shopify" in html_lower or ".myshopify.com" in html_lower or "cdn.shopify.com" in html_lower
    
    def get_title_meta(self, html):
        soup = BeautifulSoup(html, "html.parser")
        title = soup.find("title")
        title_text = title.text.strip()[:200] if title else ""
        
        meta_desc = soup.find("meta", attrs={"name": "description"})
        description = meta_desc.get("content", "")[:200] if meta_desc else ""
        
        return title_text, description
    
    def extract_script_urls(self, html):
        urls = []
        soup = BeautifulSoup(html, "html.parser")
        for script in soup.find_all("script", src=True):
            urls.append(script["src"])
        return urls
    
    def scan(self, domain):
        base_url = normalize_url(domain)
        homepage_html, final_url = self.fetch_html(base_url, timeout=10)
        
        if not homepage_html or len(homepage_html) < 100:
            return {
                "historical_checkouts": [],
                "emails": [],
                "socials": {"linkedin": "", "instagram": "", "facebook": "", "twitter": "", "youtube": ""},
                "tech_stack": [],
                "shopify": False,
                "title": "",
                "description": "",
                "page_hash": ""
            }
        
        script_urls = self.extract_script_urls(homepage_html)
        historical_providers = self.detect_historical_providers(homepage_html, script_urls)
        tech_stack = self.detect_tech_stack(homepage_html)
        socials = self.detect_social(homepage_html)
        shopify = self.detect_shopify(homepage_html)
        emails = extract_emails(homepage_html)
        ranked_emails = rank_emails(emails)
        title, description = self.get_title_meta(homepage_html)
        page_hash = hashlib.md5(homepage_html.encode()).hexdigest()[:16]
        
        return {
            "historical_checkouts": historical_providers,
            "emails": ranked_emails,
            "socials": socials,
            "tech_stack": tech_stack,
            "shopify": shopify,
            "title": title,
            "description": description,
            "page_hash": page_hash
        }

class PlaywrightDetector:
    def __init__(self, browser):
        self.browser = browser
    
    async def scan(self, domain):
        result = {
            "live_checkout": None,
            "live_confidence": 0,
            "live_evidence": [],
            "has_kwikpass": False,
            "kwikpass_evidence": [],
            "all_network_requests": []
        }
        
        context = None
        page = None
        
        try:
            base_url = normalize_url(domain)
            
            context = await self.browser.new_context(
                user_agent=HEADERS["User-Agent"],
                viewport={"width": 1280, "height": 720},
                java_script_enabled=True,
                ignore_https_errors=True
            )
            
            page = await context.new_page()
            
            detected_provider = None
            detected_evidence = []
            detected_confidence = 0
            has_kwikpass = False
            kwikpass_evidence = []
            all_requests = []
            
            def on_request(request):
                nonlocal detected_provider, detected_evidence, detected_confidence, has_kwikpass, kwikpass_evidence
                url = request.url.lower()
                all_requests.append(request.url)
                
                if "gkx.gokwik.co" in url or "kwikpass" in url:
                    has_kwikpass = True
                    kwikpass_evidence.append(request.url)
                    logger.debug(f"  Kwikpass: {url[:80]}")
                    return
                
                for provider_name, provider_data in LIVE_PROVIDER_PATTERNS.items():
                    if detected_provider:
                        break
                    
                    for pattern in provider_data["patterns"]:
                        if pattern in url:
                            detected_provider = provider_name
                            detected_evidence.append(request.url)
                            
                            if pattern in provider_data.get("required_for_live", []):
                                detected_confidence = 95
                            else:
                                detected_confidence = 80
                            
                            logger.info(f"  LIVE {provider_name} detected: {pattern}")
                            break
            
            page.on("request", on_request)
            
            await page.route("**/*.{png,jpg,jpeg,gif,webp,svg,ico,woff,woff2,ttf,mp4,mp3,pdf}", 
                lambda route: route.abort()
            )
            
            logger.info(f"  Loading: {domain}")
            try:
                await page.goto(base_url, wait_until="commit", timeout=10000)
                await asyncio.sleep(4)
                logger.debug(f"  Navigation complete")
            except Exception as e:
                logger.warning(f"  Navigation issue: {str(e)[:50]}")
                await asyncio.sleep(2)
            
            await asyncio.sleep(1)
            
            result["live_checkout"] = detected_provider
            result["live_confidence"] = detected_confidence
            result["live_evidence"] = detected_evidence
            result["has_kwikpass"] = has_kwikpass
            result["kwikpass_evidence"] = kwikpass_evidence
            result["all_network_requests"] = all_requests[:20]
            
            if detected_provider:
                logger.info(f"  FINAL: {domain} -> LIVE: {detected_provider} (conf: {detected_confidence}%)")
            elif has_kwikpass:
                logger.info(f"  {domain} -> Kwikpass only")
            else:
                logger.debug(f"  {domain} -> No live checkout")
            
            await context.close()
            
        except Exception as e:
            logger.error(f"  Error: {str(e)[:100]}")
        finally:
            if page:
                await page.close()
            if context:
                await context.close()
        
        return result

class ScoringEngine:
    @staticmethod
    def calculate_score(data):
        score = 0
        
        if data.get("shopify"):
            score += 10
        
        if data.get("emails") and len(data["emails"]) > 0:
            score += 10
        
        socials = data.get("socials", {})
        if socials.get("linkedin"): score += 5
        if socials.get("instagram"): score += 5
        if socials.get("facebook"): score += 5
        
        historical_count = len(data.get("historical_checkouts", []))
        if historical_count == 1:
            score += 20
        elif historical_count == 2:
            score += 30
        elif historical_count >= 3:
            score += 40
        
        if data.get("live_checkout"):
            provider_score = LIVE_PROVIDER_PATTERNS.get(data["live_checkout"], {}).get("score", 40)
            score += provider_score
        
        if data.get("has_kwikpass"):
            score += 10
        
        tech_count = len(data.get("tech_stack", []))
        score += min(tech_count, 5)
        
        return min(score, 100)
    
    @staticmethod
    def get_priority(score):
        if score >= 80:
            return "CRITICAL"
        elif score >= 65:
            return "HIGH"
        elif score >= 50:
            return "MEDIUM"
        else:
            return "LOW"

async def scan_domain(browser, domain, semaphore, progress, idx, total):
    async with semaphore:
        start_time = time.time()
        
        source_detector = SourceDetector()
        source_data = source_detector.scan(domain)
        
        live_detector = PlaywrightDetector(browser)
        live_data = await live_detector.scan(domain)
        
        result = {
            "domain": domain,
            "shopify": source_data["shopify"],
            "live_checkout": live_data["live_checkout"],
            "live_confidence": live_data["live_confidence"],
            "live_evidence": live_data["live_evidence"],
            "historical_checkouts": source_data["historical_checkouts"],
            "has_kwikpass": live_data["has_kwikpass"],
            "kwikpass_evidence": live_data["kwikpass_evidence"],
            "emails": source_data["emails"],
            "socials": source_data["socials"],
            "tech_stack": source_data["tech_stack"],
            "title": source_data["title"],
            "description": source_data["description"],
            "page_hash": source_data["page_hash"],
            "lead_score": 0,
            "priority": "",
            "last_scan": time.strftime("%Y-%m-%d"),
            "scan_duration": 0,
            "status": "Not Contacted",
            "notes": ""
        }
        
        result["lead_score"] = ScoringEngine.calculate_score(result)
        result["priority"] = ScoringEngine.get_priority(result["lead_score"])
        result["scan_duration"] = round(time.time() - start_time, 2)
        
        return result

async def run_scanner(domains, max_concurrent=10):
    semaphore = Semaphore(max_concurrent)
    results = []
    
    logger.info("=" * 80)
    logger.info("FlexyPe Outreach - Merchant Intelligence Scanner")
    logger.info(f"  Total domains: {len(domains)}")
    logger.info(f"  Concurrent workers: {max_concurrent}")
    logger.info("=" * 80)
    
    async with async_playwright() as p:
        logger.info("Launching Chromium...")
        browser = await p.chromium.launch(
            headless=True,
            args=['--disable-dev-shm-usage', '--no-sandbox']
        )
        
        try:
            tasks = []
            for idx, domain in enumerate(domains):
                task = scan_domain(browser, domain, semaphore, None, idx, len(domains))
                tasks.append(task)
            
            completed = 0
            total = len(tasks)
            start_time = time.time()
            progress_bar = ProgressBar(total, width=40)
            
            print("\n")
            
            for coro in asyncio.as_completed(tasks):
                result = await coro
                results.append(result)
                completed += 1
                
                live_count = sum(1 for r in results if r["live_checkout"])
                hist_count = sum(1 for r in results if r["historical_checkouts"])
                progress_bar.update(completed, live_count, hist_count)
            
            print("\n")
            
            elapsed = time.time() - start_time
            logger.info("=" * 80)
            logger.info(f"Scan completed in {elapsed:.2f} seconds")
            logger.info(f"Average speed: {total/elapsed:.2f} sites/second")
            logger.info("=" * 80)
            
        finally:
            await browser.close()
    
    return results

def main():
    print("\n" + "=" * 80)
    print("FlexyPe Outreach - Merchant Intelligence Platform")
    print("  Live Checkout Detection via Network Interception")
    print("  Historical Detection via Source Code Analysis")
    print("=" * 80 + "\n")
    
    logger.info("Loading domains from CSV...")
    try:
        domains_df = pd.read_csv(INPUT_CSV)
        domains = domains_df["domain"].tolist()
        logger.info(f"Loaded {len(domains)} domains from {INPUT_CSV}")
    except FileNotFoundError:
        logger.error(f"{INPUT_CSV} not found!")
        print(f"\nError: {INPUT_CSV} not found!")
        print("Please create a CSV file with a 'domain' column containing the websites to scan.\n")
        return
    
    results = asyncio.run(run_scanner(domains, max_concurrent=10))
    
    logger.info("Preparing export files...")
    flat_rows = []
    for row in results:
        flat_rows.append({
            "domain": row["domain"],
            "shopify": row["shopify"],
            "live_checkout": row["live_checkout"] or "",
            "live_confidence": row["live_confidence"],
            "historical_checkouts": ", ".join(row["historical_checkouts"]),
            "historical_count": len(row["historical_checkouts"]),
            "has_kwikpass": row["has_kwikpass"],
            "emails": ", ".join(row["emails"]),
            "linkedin": row["socials"]["linkedin"],
            "instagram": row["socials"]["instagram"],
            "facebook": row["socials"]["facebook"],
            "twitter": row["socials"]["twitter"],
            "youtube": row["socials"]["youtube"],
            "tech_stack": ", ".join(row["tech_stack"]),
            "title": row["title"],
            "lead_score": row["lead_score"],
            "priority": row["priority"],
            "last_scan": row["last_scan"],
            "scan_duration": row["scan_duration"],
            "status": row["status"],
            "notes": row["notes"]
        })
    
    csv_df = pd.DataFrame(flat_rows)
    csv_df = csv_df.sort_values(by=["lead_score"], ascending=False)
    
    csv_df.to_csv(OUTPUT_CSV, index=False)
    logger.info(f"Saved {OUTPUT_CSV}")
    
    try:
        csv_df.to_excel(OUTPUT_XLSX, index=False)
        logger.info(f"Saved {OUTPUT_XLSX}")
    except Exception as e:
        logger.warning(f"Could not save Excel: {e}")
    
    with open(OUTPUT_JSON, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)
    logger.info(f"Saved {OUTPUT_JSON}")
    
    flexype_count = sum(1 for r in results if r["live_checkout"] == "FlexyPe")
    live_count = sum(1 for r in results if r["live_checkout"])
    historical_only = sum(1 for r in results if r["historical_checkouts"] and not r["live_checkout"])
    kwikpass_count = sum(1 for r in results if r["has_kwikpass"])
    email_count = sum(1 for r in results if r["emails"])
    
    print("\n" + "=" * 80)
    print("SCAN SUMMARY")
    print("=" * 80)
    print(f"  Total domains scanned:     {len(domains)}")
    print(f"  FlexyPe Outreach live:        {flexype_count}")
    print(f"  Other live checkout:       {live_count - flexype_count}")
    print(f"  Historical only:           {historical_only}")
    print(f"  Kwikpass (login only):     {kwikpass_count}")
    print(f"  Emails found:              {email_count}")
    print("=" * 80)
    
    if flexype_count > 0:
        print("\nFlexyPe Outreach LEADS:")
        print("-" * 60)
        for r in results:
            if r["live_checkout"] == "FlexyPe":
                print(f"  {r['domain']:35} | Score: {r['lead_score']} | Kwikpass: {'Yes' if r['has_kwikpass'] else 'No'}")
    
    print(f"\nLog file: {log_filename}")
    print("\nDone. Open results.html or results.csv to view data.\n")

if __name__ == "__main__":
    main()