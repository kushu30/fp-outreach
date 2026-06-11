#!/usr/bin/env python3
import re
import json
import asyncio
import pandas as pd
import time
import hashlib
import requests
import logging
import sys
import os
from datetime import datetime
from bs4 import BeautifulSoup
from playwright.async_api import async_playwright
from asyncio import Semaphore
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from urllib.parse import urlparse, urljoin, unquote
from db import merchants

# ─────────────────────────────────────────────
#  LOGGING SETUP
# ─────────────────────────────────────────────

LOG_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "logs"
)
os.makedirs(LOG_DIR, exist_ok=True)

FAILED_LOG = os.path.join(LOG_DIR, "failed_scans.log")

log_filename = os.path.join(
    LOG_DIR,
    f'scan_{datetime.now().strftime("%Y%m%d_%H%M%S")}.log'
)

# ── file handler: full debug ──
file_handler = logging.FileHandler(log_filename, encoding='utf-8')
file_handler.setLevel(logging.DEBUG)
file_handler.setFormatter(logging.Formatter(
    '%(asctime)s  %(levelname)-8s  %(message)s',
    datefmt='%H:%M:%S'
))

# ── console handler: clean, info-only ──
class _ConsoleFormatter(logging.Formatter):
    """Compact, symbol-prefixed console output."""
    SYMBOLS = {
        logging.DEBUG:    '\033[90m  ·\033[0m',
        logging.INFO:     '\033[36m  →\033[0m',
        logging.WARNING:  '\033[33m  !\033[0m',
        logging.ERROR:    '\033[31m  ✗\033[0m',
        logging.CRITICAL: '\033[35m  ✖\033[0m',
    }
    def format(self, record):
        sym = self.SYMBOLS.get(record.levelno, '   ')
        return f'{sym}  {record.getMessage()}'

console_handler = logging.StreamHandler(sys.stdout)
console_handler.setLevel(logging.INFO)
console_handler.setFormatter(_ConsoleFormatter())

logging.basicConfig(level=logging.DEBUG, handlers=[file_handler, console_handler])
logger = logging.getLogger('scanner')

# Silence noisy third-party loggers
for _noisy in ('urllib3', 'asyncio', 'playwright', 'websockets'):
    logging.getLogger(_noisy).setLevel(logging.WARNING)


# ─────────────────────────────────────────────
#  CONFIG
# ─────────────────────────────────────────────

INPUT_CSV  = "../data/domains.csv"
OUTPUT_CSV  = "../data/results.csv"
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
    "FlexyPe":    ["flexype", "grade.flexype"],
    "GoKwik":     ["gokwik", "kwikcheckout", "gokwik.co"],
    "Shopflo":    ["shopflo", "checkout.shopflo.com"],
    "Razorpay":   ["razorpay", "rzp", "magic.razorpay"],
    "Fastrr":     ["fastrr", "sr-checkout", "shiprocket"],
    "ecom360":    ["cashfree", "cashfree.js"],
    "Simpl":      ["getsimpl", "simpl-checkout"],
    "Paytm":      ["paytm.com/checkout"],
    "Instamojo":  ["instamojo"]
}

TECH_STACK_PATTERNS = {
    "Klaviyo":           ["klaviyo"],
    "Judge.me":          ["judge.me"],
    "Yotpo":             ["yotpo"],
    "Meta Pixel":        ["fbq(", "connect.facebook.net"],
    "Google Analytics":  ["gtag(", "google-analytics"],
    "Hotjar":            ["hotjar"],
    "Microsoft Clarity": ["clarity.ms"],
    "Intercom":          ["intercom"],
    "Gorgias":           ["gorgias"],
    "Recharge":          ["recharge"]
}


# ─────────────────────────────────────────────
#  CONTACT EXTRACTION HELPERS
# ─────────────────────────────────────────────

# ── Email ──
_EMAIL_RE = re.compile(
    r'(?<![=\'\"/])(?<!\w)'          # not preceded by =, quote, slash
    r'[A-Za-z0-9._%+\-]{2,}'
    r'@'
    r'[A-Za-z0-9.\-]+'
    r'\.[A-Za-z]{2,}',
    re.IGNORECASE
)

_BAD_EMAIL_DOMAINS  = {'example.com', 'test.com', 'domain.com', 'email.com',
                       'yoursite.com', 'sentry.io', 'wixpress.com'}
_BAD_EMAIL_PATTERNS = ['.png', '.jpg', '.jpeg', '.gif', '.svg', '.webp',
                       '.js', '.css', '.woff', 'noreply', 'no-reply',
                       '@2x', '@3x', 'schema.org', 'w3.org']
_PRIORITY_PREFIXES  = ['founder', 'ceo', 'partnerships', 'business', 'sales',
                       'marketing', 'growth', 'hello', 'care', 'support',
                       'info', 'contact']

def _clean_emails(raw: set) -> list:
    out = set()
    for e in raw:
        e = e.strip().lower().rstrip('.,;)')
        if len(e) < 6 or '@' not in e:
            continue
        local, domain = e.rsplit('@', 1)
        if domain in _BAD_EMAIL_DOMAINS:
            continue
        if any(p in e for p in _BAD_EMAIL_PATTERNS):
            continue
        if len(local) < 2 or len(domain.split('.')[0]) < 2:
            continue
        out.add(e)
    # rank: priority prefixes first
    ranked = []
    for prefix in _PRIORITY_PREFIXES:
        for e in sorted(out):
            if e.startswith(prefix + '@') and e not in ranked:
                ranked.append(e)
    for e in sorted(out):
        if e not in ranked:
            ranked.append(e)
    return ranked[:5]

def extract_emails(html: str) -> list:
    soup = BeautifulSoup(html, 'html.parser')
    raw = set()

    # 1. mailto: links (most reliable)
    for a in soup.find_all('a', href=True):
        href = a['href']
        if href.lower().startswith('mailto:'):
            addr = href[7:].split('?')[0].strip().lower()
            raw.add(addr)

    # 2. visible text + regex (skip script/style)
    for tag in soup(['script', 'style', 'noscript']):
        tag.decompose()
    text = soup.get_text(' ')
    raw.update(_EMAIL_RE.findall(text))

    # 3. raw html (catches data- attributes, JSON-LD, etc.)
    raw.update(_EMAIL_RE.findall(html))

    return _clean_emails(raw)


# ── Phone numbers ──
# Matches Indian and international mobile/landline numbers
_PHONE_RE = re.compile(
    r'(?:(?:\+|00)(?:91|1|44|61|971|65|60|66|880|94|977|92|62|63|84|66|82|81|86)\s*[-.\s]?)?'  # optional country code
    r'(?:\(?\d{2,4}\)?\s*[-.\s]?)?'     # optional area code
    r'\d{3,5}'                           # first segment
    r'[-.\s]?\d{3,5}'                    # second segment
    r'(?:[-.\s]?\d{2,4})?',             # optional third segment
    re.IGNORECASE
)
# Stricter validation after extraction
_PHONE_VALID = re.compile(r'^\+?[\d\s\-().]{7,17}$')
_PHONE_JUNK  = re.compile(r'(19|20)\d{2}')  # looks like a year

def extract_phones(html: str) -> list:
    soup = BeautifulSoup(html, 'html.parser')
    raw = set()

    # 1. tel: links
    for a in soup.find_all('a', href=True):
        href = a['href']
        if href.lower().startswith('tel:'):
            number = href[4:].strip()
            raw.add(number)

    # 2. schema.org telephone in JSON-LD
    for script in soup.find_all('script', type='application/ld+json'):
        try:
            data = json.loads(script.string or '')
            if isinstance(data, dict):
                for key in ('telephone', 'phone', 'faxNumber'):
                    if key in data:
                        raw.add(str(data[key]))
        except Exception:
            pass

    # 3. regex over visible text
    for tag in soup(['script', 'style', 'noscript']):
        tag.decompose()
    text = soup.get_text(' ')
    for m in _PHONE_RE.findall(text):
        digits = re.sub(r'\D', '', m)
        if 7 <= len(digits) <= 15:
            raw.add(m.strip())

    # Deduplicate and validate
    cleaned = []
    seen_digits = set()
    for p in sorted(raw, key=lambda x: -len(x)):
        digits = re.sub(r'\D', '', p)
        if digits in seen_digits:
            continue
        if not _PHONE_VALID.match(p):
            continue
        if _PHONE_JUNK.search(p) and len(digits) <= 6:
            continue
        if len(digits) < 7:
            continue
        seen_digits.add(digits)
        cleaned.append(p.strip())

    return cleaned[:5]


# ── WhatsApp ──
_WA_LINK_RE = re.compile(
    r'https?://(?:api\.whatsapp\.com/send|wa\.me|web\.whatsapp\.com/send)'
    r'[^\s\'"<>]*',
    re.IGNORECASE
)
_WA_PHONE_RE = re.compile(r'[\+\d][\d\s\-]{6,}')

def extract_whatsapp(html: str) -> dict:
    """Return {'link': str, 'number': str}"""
    soup = BeautifulSoup(html, 'html.parser')
    result = {'link': '', 'number': ''}

    # 1. Check <a href> tags
    for a in soup.find_all('a', href=True):
        href = a['href']
        if _WA_LINK_RE.match(href):
            result['link'] = href
            # extract phone from link
            phone_match = re.search(r'(?:phone|to)=(\+?[\d]+)', href, re.IGNORECASE)
            if phone_match:
                result['number'] = '+' + phone_match.group(1).lstrip('+')
            break

    # 2. Regex over full HTML if not found
    if not result['link']:
        m = _WA_LINK_RE.search(html)
        if m:
            result['link'] = m.group(0)
            phone_match = re.search(r'(?:phone|to)=(\+?[\d]+)', m.group(0), re.IGNORECASE)
            if phone_match:
                result['number'] = '+' + phone_match.group(1).lstrip('+')

    return result


# ── MyShopify domain ──
_MYSHOPIFY_RE = re.compile(
    r'([\w\-]+\.myshopify\.com)',
    re.IGNORECASE
)

def extract_myshopify(html: str) -> str:
    m = _MYSHOPIFY_RE.search(html)
    return m.group(1).lower() if m else ''


# ─────────────────────────────────────────────
#  SOURCE DETECTOR  (requests-based)
# ─────────────────────────────────────────────

def normalize_url(domain: str) -> str:
    domain = str(domain).strip().lower()
    if not domain.startswith(('http://', 'https://')):
        return f'https://{domain}'
    return domain


class SourceDetector:
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update(HEADERS)
        retry = Retry(total=2, backoff_factor=0.5, status_forcelist=[429, 500, 502, 503, 504])
        adapter = HTTPAdapter(pool_connections=10, pool_maxsize=10, max_retries=retry)
        self.session.mount('http://', adapter)
        self.session.mount('https://', adapter)

    def fetch_html(self, url: str, timeout: int = 10):
        try:
            r = self.session.get(url, timeout=timeout, allow_redirects=True)
            if r.status_code < 400:
                return r.text, r.url
        except Exception as e:
            logger.debug(f'Fetch failed {url}: {str(e)[:60]}')
        return '', url

    # ── detectors ──

    def detect_historical(self, html: str, script_urls: list) -> list:
        detected = set()
        corpus = html.lower() + ' ' + ' '.join(script_urls).lower()
        for provider, patterns in HISTORICAL_PATTERNS.items():
            if any(p.lower() in corpus for p in patterns):
                detected.add(provider)
        return sorted(detected)

    def detect_tech_stack(self, html: str) -> list:
        hl = html.lower()
        return [tech for tech, patterns in TECH_STACK_PATTERNS.items()
                if any(p.lower() in hl for p in patterns)]

    def detect_social(self, html: str) -> dict:
        social = {k: '' for k in ('linkedin', 'instagram', 'facebook', 'twitter', 'youtube')}
        soup = BeautifulSoup(html, 'html.parser')
        for a in soup.find_all('a', href=True):
            h = a['href'].lower()
            if 'linkedin.com/company' in h and not social['linkedin']:
                social['linkedin'] = a['href']
            elif 'instagram.com' in h and not social['instagram']:
                social['instagram'] = a['href']
            elif 'facebook.com' in h and not social['facebook']:
                social['facebook'] = a['href']
            elif ('twitter.com' in h or 'x.com' in h) and not social['twitter']:
                social['twitter'] = a['href']
            elif 'youtube.com' in h and not social['youtube']:
                social['youtube'] = a['href']
        return social

    def detect_shopify(self, html: str) -> bool:
        hl = html.lower()
        return any(p in hl for p in ('shopify', '.myshopify.com', 'cdn.shopify.com'))

    def get_title_meta(self, html: str):
        soup = BeautifulSoup(html, 'html.parser')
        title = soup.find('title')
        title_text = (title.text.strip()[:200] if title else '')
        meta = soup.find('meta', attrs={'name': 'description'})
        desc = (meta.get('content', '')[:200] if meta else '')
        return title_text, desc

    def extract_script_urls(self, html: str) -> list:
        soup = BeautifulSoup(html, 'html.parser')
        return [s['src'] for s in soup.find_all('script', src=True)]

    def scan(self, domain: str) -> dict:
        base_url = normalize_url(domain)
        html, _ = self.fetch_html(base_url)

        empty = {
            'historical_checkouts': [], 'emails': [], 'phone_numbers': [],
            'whatsapp': {'link': '', 'number': ''},
            'myshopify_domain': '',
            'socials': {k: '' for k in ('linkedin', 'instagram', 'facebook', 'twitter', 'youtube')},
            'tech_stack': [], 'shopify': False, 'title': '', 'description': '', 'page_hash': ''
        }
        if not html or len(html) < 100:
            logger.warning(f'Empty or too-short page: {domain}')
            return empty

        scripts = self.extract_script_urls(html)

        return {
            'historical_checkouts': self.detect_historical(html, scripts),
            'emails':               extract_emails(html),
            'phone_numbers':        extract_phones(html),
            'whatsapp':             extract_whatsapp(html),
            'myshopify_domain':     extract_myshopify(html),
            'socials':              self.detect_social(html),
            'tech_stack':           self.detect_tech_stack(html),
            'shopify':              self.detect_shopify(html),
            'title':                self.get_title_meta(html)[0],
            'description':          self.get_title_meta(html)[1],
            'page_hash':            hashlib.md5(html.encode()).hexdigest()[:16],
        }


# ─────────────────────────────────────────────
#  PLAYWRIGHT DETECTOR  (live network)
# ─────────────────────────────────────────────

class PlaywrightDetector:
    def __init__(self, browser):
        self.browser = browser

    async def scan(self, domain: str) -> dict:
        result = {
            'live_checkout': None, 'live_confidence': 0,
            'live_evidence': [], 'has_kwikpass': False,
            'kwikpass_evidence': [], 'all_network_requests': []
        }
        context = page = None
        try:
            base_url = normalize_url(domain)
            context = await self.browser.new_context(
                user_agent=HEADERS['User-Agent'],
                viewport={'width': 1280, 'height': 720},
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
                nonlocal detected_provider, detected_evidence, detected_confidence
                nonlocal has_kwikpass, kwikpass_evidence
                url = request.url.lower()
                all_requests.append(request.url)

                if 'gkx.gokwik.co' in url or 'kwikpass' in url:
                    has_kwikpass = True
                    kwikpass_evidence.append(request.url)
                    return

                if detected_provider:
                    return

                for name, data in LIVE_PROVIDER_PATTERNS.items():
                    for pattern in data['patterns']:
                        if pattern in url:
                            detected_provider = name
                            detected_evidence.append(request.url)
                            detected_confidence = (
                                95 if pattern in data.get('required_for_live', []) else 80
                            )
                            logger.info(f'Live detected [{name}]  {pattern}')
                            return

            page.on('request', on_request)
            await page.route(
                '**/*.{png,jpg,jpeg,gif,webp,svg,ico,woff,woff2,ttf,mp4,mp3,pdf}',
                lambda r: r.abort()
            )

            try:
                await page.goto(base_url, wait_until='commit', timeout=10000)
                await asyncio.sleep(4)
            except Exception as e:
                logger.warning(f'Navigation issue {domain}: {str(e)[:60]}')
                await asyncio.sleep(2)

            await asyncio.sleep(1)

            result.update({
                'live_checkout':        detected_provider,
                'live_confidence':      detected_confidence,
                'live_evidence':        detected_evidence,
                'has_kwikpass':         has_kwikpass,
                'kwikpass_evidence':    kwikpass_evidence,
                'all_network_requests': all_requests[:20],
            })

        except Exception as e:
            logger.error(f'Playwright error {domain}: {str(e)[:100]}')
        finally:
            if page:    await page.close()
            if context: await context.close()

        return result


# ─────────────────────────────────────────────
#  SCORING
# ─────────────────────────────────────────────

class ScoringEngine:
    @staticmethod
    def calculate_score(data: dict) -> int:
        score = 0
        if data.get('shopify'):                         score += 10
        if data.get('emails'):                          score += 10
        if data.get('phone_numbers'):                   score += 5
        if data.get('whatsapp', {}).get('number'):      score += 5

        s = data.get('socials', {})
        score += sum(5 for k in ('linkedin', 'instagram', 'facebook') if s.get(k))

        hc = len(data.get('historical_checkouts', []))
        score += {0: 0, 1: 20, 2: 30}.get(hc, 40)

        if data.get('live_checkout'):
            score += LIVE_PROVIDER_PATTERNS.get(data['live_checkout'], {}).get('score', 40)
        if data.get('has_kwikpass'):    score += 10
        score += min(len(data.get('tech_stack', [])), 5)

        return min(score, 100)

    @staticmethod
    def get_priority(score: int) -> str:
        if score >= 80: return 'CRITICAL'
        if score >= 65: return 'HIGH'
        if score >= 50: return 'MEDIUM'
        return 'LOW'


# ─────────────────────────────────────────────
#  PROGRESS BAR
# ─────────────────────────────────────────────

class ProgressBar:
    def __init__(self, total: int, width: int = 36):
        self.total = total
        self.width = width
        self.start = time.time()

    def update(self, done: int, live: int = 0, hist: int = 0):
        elapsed = time.time() - self.start
        rps     = done / elapsed if elapsed > 0 else 0
        eta     = (self.total - done) / rps if rps > 0 else 0
        eta_s   = f'{eta:.0f}s' if eta < 60 else (f'{eta/60:.1f}m' if eta < 3600 else f'{eta/3600:.1f}h')
        pct     = done / self.total
        filled  = int(self.width * pct)
        bar     = '█' * filled + '░' * (self.width - filled)
        sys.stdout.write(
            f'\r  [{bar}] {pct*100:4.0f}%  {done}/{self.total}  '
            f'live={live}  hist={hist}  {rps:.1f}/s  ETA {eta_s:<6}'
        )
        sys.stdout.flush()


# ─────────────────────────────────────────────
#  DOMAIN SCAN (combined)
# ─────────────────────────────────────────────

async def scan_domain(browser, domain: str, semaphore: Semaphore) -> dict:
    async with semaphore:
        start = time.time()
        logger.info(f'Scanning  {domain}')

        src  = SourceDetector().scan(domain)
        live = await PlaywrightDetector(browser).scan(domain)

        wa = src.get('whatsapp', {})

        result = {
            'domain':               domain,
            'shopify':              src['shopify'],
            'live_checkout':        live['live_checkout'],
            'live_confidence':      live['live_confidence'],
            'live_evidence':        live['live_evidence'],
            'historical_checkouts': src['historical_checkouts'],
            'has_kwikpass':         live['has_kwikpass'],
            'kwikpass_evidence':    live['kwikpass_evidence'],
            'emails':               src['emails'],
            'phone_numbers':        src['phone_numbers'],
            'whatsapp_link':        wa.get('link', ''),
            'whatsapp_number':      wa.get('number', ''),
            'myshopify_domain':     src['myshopify_domain'],
            'socials':              src['socials'],
            'tech_stack':           src['tech_stack'],
            'title':                src['title'],
            'description':          src['description'],
            'page_hash':            src['page_hash'],
            'lead_score':           0,
            'priority':             '',
            'last_scan':            time.strftime('%Y-%m-%d'),
            'scan_duration':        0,
            'status':               'Not Contacted',
            'notes':                '',
        }
        result['lead_score'] = ScoringEngine.calculate_score(result)
        result['priority']   = ScoringEngine.get_priority(result['lead_score'])
        result['scan_duration'] = round(time.time() - start, 2)

        provider = result['live_checkout'] or ('historical:' + ','.join(result['historical_checkouts'][:2]) if result['historical_checkouts'] else 'none')
        logger.info(f'Done  {domain}  score={result["lead_score"]}  checkout={provider}  {result["scan_duration"]}s')
        return result


# ─────────────────────────────────────────────
#  RUNNER
# ─────────────────────────────────────────────

async def run_scanner(domains: list, max_concurrent: int = 10) -> list:
    results   = []
    semaphore = Semaphore(max_concurrent)

    logger.info(f'Starting scan  domains={len(domains)}  concurrency={max_concurrent}')

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=['--disable-dev-shm-usage', '--no-sandbox']
        )
        try:
            tasks    = [scan_domain(browser, d, semaphore) for d in domains]
            total    = len(tasks)
            pbar     = ProgressBar(total)
            done     = 0

            print()
            for coro in asyncio.as_completed(tasks):
                r = await coro
                merchants.update_one(
                    {"domain": r["domain"]},
                    {"$set": r},
                    upsert=True
                )
                results.append(r)
                done += 1
                live_c = sum(1 for x in results if x['live_checkout'])
                hist_c = sum(1 for x in results if x['historical_checkouts'])
                pbar.update(done, live_c, hist_c)
            print('\n')
        finally:
            await browser.close()

    logger.info(f'Scan complete  total={len(results)}')
    return results


# ─────────────────────────────────────────────
#  MAIN
# ─────────────────────────────────────────────

def main():
    print('\n' + '─' * 64)
    print('  FlexyPe  ·  Merchant Intelligence Scanner')
    print('─' * 64 + '\n')

    try:
        df = pd.read_csv(INPUT_CSV)
        domains = df['domain'].dropna().tolist()
        logger.info(f'Loaded {len(domains)} domains from {INPUT_CSV}')
    except FileNotFoundError:
        logger.error(f'Input file not found: {INPUT_CSV}')
        print(f'\n  Error: {INPUT_CSV} not found.')
        print('  Create a CSV with a "domain" column.\n')
        return

    results = asyncio.run(run_scanner(domains, max_concurrent=10))

    # ── flat export ──
    flat = []
    for r in results:
        flat.append({
            'domain':              r['domain'],
            'shopify':             r['shopify'],
            'live_checkout':       r['live_checkout'] or '',
            'live_confidence':     r['live_confidence'],
            'historical_checkouts': ', '.join(r['historical_checkouts']),
            'historical_count':    len(r['historical_checkouts']),
            'has_kwikpass':        r['has_kwikpass'],
            'emails':              ', '.join(r['emails']),
            'phone_numbers':       ', '.join(r.get('phone_numbers', [])),
            'whatsapp_link':       r.get('whatsapp_link', ''),
            'whatsapp_number':     r.get('whatsapp_number', ''),
            'myshopify_domain':    r.get('myshopify_domain', ''),
            'linkedin':            r['socials']['linkedin'],
            'instagram':           r['socials']['instagram'],
            'facebook':            r['socials']['facebook'],
            'twitter':             r['socials']['twitter'],
            'youtube':             r['socials']['youtube'],
            'tech_stack':          ', '.join(r['tech_stack']),
            'title':               r['title'],
            'lead_score':          r['lead_score'],
            'priority':            r['priority'],
            'last_scan':           r['last_scan'],
            'scan_duration':       r['scan_duration'],
            'status':              r['status'],
            'notes':               r['notes'],
        })

    df_out = pd.DataFrame(flat).sort_values('lead_score', ascending=False)
    df_out.to_csv(OUTPUT_CSV, index=False)
    logger.info(f'Saved {OUTPUT_CSV}')

    try:
        df_out.to_excel(OUTPUT_XLSX, index=False)
        logger.info(f'Saved {OUTPUT_XLSX}')
    except Exception as e:
        logger.warning(f'Excel export failed: {e}')

    with open(OUTPUT_JSON, 'w', encoding='utf-8') as f:
        json.dump(results, f, indent=2, ensure_ascii=False)
    logger.info(f'Saved {OUTPUT_JSON}')

    # ── summary ──
    live_c  = sum(1 for r in results if r['live_checkout'])
    hist_c  = sum(1 for r in results if r['historical_checkouts'] and not r['live_checkout'])
    kp_c    = sum(1 for r in results if r['has_kwikpass'])
    email_c = sum(1 for r in results if r['emails'])
    phone_c = sum(1 for r in results if r.get('phone_numbers'))
    wa_c    = sum(1 for r in results if r.get('whatsapp_number'))
    ms_c    = sum(1 for r in results if r.get('myshopify_domain'))

    print('─' * 64)
    print('  SUMMARY')
    print('─' * 64)
    print(f'  Total scanned      {len(domains)}')
    print(f'  Live checkout      {live_c}')
    print(f'  Historical only    {hist_c}')
    print(f'  Kwikpass           {kp_c}')
    print(f'  Emails found       {email_c}')
    print(f'  Phones found       {phone_c}')
    print(f'  WhatsApp found     {wa_c}')
    print(f'  MyShopify found    {ms_c}')
    print('─' * 64)
    print(f'  Log  →  {log_filename}\n')


if __name__ == '__main__':
    main()