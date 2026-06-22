#!/usr/bin/env python3
import re
import json
import socket
import asyncio
import pandas as pd
import time
import hashlib
import requests
import httpx
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
from db import merchants, fingerprint_history

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


def safe_soup(html: str) -> BeautifulSoup:
    if not html:
        return BeautifulSoup("", "html.parser")
    try:
        return BeautifulSoup(html, "html.parser")
    except Exception:
        return BeautifulSoup("", "html.parser")

def domain_resolves(domain: str) -> bool:
    """
    Fast pre-flight DNS check. A domain that cannot resolve will fail in both
    the requests fetch (after pointless retries across 4 URL candidates) and
    Playwright navigation, wasting minutes. Catch it once, in milliseconds.
    Tries both the bare domain and the www. variant.
    """
    d = canonical_domain(domain)
    for host in (d, f'www.{d}'):
        try:
            socket.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP)
            return True
        except socket.gaierror:
            continue
        except Exception:
            # Any non-DNS error — don't block the scan on it.
            return True
    return False

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


def log_failed(domain: str, reason: str):
    """Append a permanently-failed domain to the failed scans log."""
    try:
        with open(FAILED_LOG, 'a', encoding='utf-8') as f:
            f.write(f'{datetime.now().isoformat()}  {domain}  {reason}\n')
    except Exception:
        pass


# ─────────────────────────────────────────────
#  CONFIG
# ─────────────────────────────────────────────

INPUT_CSV  = "../data/domains.csv"
OUTPUT_CSV  = "../data/results.csv"
OUTPUT_JSON = "../data/results.json"
OUTPUT_XLSX = "../data/results.xlsx"

# ── Tunables (accuracy / speed) ──
# Playwright: max time to wait for a definitive checkout signal after navigation.
# A required (95-confidence) pattern resolves the wait almost instantly; this
# cap only applies to pages that never surface a clear signal.
LIVE_SIGNAL_TIMEOUT = 12          # seconds
NAV_TIMEOUT_MS      = 15000       # Playwright goto timeout
# After a definitive hit we still linger briefly so kwikpass / secondary
# requests have a chance to register before we tear the page down.
POST_HIT_GRACE      = 0.6         # seconds
# Absolute wall-clock cap for a single domain's Playwright phase. No page can
# hold a worker longer than this regardless of how it behaves.
DOMAIN_HARD_CAP     = 20          # seconds
# Concurrency for the verification pass. Deterministic ranking (not timing)
# now fixes checkout mis-IDs, so light concurrency here is safe.
VERIFY_CONCURRENCY  = 3

# requests fetch timeouts: (connect, read). Loosened so concurrent load does
# not silently turn a real page into an empty fetch.
HTTP_TIMEOUT = (5, 15)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/137.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Ch-Ua": '"Not A(Brand";v="99", "Google Chrome";v="121", "Chromium";v="121"',
    "Sec-Ch-Ua-Mobile": "?0",
    "Sec-Ch-Ua-Platform": '"macOS"',
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
}


# Per-provider "score" doubles as a deterministic tiebreak when two providers
# match at the same confidence (higher score wins).
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
            "pdp.gokwik.co/merchant-integration/build/merchant.integration.js?v4",
            "pdp.gokwik.co/build/gokwik.js?build=0.1.0",
            "pdp.gokwik.co/v4/assets/icons/gokwik_gif.gif",
            "pdp.gokwik.co/v4/assets/icons/gk-logo.svg",
            "pdp.gokwik.co/sa-login-ui/gokwikSdk.js",
            "pdp.gokwik.co/sa-login-ui/gokwik-sso-sdk.js",
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
            "fastrr-boost-ui.pickrr.com/assets/styles/shopify.css",
            "fastrr-boost-ui.pickrr.com/assets/js/channels/shopify.js",
            "uc.shiprocket.in/v1/track/user",
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
    soup = safe_soup(html)
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
    soup = safe_soup(html)
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
    soup = safe_soup(html)
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
    """Normalize domain to a full URL, prepending www. for bare root domains."""
    domain = str(domain).strip().lower()
    protocol = 'https://'
    host = domain
    if host.startswith('https://'):
        host = host[8:]
    elif host.startswith('http://'):
        host = host[7:]
        protocol = 'http://'
    # Strip trailing slashes/paths for the bare-domain check
    host_only = host.split('/')[0]
    parts = host_only.split('.')
    is_bare = False
    if len(parts) == 2 and not host_only.startswith('www.'):
        is_bare = True
    elif len(parts) == 3 and parts[-2] in ('co', 'com', 'net', 'org', 'gov', 'edu') and not host_only.startswith('www.'):
        is_bare = True
    if is_bare:
        return f'{protocol}www.{host}'
    return f'{protocol}{host}'


def send_slack_notification(domain: str, old_provider: str, new_provider: str, reason: str):
    webhook_url = os.getenv("SLACK_WEBHOOK_URL")
    if not webhook_url:
        logger.warning("[Slack Warning] SLACK_WEBHOOK_URL is not set. Skipping Slack notification.")
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


def delete_old_logs():
    try:
        import glob
        # Find all files in LOG_DIR
        for filepath in glob.glob(os.path.join(LOG_DIR, "*.log")):
            # Don't delete the current log file or failed scans log
            if os.path.basename(filepath) not in (os.path.basename(log_filename), os.path.basename(FAILED_LOG)):
                try:
                    os.remove(filepath)
                except Exception:
                    pass
        print("[Logs] Deleted old log files.")
    except Exception as e:
        logger.warning(f"Failed to delete old logs: {e}")



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


class SourceDetector:
    def __init__(self):
        self.client = httpx.Client(
            headers=HEADERS,
            timeout=httpx.Timeout(15.0, connect=5.0),
            verify=False,
            follow_redirects=True
        )

    def fetch_html(self, url: str, timeout=None):
        if timeout is None:
            to = httpx.Timeout(15.0, connect=5.0)
        elif isinstance(timeout, tuple):
            to = httpx.Timeout(timeout[1], connect=timeout[0])
        else:
            to = httpx.Timeout(timeout)
            
        status_code = None
        retries = 2
        for attempt in range(retries + 1):
            try:
                r = self.client.get(url, timeout=to)
                status_code = r.status_code
                if r.status_code in [502, 503, 504] and attempt < retries:
                    time.sleep(0.5 * (attempt + 1))
                    continue
                if (r.status_code < 400 or r.status_code in [402, 423]) and len(r.text) > 100:
                    return r.text, str(r.url), r.status_code
                break
            except (httpx.ReadTimeout, httpx.ReadError) as exc:
                if attempt < retries:
                    time.sleep(0.5 * (attempt + 1))
                    continue
                logger.warning(f"Timeout: {url}")
                break
            except httpx.ConnectError:
                logger.warning(f"DNS/Connection failure: {url}")
                break
            except httpx.ConnectTimeout:
                logger.warning(f"Timeout: {url}")
                break
            except httpx.RequestError as exc:
                if "ssl" in str(exc).lower():
                    logger.warning(f"SSL failure: {url}")
                else:
                    logger.warning(f"Fetch failed: {url}")
                break
            except Exception:
                logger.warning(f"Fetch failed: {url}")
                break

        return "", url, status_code

    def fetch_with_fallbacks(self, domain: str):
        domain = canonical_domain(domain)

        candidates = [
            f"https://www.{domain}",
            f"https://{domain}",
            f"http://www.{domain}",
            f"http://{domain}",
        ]

        last_status = None
        for url in candidates:
            html, final_url, status_code = self.fetch_html(url)
            if status_code is not None:
                last_status = status_code

            if html and len(html) > 100:
                return html, final_url, status_code

        return "", "", last_status



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
        soup = safe_soup(html)
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
        soup = safe_soup(html)
        title = soup.find('title')
        title_text = (title.text.strip()[:200] if title else '')
        meta = soup.find('meta', attrs={'name': 'description'})
        desc = (meta.get('content', '')[:200] if meta else '')
        return title_text, desc

    def extract_script_urls(self, html: str) -> list:
        soup = safe_soup(html)
        return [s['src'] for s in soup.find_all('script', src=True)]

    def scan(self, domain: str) -> dict:
        mock_cfg = load_mock_config(domain)
        if mock_cfg:
            return {
                'historical_checkouts': mock_cfg.get('checkout_providers', []),
                'emails':               mock_cfg.get('emails', []),
                'phone_numbers':        mock_cfg.get('phone_numbers', []),
                'whatsapp':             {'link': '', 'number': ''},
                'myshopify_domain':     f"{domain.split('.')[0]}.myshopify.com" if mock_cfg.get('shopify') else "",
                'socials':              {"linkedin": "", "instagram": "", "facebook": "", "twitter": "", "youtube": ""},
                'tech_stack':           mock_cfg.get('app_signatures', []),
                'shopify':              mock_cfg.get('shopify', False),
                'title':                mock_cfg.get('title', ''),
                'description':          mock_cfg.get('description', ''),
                'page_hash':            hashlib.md5(domain.encode()).hexdigest()[:16],
                'fetch_ok':             True,
                'raw_html':             f"<html><title>{mock_cfg.get('title', '')}</title><body>{mock_cfg.get('description', '')}</body></html>",
            }

        html, final_url, _ = self.fetch_with_fallbacks(domain)

        empty = {
            'historical_checkouts': [], 'emails': [], 'phone_numbers': [],
            'whatsapp': {'link': '', 'number': ''},
            'myshopify_domain': '',
            'socials': {k: '' for k in ('linkedin', 'instagram', 'facebook', 'twitter', 'youtube')},
            'tech_stack': [], 'shopify': False, 'title': '', 'description': '', 'page_hash': '',
            'fetch_ok': False,
        }
        if not html or len(html) < 100:
            logger.warning(f'{domain}: Fetch failed after all URL fallbacks')
            return empty

        scripts = self.extract_script_urls(html)
        title, desc = self.get_title_meta(html)

        return {
            'historical_checkouts': self.detect_historical(html, scripts),
            'emails':               extract_emails(html),
            'phone_numbers':        extract_phones(html),
            'whatsapp':             extract_whatsapp(html),
            'myshopify_domain':     extract_myshopify(html),
            'socials':              self.detect_social(html),
            'tech_stack':           self.detect_tech_stack(html),
            'shopify':              self.detect_shopify(html),
            'title':                title,
            'description':          desc,
            'page_hash':            hashlib.md5(html.encode()).hexdigest()[:16],
            'fetch_ok':             True,
            'raw_html':             html,
        }


# ─────────────────────────────────────────────
#  PLAYWRIGHT DETECTOR  (live network)
# ─────────────────────────────────────────────

class PlaywrightDetector:
    def __init__(self, browser):
        self.browser = browser

    @staticmethod
    def _rank_winner(matches: dict):
        """
        Given {provider: confidence}, pick the deterministic winner:
        highest confidence first, then per-provider score as tiebreak.
        This replaces 'first request to arrive wins', which was the root
        cause of native<->3rd-party mislabeling under concurrent load.
        """
        if not matches:
            return None, 0
        def keyfn(item):
            provider, conf = item
            score = LIVE_PROVIDER_PATTERNS.get(provider, {}).get('score', 0)
            return (conf, score)
        winner, conf = max(matches.items(), key=keyfn)
        return winner, conf

    async def scan(self, domain: str) -> dict:
        mock_cfg = load_mock_config(domain)
        if mock_cfg:
            live_co = mock_cfg.get('live_checkout')
            return {
                'live_checkout':        live_co,
                'live_confidence':      95 if live_co else 0,
                'live_evidence':        mock_cfg.get('checkout_scripts', []) if live_co else [],
                'has_kwikpass':         mock_cfg.get('has_kwikpass', False),
                'kwikpass_evidence':    [],
                'all_network_requests': [],
                'nav_ok': True,
                'live_ambiguous': False,
            }

        result = {
            'live_checkout': None, 'live_confidence': 0,
            'live_evidence': [], 'has_kwikpass': False,
            'kwikpass_evidence': [], 'all_network_requests': [],
            'nav_ok': False, 'live_ambiguous': False,
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

            # Collect ALL provider matches, do not lock on first hit.
            matches = {}                 # provider -> best confidence seen
            evidence = {}                # provider -> [urls]
            has_kwikpass = False
            kwikpass_evidence = []
            all_requests = []
            # Set when a required (95) pattern fires — that's definitive,
            # so we can stop waiting early. 80-level hits never fast-exit.
            required_hit = asyncio.Event()

            def on_request(request):
                nonlocal has_kwikpass
                url = request.url.lower()
                all_requests.append(request.url)

                if 'gkx.gokwik.co' in url or 'kwikpass' in url:
                    has_kwikpass = True
                    kwikpass_evidence.append(request.url)
                    return

                for name, data in LIVE_PROVIDER_PATTERNS.items():
                    for pattern in data['patterns']:
                        if pattern in url:
                            is_required = pattern in data.get('required_for_live', [])
                            conf = 95 if is_required else 80
                            if conf > matches.get(name, 0):
                                matches[name] = conf
                            evidence.setdefault(name, []).append(request.url)
                            logger.info(f'Live signal [{name}] conf={conf}  {pattern}')
                            if is_required:
                                required_hit.set()
                            # keep scanning other patterns/providers
                            return

            page.on('request', on_request)
            await page.route(
                '**/*.{png,jpg,jpeg,gif,webp,svg,ico,woff,woff2,ttf,mp4,mp3,pdf}',
                lambda r: r.abort()
            )

            try:
                await page.goto(base_url, wait_until='commit', timeout=NAV_TIMEOUT_MS)
                result['nav_ok'] = True
            except Exception:
                logger.warning(f'{domain}: Playwright navigation failed')

            # Wait for a definitive (required) signal, capped. Even after a
            # required hit, linger briefly so a competing required signal or
            # kwikpass can also register before we rank the winner.
            try:
                await asyncio.wait_for(required_hit.wait(), timeout=LIVE_SIGNAL_TIMEOUT)
                await asyncio.sleep(POST_HIT_GRACE)
            except asyncio.TimeoutError:
                pass

            winner, conf = self._rank_winner(matches)
            # Ambiguity flag: low-confidence winner, or two different providers
            # both hit at the SAME confidence — worth re-verifying serially.
            top_conf_providers = [p for p, c in matches.items() if c == conf]
            ambiguous = (conf == 80) or (len(top_conf_providers) > 1)

            result.update({
                'live_checkout':        winner,
                'live_confidence':      conf,
                'live_evidence':        evidence.get(winner, []) if winner else [],
                'has_kwikpass':         has_kwikpass,
                'kwikpass_evidence':    kwikpass_evidence,
                'all_network_requests': all_requests[:20],
                'live_ambiguous':       bool(winner) and ambiguous,
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
    # 20 patterns to detect hot brands (Shark Tank, bestsellers, press, viral)
    HOT_BRAND_PATTERNS = [
        # Shark Tank India / US
        r"shark\s*tank",
        r"as\s+seen\s+on\s+shark\s+tank",
        r"featured\s+on\s+shark\s+tank",
        # Amazon / Flipkart bestseller signals
        r"#\s*1\s+best\s*seller",
        r"amazon\s+best\s*seller",
        r"flipkart\s+best\s*seller",
        r"bestselling\s+on\s+(amazon|flipkart)",
        r"top\s+rated\s+on\s+(amazon|flipkart)",
        r"amazon\s+choice",
        r"amazon[\u2019']?s\s+choice",
        # Press / Awards
        r"as\s+seen\s+in\s+(vogue|forbes|elle|gq|harpers|tatler|nykaa)",
        r"featured\s+in\s+(vogue|forbes|elle|bbc|cnbc|ndtv)",
        r"award[\s\-]winning",
        r"india[\s\-]+brand[\s\-]+(award|winner)",
        # Viral / popularity signals
        r"(went\s+)?viral\s+on\s+(instagram|reels|youtube|twitter|tiktok)",
        r"[0-9]+\s*[km\+]+\s+happy\s+customers",
        r"[0-9]+\s+lakh\s+(customers|orders|sold)",
        r"crore\s+(in\s+)?revenue",
        r"trusted\s+by\s+[0-9,]+",
        r"celebrity[\s\-]endorsed",
    ]

    @staticmethod
    def detect_hot_brand(data: dict, html: str = "") -> bool:
        return bool(ScoringEngine.detect_hot_brand_reason(data, html))

    @staticmethod
    def detect_hot_brand_reason(data: dict, html: str = "") -> str:
        """Return the pattern name (e.g. Shark Tank, Amazon Bestseller) that matched, or empty string."""
        text = f"{data.get('title', '')} {data.get('description', '')}".lower()
        
        pattern_names = [
            (r"shark\s*tank", "Shark Tank"),
            (r"as\s+seen\s+on\s+shark\s+tank", "Shark Tank"),
            (r"featured\s+on\s+shark\s+tank", "Shark Tank"),
            (r"#\s*1\s+best\s*seller", "Bestseller"),
            (r"amazon\s+best\s*seller", "Amazon Bestseller"),
            (r"flipkart\s+best\s*seller", "Flipkart Bestseller"),
            (r"bestselling\s+on\s+(amazon|flipkart)", "Bestseller on Amazon/Flipkart"),
            (r"top\s+rated\s+on\s+(amazon|flipkart)", "Top Rated on Amazon/Flipkart"),
            (r"amazon\s+choice", "Amazon Choice"),
            (r"amazon[\u2019']?s\s+choice", "Amazon Choice"),
            (r"as\s+seen\s+in\s+(vogue|forbes|elle|gq|harpers|tatler|nykaa)", "Media Feature"),
            (r"featured\s+in\s+(vogue|forbes|elle|bbc|cnbc|ndtv)", "Media Feature"),
            (r"award[\s\-]winning", "Award Winner"),
            (r"india[\s\-]+brand[\s\-]+(award|winner)", "Award Winner"),
            (r"(went\s+)?viral\s+on\s+(instagram|reels|youtube|twitter|tiktok)", "Viral Brand"),
            (r"[0-9]+\s*[km\+]+\s+happy\s+customers", "Happy Customers"),
            (r"[0-9]+\s+lakh\s+(customers|orders|sold)", "Happy Customers"),
            (r"crore\s+(in\s+)?revenue", "High Revenue"),
            (r"trusted\s+by\s+[0-9,]+", "Trusted Brand"),
            (r"celebrity[\s\-]endorsed", "Celebrity Endorsed")
        ]
        
        for pattern, name in pattern_names:
            if re.search(pattern, text, re.IGNORECASE):
                return name
                
        if html:
            html_lower = html.lower()
            for pattern, name in pattern_names:
                if re.search(pattern, html_lower, re.IGNORECASE):
                    return name
                    
        return ""

    @staticmethod
    def calculate_score(data: dict) -> int:
        score = 0

        # Baseline — Shopify only
        if not data.get('shopify'):
            return 0

        score += 15  # Shopify baseline

        # Contact signals (most important)
        if data.get('phone_numbers'):           score += 30
        if data.get('emails'):                  score += 15

        # Win-back signal — FlexyPe in historical but NOT currently live
        hist = data.get('historical_checkouts', [])
        if 'FlexyPe' in hist and data.get('live_checkout') != 'FlexyPe':
            score += 15

        # Social signals (minor)
        s = data.get('socials', {})
        if s.get('linkedin'):                   score += 8
        if s.get('instagram'):                  score += 4
        if s.get('facebook'):                   score += 3

        # Hot brand signal
        if data.get('hot_brand'):               score += 20

        # Activity signals
        if data.get('live_checkout'):           score += 5

        # Historical checkout count bonus (proven checkout spender)
        hc = len(hist)
        score += {0: 0, 1: 5, 2: 8}.get(hc, 10)

        # NOTE: has_kwikpass no longer contributes to score.
        # It is surfaced as an indicator in the UI to prompt FlexyPass pitch.

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

def _empty_live() -> dict:
    return {
        'live_checkout': None, 'live_confidence': 0, 'live_evidence': [],
        'has_kwikpass': False, 'kwikpass_evidence': [],
        'all_network_requests': [], 'nav_ok': False, 'live_ambiguous': False,
    }


async def scan_domain(browser, domain: str, semaphore: Semaphore) -> dict:
    async with semaphore:
        start = time.time()
        logger.info(f'Scanning  {domain}')

        # ── DNS pre-flight: dead domains return empty instantly ──
        if not load_mock_config(domain) and not await asyncio.to_thread(domain_resolves, domain):
            logger.warning(f'{domain}: DNS does not resolve — skipping scan')
            return _empty_result(domain, start, dns_dead=True)

        src = await asyncio.to_thread(SourceDetector().scan, domain)

        # If the HTTP fetch died, the browser likely can't reach it either —
        # skip Playwright to avoid burning the hard cap on nothing.
        if not src.get('fetch_ok'):
            live = _empty_live()
        else:
            # Hard wall-clock cap so no single page can stall a worker.
            try:
                live = await asyncio.wait_for(
                    PlaywrightDetector(browser).scan(domain),
                    timeout=DOMAIN_HARD_CAP
                )
                
                # Double-check: if it differs from current database state, run again
                existing = merchants.find_one({"domain": domain})
                old_chk = existing.get("live_checkout") if existing else None
                new_chk = live.get("live_checkout")
                
                if old_chk != new_chk:
                    logger.info(f'[Double-Check] Checkout changed for {domain} ({old_chk} -> {new_chk}). Running retry scan...')
                    retry_live = await asyncio.wait_for(
                        PlaywrightDetector(browser).scan(domain),
                        timeout=DOMAIN_HARD_CAP
                    )
                    if retry_live.get('live_checkout') == new_chk:
                        logger.info(f'[Double-Check] Confirmed checkout change for {domain}: {new_chk}')
                        live = retry_live
                    else:
                        logger.warning(f'[Double-Check] Warning: Retry did not confirm the change for {domain}. Reverting to: {old_chk}')
                        # Revert back to database state
                        live['live_checkout'] = old_chk
                        if existing:
                            live['live_confidence'] = existing.get('live_confidence', 0)
                            live['live_evidence'] = existing.get('live_evidence', [])
                            live['has_kwikpass'] = existing.get('has_kwikpass', False)
                            live['kwikpass_evidence'] = existing.get('kwikpass_evidence', [])
            except asyncio.TimeoutError:
                logger.warning(f'{domain}: Playwright phase hit {DOMAIN_HARD_CAP}s hard cap')
                live = _empty_live()

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
            'hot_brand':            False,
            'lead_score':           0,
            'priority':             '',
            'last_scan':            time.strftime('%Y-%m-%d %H:%M'),
            'scan_duration':        0,
            'status':               'Not Contacted',
            'notes':                '',
            # internal trust signals (stripped before persist/return)
            '_fetch_ok':            src.get('fetch_ok', False),
            '_nav_ok':              live.get('nav_ok', False),
            '_live_ambiguous':      live.get('live_ambiguous', False),
            '_dns_dead':            False,
        }
        result['hot_brand']  = ScoringEngine.detect_hot_brand(result, src.get('raw_html', ''))
        result['lead_score'] = ScoringEngine.calculate_score(result)
        result['priority']   = ScoringEngine.get_priority(result['lead_score'])
        result['scan_duration'] = round(time.time() - start, 2)

        provider = result['live_checkout'] or ('historical:' + ','.join(result['historical_checkouts'][:2]) if result['historical_checkouts'] else 'none')
        logger.info(f'Done  {domain}  score={result["lead_score"]}  checkout={provider}  {result["scan_duration"]}s')
        return result


def _empty_result(domain: str, start: float, dns_dead: bool = False) -> dict:
    return {
        'domain': domain, 'shopify': False, 'live_checkout': None,
        'live_confidence': 0, 'live_evidence': [], 'historical_checkouts': [],
        'has_kwikpass': False, 'kwikpass_evidence': [], 'emails': [],
        'phone_numbers': [], 'whatsapp_link': '', 'whatsapp_number': '',
        'myshopify_domain': '',
        'socials': {k: '' for k in ('linkedin', 'instagram', 'facebook', 'twitter', 'youtube')},
        'tech_stack': [], 'title': '', 'description': '', 'page_hash': '',
        'hot_brand': False, 'lead_score': 0, 'priority': 'LOW',
        'last_scan': time.strftime('%Y-%m-%d %H:%M'),
        'scan_duration': round(time.time() - start, 2),
        'status': 'Not Contacted', 'notes': '',
        '_fetch_ok': False, '_nav_ok': False, '_live_ambiguous': False,
        '_dns_dead': dns_dead,
    }


def is_suspicious(r: dict) -> bool:
    """
    A result we don't trust enough to persist when produced under concurrent
    load. Re-verified in a second pass.

    Suspicious if:
      • fetch failed AND no positive signal (and DNS resolves — a genuinely
        dead domain is trusted as-is, not re-tried), OR
      • the live checkout was an ambiguous / low-confidence (80) detection, OR
      • kwikpass fired but no provider resolved (likely a missed required hit), OR
      • live==none but historical shows a known checkout (likely missed live).
    """
    if not r:
        return True

    # genuinely dead DNS — trust it, re-scanning won't help
    if r.get('_dns_dead'):
        return False

    # fetch failed but DNS resolves → transient, worth one serial retry
    if not r.get('_fetch_ok'):
        if r.get('shopify') or r.get('emails') or r.get('title') or r.get('historical_checkouts'):
            return False
        return True

    # ambiguous / low-confidence live detection
    if r.get('_live_ambiguous'):
        return True

    # kwikpass present but provider unresolved
    if r.get('has_kwikpass') and not r.get('live_checkout'):
        return True

    # none live, but historical lists a real checkout → likely a miss
    if not r.get('live_checkout') and r.get('historical_checkouts'):
        return True

    return False


def _strip_internal(r: dict) -> dict:
    """Drop internal underscore-prefixed trust flags before persisting/returning."""
    return {k: v for k, v in r.items() if not k.startswith('_')}


# ─────────────────────────────────────────────
#  RUNNER
# ─────────────────────────────────────────────

FLEXY_LEFTERS = []
FLEXY_JOINERS = []


def _persist_result(r: dict, exclude_master: bool):
    """Slack-notify on provider change, upsert into merchants. Mutates r in place."""
    canonical = canonical_domain(r["domain"])
    r["domain"] = canonical
    if exclude_master:
        return
    try:
        existing = merchants.find_one({"domain": canonical})
        if existing:
            old_chk = existing.get("live_checkout")
            new_chk = r["live_checkout"]
            if old_chk != new_chk:
                send_slack_notification(canonical, old_chk, new_chk, "Scan Run")
                
                # Track FlexyPe transitions
                old_lower = (old_chk or "").lower()
                new_lower = (new_chk or "").lower()
                if old_lower == "flexype" and new_lower != "flexype":
                    FLEXY_LEFTERS.append(canonical)
                elif new_lower == "flexype" and old_lower != "flexype":
                    FLEXY_JOINERS.append(canonical)

                # Log all live_checkout changes to fingerprint_history so they persist until acknowledged
                fingerprint_history.insert_one({
                    "merchant": canonical,
                    "old_hash": "",
                    "new_hash": "",
                    "changes": {
                        "live_checkout": {
                            "old": old_chk if old_chk else "None",
                            "new": new_chk if new_chk else "None"
                        }
                    },
                    "timestamp": datetime.utcnow()
                })

                # Append old provider to historical checkouts list
                if old_chk and old_chk.lower() not in ["none", "unknown"]:
                    hists = r.get("historical_checkouts", [])
                    if old_chk not in hists:
                        hists.append(old_chk)
                        r["historical_checkouts"] = hists
        else:
            new_chk = r.get("live_checkout")
            if new_chk and new_chk.lower() == "flexype":
                FLEXY_JOINERS.append(canonical)
    except Exception as e:
        logger.warning(f"Error checking provider change: {e}")

    merchants.update_one(
        {"domain": canonical},
        {"$set": _strip_internal(r)},
        upsert=True
    )


async def run_scanner(domains: list, max_concurrent: int = 10, exclude_master: bool = False) -> list:
    delete_old_logs()
    results   = []
    semaphore = Semaphore(max_concurrent)

    logger.info(f'Starting scan  domains={len(domains)}  concurrency={max_concurrent}')

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=['--disable-dev-shm-usage', '--no-sandbox']
        )
        try:
            tasks    = [scan_domain(browser, canonical_domain(d), semaphore) for d in domains]
            total    = len(tasks)
            pbar     = ProgressBar(total)
            done     = 0

            print()
            for coro in asyncio.as_completed(tasks):
                r = await coro
                if r:
                    # Defer DB write + Slack notify for suspicious results until
                    # AFTER verification — this stops a transient 'none' from
                    # polluting Mongo and firing a false 'provider changed' alert.
                    if not is_suspicious(r):
                        _persist_result(r, exclude_master)
                    results.append(r)
                done += 1
                live_c = sum(1 for x in results if x.get('live_checkout'))
                hist_c = sum(1 for x in results if x.get('historical_checkouts'))
                pbar.update(done, live_c, hist_c)
            print('\n')

            # ── VERIFICATION PASS ──
            # Re-scan every suspicious result under light concurrency. Checkout
            # mis-IDs are now fixed by deterministic ranking (not timing), so a
            # clean re-scan resolves them; fetch-failure recoveries also benefit
            # from the reduced contention.
            suspect_domains = [r['domain'] for r in results if is_suspicious(r)]
            if suspect_domains:
                logger.info(f'Verification pass: re-scanning {len(suspect_domains)} suspicious domains (concurrency={VERIFY_CONCURRENCY})')
                print(f'  Verifying {len(suspect_domains)} suspicious result(s)...')
                verify_sem = Semaphore(VERIFY_CONCURRENCY)
                vtasks = [scan_domain(browser, d, verify_sem) for d in suspect_domains]
                recovered = 0
                for coro in asyncio.as_completed(vtasks):
                    vr = await coro
                    if not vr:
                        continue
                    canonical = canonical_domain(vr['domain'])
                    # Replace the old record; the re-scan is at least as
                    # trustworthy as the contended original.
                    results = [x for x in results if canonical_domain(x['domain']) != canonical]
                    results.append(vr)
                    if not is_suspicious(vr):
                        recovered += 1
                    else:
                        log_failed(canonical, 'still suspicious after re-verify')
                    # Persist (and notify) the verified result now.
                    _persist_result(vr, exclude_master)
                logger.info(f'Verification pass complete: cleanly resolved={recovered}/{len(suspect_domains)}')
                print(f'  Verification done: cleanly resolved {recovered}/{len(suspect_domains)}\n')
        finally:
            await browser.close()

    # Strip internal trust flags from returned results
    results = [_strip_internal(r) for r in results]
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

    # Send Slack summary notification
    webhook_url = os.getenv("SLACK_WEBHOOK_URL")
    if webhook_url:
        flexype_count = sum(1 for r in results if r.get('live_checkout') == 'FlexyPe')
        left_str = ", ".join(f"`{d}`" for d in FLEXY_LEFTERS) if FLEXY_LEFTERS else "None"
        joined_str = ", ".join(f"`{d}`" for d in FLEXY_JOINERS) if FLEXY_JOINERS else "None"

        summary_text = (
            f"🚀 *Active Scan Completed Summary (`main_scraper.py`)*\n"
            f"• *Total Scanned:* {len(domains)}\n"
            f"• *Total with FlexyPe:* {flexype_count}\n"
            f"• *Total who left FlexyPe since last main scan:* {len(FLEXY_LEFTERS)} ({left_str})\n"
            f"• *Total who joined FlexyPe:* {len(FLEXY_JOINERS)} ({joined_str})\n"
        )
        try:
            requests.post(webhook_url, json={"text": summary_text}, timeout=10)
            print("[Slack Success] Sent scan run completion summary.")
        except Exception as e:
            print(f"[Slack Error] Failed to send completion summary: {e}")


if __name__ == '__main__':
    main()