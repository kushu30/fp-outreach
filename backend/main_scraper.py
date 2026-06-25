#!/usr/bin/env python3
import re
import json
import socket
import asyncio
import pandas as pd
import time
import random
import hashlib
import requests
import httpx
import logging
import sys
import os
import threading
from datetime import datetime
from bs4 import BeautifulSoup
from playwright.async_api import async_playwright
from asyncio import Semaphore
from urllib.parse import urlparse, urljoin, unquote
from db import merchants, fingerprint_history, merchant_fingerprints

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


def _looks_like_html(text: str) -> bool:
    """
    Guard against undecoded brotli/gzip or binary reaching bs4 (which raises
    ParserRejectedMarkup). Intentionally LENIENT: only reject content that is
    clearly binary. A valid page with a stray odd byte must still pass — being
    too strict here threw away real Shopify stores.
    """
    if not text:
        return False
    head = text[:4096]
    bad = sum(1 for c in head
              if (ord(c) < 32 and c not in '\t\n\r') or c == '\ufffd')
    if bad > len(head) * 0.15:
        return False
    low = head.lower()
    if any(t in low for t in ('<html', '<!doctype', '<head', '<body', '<meta',
                              '<div', '<script', '<link', '<title', '<!--')):
        return True
    return bad <= len(head) * 0.02


def safe_soup(html: str):
    """BeautifulSoup that never raises — returns an empty soup on bad markup."""
    try:
        return BeautifulSoup(html or '', 'html.parser')
    except Exception:
        return BeautifulSoup('', 'html.parser')


def domain_resolves(domain: str) -> bool:
    """
    Fast pre-flight DNS check. A domain that cannot resolve will fail in both
    the requests fetch (after pointless retries across 4 URL candidates) and
    Playwright navigation, wasting minutes. Catch it once, in milliseconds.
    Tries both the bare domain and the www. variant.
    """
    d = canonical_domain(domain)
    hosts = [d]
    if "myshopify.com" not in d:
        hosts.append(f"www.{d}")
    for host in hosts:
        try:
            socket.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP)
            return True
        except socket.gaierror:
            continue
        except Exception:
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

file_handler = logging.FileHandler(log_filename, encoding='utf-8')
file_handler.setLevel(logging.DEBUG)
file_handler.setFormatter(logging.Formatter(
    '%(asctime)s  %(levelname)-8s  %(message)s',
    datefmt='%H:%M:%S'
))

class _ConsoleFormatter(logging.Formatter):
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

for _noisy in ('urllib3', 'asyncio', 'playwright', 'websockets', 'httpx', 'httpcore'):
    logging.getLogger(_noisy).setLevel(logging.WARNING)


def log_failed(domain: str, reason: str):
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

LIVE_SIGNAL_TIMEOUT = 12
NAV_TIMEOUT_MS      = 15000
POST_HIT_GRACE      = 0.6
DOMAIN_HARD_CAP     = 20
VERIFY_CONCURRENCY  = 3

HASH_GATE_ENABLED   = os.getenv("HASH_GATE_ENABLED", "1") == "1"
RECHECK_ON_CHANGE   = os.getenv("RECHECK_ON_CHANGE", "1") == "1"

CONFIRMATIONS_REQUIRED = int(os.getenv("CONFIRMATIONS_REQUIRED", "2"))
FLEXYPE_DEPARTURE_ALERT_ON_PENDING = os.getenv("FLEXYPE_DEPARTURE_ALERT_ON_PENDING", "1") == "1"

HTTP_TIMEOUT = (5, 15)

MOCK_STORES_PATH = os.getenv("MOCK_STORES_PATH", "")
_MOCK_STORES = {}
if MOCK_STORES_PATH and os.path.exists(MOCK_STORES_PATH):
    try:
        with open(MOCK_STORES_PATH, 'r', encoding='utf-8') as _f:
            _MOCK_STORES = json.load(_f) or {}
        logger.info(f'Loaded {len(_MOCK_STORES)} mock stores from {MOCK_STORES_PATH}')
    except Exception as _e:
        logger.warning(f'Failed to load mock stores: {_e}')

def load_mock_config(domain: str):
    if not _MOCK_STORES:
        return None
    return _MOCK_STORES.get(canonical_domain(domain)) or _MOCK_STORES.get(domain)


# ─────────────────────────────────────────────
#  USER-AGENT POOL  (rotated per request to reduce UA+IP fingerprinting)
# ─────────────────────────────────────────────

USER_AGENTS = [
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/137.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36 Edg/135.0.0.0",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:126.0) Gecko/20100101 Firefox/126.0",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Mobile/15E148 Safari/604.1",
]

def _base_headers(user_agent: str) -> dict:
    is_mac = "Mac OS X" in user_agent
    is_firefox = "Firefox" in user_agent
    platform = '"macOS"' if is_mac else ('"Windows"' if "Windows" in user_agent else '"Linux"')
    h = {
        "User-Agent": user_agent,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-User": "?1",
    }
    # Client-hints only make sense for Chromium UAs.
    if not is_firefox and "Safari/605" not in user_agent:
        h["Sec-Ch-Ua"] = '"Not A(Brand";v="99", "Google Chrome";v="137", "Chromium";v="137"'
        h["Sec-Ch-Ua-Mobile"] = "?0"
        h["Sec-Ch-Ua-Platform"] = platform
    return h

def random_ua() -> str:
    return random.choice(USER_AGENTS)

# Kept for callers (e.g. Playwright) that still reference a static HEADERS dict.
HEADERS = _base_headers(USER_AGENTS[0])


# ─────────────────────────────────────────────
#  ADAPTIVE THROTTLE  (global token bucket + circuit breaker)
# ─────────────────────────────────────────────
#
# This is a THREAD-SAFE limiter because SourceDetector.fetch_html runs inside
# asyncio.to_thread (i.e. real worker threads), not on the event loop. We use a
# threading.Lock + blocking sleeps so it paces correctly regardless of how many
# threads call it concurrently. One shared instance gates EVERY outbound HTTP
# request, so the rate Shopify/Cloudflare sees is what we actually control.

class AdaptiveThrottle:
    def __init__(self, base_rate=None, min_rate=None, max_rate=None):
        self.base_rate = float(base_rate if base_rate is not None
                               else os.getenv("THROTTLE_BASE_RATE", "3.0"))
        self.min_rate  = float(min_rate if min_rate is not None
                               else os.getenv("THROTTLE_MIN_RATE", "0.4"))
        self.max_rate  = float(max_rate if max_rate is not None
                               else os.getenv("THROTTLE_MAX_RATE", "6.0"))
        self.rate = self.base_rate
        self._tokens = self.base_rate
        self._last = time.monotonic()
        self._lock = threading.Lock()
        self._clean_streak = 0
        self._cooldown_until = 0.0

    def acquire(self):
        """Blocking. Call once per outbound HTTP request, before sending."""
        while True:
            with self._lock:
                now = time.monotonic()
                cooldown_wait = self._cooldown_until - now
                if cooldown_wait <= 0:
                    self._tokens = min(5.0, self._tokens + (now - self._last) * self.rate)
                    self._last = now
                    if self._tokens >= 1:
                        self._tokens -= 1
                        return
                    deficit_wait = (1 - self._tokens) / self.rate
                    wait = deficit_wait
                else:
                    wait = cooldown_wait
            time.sleep(min(wait, 5.0))

    def on_rate_limit(self):
        """Multiplicative backoff + a short global cooldown for all threads."""
        with self._lock:
            self._clean_streak = 0
            self.rate = max(self.min_rate, self.rate * 0.5)
            self._cooldown_until = time.monotonic() + float(os.getenv("THROTTLE_COOLDOWN", "5.0"))
        logger.warning(f'[Throttle] rate-limited — backing off to {self.rate:.2f} req/s')

    def on_success(self):
        """Additive recovery once a clean streak proves headroom exists."""
        with self._lock:
            self._clean_streak += 1
            if self._clean_streak >= 25:
                self._clean_streak = 0
                if self.rate < self.max_rate:
                    self.rate = min(self.max_rate, self.rate + 0.3)
                    logger.info(f'[Throttle] recovering — rate up to {self.rate:.2f} req/s')


# One process-wide throttle shared by every SourceDetector instance/thread.
THROTTLE = AdaptiveThrottle()


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
#  CONTACT EXTRACTION HELPERS  (unchanged logic)
# ─────────────────────────────────────────────

_EMAIL_RE = re.compile(
    r'(?<![=\'\"/])(?<!\w)'
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
    for a in soup.find_all('a', href=True):
        href = a['href']
        if href.lower().startswith('mailto:'):
            addr = href[7:].split('?')[0].strip().lower()
            raw.add(addr)
    for tag in soup(['script', 'style', 'noscript']):
        tag.decompose()
    text = soup.get_text(' ')
    raw.update(_EMAIL_RE.findall(text))
    raw.update(_EMAIL_RE.findall(html))
    return _clean_emails(raw)


_PHONE_RE = re.compile(
    r'(?:(?:\+|00)(?:91|1|44|61|971|65|60|66|880|94|977|92|62|63|84|66|82|81|86)\s*[-.\s]?)?'
    r'(?:\(?\d{2,4}\)?\s*[-.\s]?)?'
    r'\d{3,5}'
    r'[-.\s]?\d{3,5}'
    r'(?:[-.\s]?\d{2,4})?',
    re.IGNORECASE
)
_PHONE_VALID = re.compile(r'^\+?[\d\s\-().]{7,17}$')
_PHONE_JUNK  = re.compile(r'(19|20)\d{2}')

def extract_phones(html: str) -> list:
    soup = BeautifulSoup(html, 'html.parser')
    raw = set()
    for a in soup.find_all('a', href=True):
        href = a['href']
        if href.lower().startswith('tel:'):
            raw.add(href[4:].strip())
    for script in soup.find_all('script', type='application/ld+json'):
        try:
            data = json.loads(script.string or '')
            if isinstance(data, dict):
                for key in ('telephone', 'phone', 'faxNumber'):
                    if key in data:
                        raw.add(str(data[key]))
        except Exception:
            pass
    for tag in soup(['script', 'style', 'noscript']):
        tag.decompose()
    text = soup.get_text(' ')
    for m in _PHONE_RE.findall(text):
        digits = re.sub(r'\D', '', m)
        if 7 <= len(digits) <= 15:
            raw.add(m.strip())
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


_WA_LINK_RE = re.compile(
    r'https?://(?:api\.whatsapp\.com/send|wa\.me|web\.whatsapp\.com/send)'
    r'[^\s\'"<>]*',
    re.IGNORECASE
)

def extract_whatsapp(html: str) -> dict:
    soup = BeautifulSoup(html, 'html.parser')
    result = {'link': '', 'number': ''}
    for a in soup.find_all('a', href=True):
        href = a['href']
        if _WA_LINK_RE.match(href):
            result['link'] = href
            phone_match = re.search(r'(?:phone|to)=(\+?[\d]+)', href, re.IGNORECASE)
            if phone_match:
                result['number'] = '+' + phone_match.group(1).lstrip('+')
            break
    if not result['link']:
        m = _WA_LINK_RE.search(html)
        if m:
            result['link'] = m.group(0)
            phone_match = re.search(r'(?:phone|to)=(\+?[\d]+)', m.group(0), re.IGNORECASE)
            if phone_match:
                result['number'] = '+' + phone_match.group(1).lstrip('+')
    return result


_MYSHOPIFY_RE = re.compile(r'([\w\-]+\.myshopify\.com)', re.IGNORECASE)

def extract_myshopify(html: str) -> str:
    m = _MYSHOPIFY_RE.search(html)
    return m.group(1).lower() if m else ''


# ─────────────────────────────────────────────
#  SOURCE DETECTOR  (httpx-based, throttled, UA-rotating)
# ─────────────────────────────────────────────

def normalize_url(domain: str) -> str:
    domain = str(domain).strip().lower()
    protocol = 'https://'
    host = domain
    if host.startswith('https://'):
        host = host[8:]
    elif host.startswith('http://'):
        host = host[7:]
        protocol = 'http://'
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


def send_slack_notification(domain: str, old_provider: str, new_provider: str, reason: str, status: str = "confirmed"):
    webhook_url = os.getenv("SLACK_WEBHOOK_URL")
    if not webhook_url:
        logger.warning("[Slack Warning] SLACK_WEBHOOK_URL is not set. Skipping Slack notification.")
        return
    old_display = old_provider if old_provider else "none"
    new_display = new_provider if new_provider else "none"
    is_flexype_removed = bool(old_provider) and old_provider.lower() == "flexype" and (not new_provider or new_provider.lower() != "flexype")
    is_flexype_joined  = bool(new_provider) and new_provider.lower() == "flexype" and (not old_provider or old_provider.lower() != "flexype")

    if is_flexype_removed:
        prefix = "🔻 FlexyPe left"
    elif is_flexype_joined:
        prefix = "🟢 FlexyPe joined"
    else:
        prefix = "🔄 Checkout change"

    if status == "pending":
        text = f"{prefix} — *{domain}*: `{old_display}` → `{new_display}`  _(unverified, re-checking next scan)_"
    else:
        text = f"{prefix} — *{domain}*: `{old_display}` → `{new_display}`"

    try:
        response = requests.post(webhook_url, json={"text": text}, timeout=10)
        if response.status_code == 200:
            print(f"[Slack] {domain}: {old_display} -> {new_display} ({status})")
        else:
            print(f"[Slack Error] Status {response.status_code}: {response.text}")
    except Exception as e:
        print(f"[Slack Error] {e}")


def delete_old_logs():
    try:
        import glob
        for filepath in glob.glob(os.path.join(LOG_DIR, "*.log")):
            if os.path.basename(filepath) not in (os.path.basename(log_filename), os.path.basename(FAILED_LOG)):
                try:
                    os.remove(filepath)
                except Exception:
                    pass
        print("[Logs] Deleted old log files.")
    except Exception as e:
        logger.warning(f"Failed to delete old logs: {e}")


# Sentinel statuses fetch_html can surface to the caller so rate limits are
# never silently swallowed. A negative pseudo-status means "throttled".
RATE_LIMITED_STATUS = -429


class SourceDetector:
    _brotli_checked = False
    _has_brotli = False

    def __init__(self, throttle: AdaptiveThrottle = None):
        # Share the process-wide throttle by default so EVERY detector instance
        # (and there are many, one per thread) paces against the same budget.
        self.throttle = throttle or THROTTLE

        if not SourceDetector._brotli_checked:
            SourceDetector._brotli_checked = True
            try:
                import brotli  # noqa: F401
                SourceDetector._has_brotli = True
            except Exception:
                try:
                    import brotlicffi  # noqa: F401
                    SourceDetector._has_brotli = True
                except Exception:
                    SourceDetector._has_brotli = False
                    logger.warning("brotli not installed — requesting only gzip/deflate "
                                   "so responses stay decodable. `pip install brotli` to "
                                   "also accept 'br' (smaller, faster).")

        self._accept_encoding = "gzip, deflate, br" if SourceDetector._has_brotli else "gzip, deflate"
        # No fixed UA on the client; we set headers per-request so the UA rotates.
        self.client = httpx.Client(
            timeout=httpx.Timeout(15.0, connect=5.0),
            verify=False,
            follow_redirects=True
        )

    def _request_headers(self) -> dict:
        h = _base_headers(random_ua())
        h["Accept-Encoding"] = self._accept_encoding
        return h

    def fetch_html(self, url: str, timeout=None):
        """
        Returns (text, final_url, status_code).
        status_code == RATE_LIMITED_STATUS means we exhausted retries on 429/430;
        the caller should treat the domain as rate-limited (retry later), NOT dead.
        Every outbound attempt passes through the shared throttle.
        """
        if timeout is None:
            to = httpx.Timeout(15.0, connect=5.0)
        elif isinstance(timeout, tuple):
            to = httpx.Timeout(timeout[1], connect=timeout[0])
        else:
            to = httpx.Timeout(timeout)
        status_code = None
        retries = 3
        for attempt in range(retries + 1):
            # Pace BEFORE every network attempt — this is what the CDN counts.
            self.throttle.acquire()
            try:
                r = self.client.get(url, headers=self._request_headers(), timeout=to)
                status_code = r.status_code

                if r.status_code in [429, 430]:
                    self.throttle.on_rate_limit()
                    if attempt < retries:
                        sleep_time = (3.0 * (2 ** attempt)) + random.uniform(0.5, 2.5)
                        time.sleep(min(sleep_time, 45.0))
                        continue
                    return "", url, RATE_LIMITED_STATUS

                if r.status_code in [502, 503, 504] and attempt < retries:
                    time.sleep(0.5 * (attempt + 1) + random.uniform(0.2, 0.8))
                    continue

                if (r.status_code < 400 or r.status_code in [402, 423]) and len(r.text) > 100:
                    self.throttle.on_success()
                    return r.text, str(r.url), r.status_code
                break
            except (httpx.ReadTimeout, httpx.ReadError):
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
        if "myshopify.com" in domain:
            candidates = [
                f"https://{domain}",
                f"http://{domain}",
            ]
        else:
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
            # If we got definitively rate-limited, stop trying more candidates —
            # hammering the other 3 URLs just deepens the block. Surface it.
            if status_code == RATE_LIMITED_STATUS:
                return "", "", RATE_LIMITED_STATUS
            if html and len(html) > 100:
                return html, final_url, status_code
        return "", "", last_status

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
                'rate_limited':         False,
                'raw_html':             f"<html><title>{mock_cfg.get('title', '')}</title><body>{mock_cfg.get('description', '')}</body></html>",
            }

        html, final_url, status = self.fetch_with_fallbacks(domain)
        empty = {
            'historical_checkouts': [], 'emails': [], 'phone_numbers': [],
            'whatsapp': {'link': '', 'number': ''}, 'myshopify_domain': '',
            'socials': {k: '' for k in ('linkedin', 'instagram', 'facebook', 'twitter', 'youtube')},
            'tech_stack': [], 'shopify': False, 'title': '', 'description': '', 'page_hash': '',
            'fetch_ok': False, 'rate_limited': False, 'raw_html': '',
        }
        if status == RATE_LIMITED_STATUS:
            empty['rate_limited'] = True
            logger.warning(f'{domain}: rate-limited after retries')
            return empty
        if not html or len(html) < 100:
            logger.warning(f'{domain}: Fetch failed after all URL fallbacks')
            return empty

        if not _looks_like_html(html):
            logger.warning(f'{domain}: response did not look like HTML (binary/undecoded) — treating as failed fetch')
            return empty

        try:
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
                'page_hash':            hashlib.md5(html.encode('utf-8', 'replace')).hexdigest()[:16],
                'fetch_ok':             True,
                'rate_limited':         False,
                'raw_html':             html,
            }
        except Exception as e:
            logger.warning(f'{domain}: HTML parse failed ({type(e).__name__}: {str(e)[:80]}) — treating as failed fetch')
            return empty


# ─────────────────────────────────────────────
#  PLAYWRIGHT DETECTOR  (live network)
# ─────────────────────────────────────────────

class PlaywrightDetector:
    def __init__(self, browser):
        self.browser = browser

    @staticmethod
    def _rank_winner(matches: dict):
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
                'nav_ok': True, 'live_ambiguous': False,
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
                user_agent=random_ua(),
                viewport={'width': 1280, 'height': 720},
                java_script_enabled=True,
                ignore_https_errors=True
            )
            page = await context.new_page()

            matches = {}
            evidence = {}
            has_kwikpass = False
            kwikpass_evidence = []
            all_requests = []
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
            try:
                await asyncio.wait_for(required_hit.wait(), timeout=LIVE_SIGNAL_TIMEOUT)
                await asyncio.sleep(POST_HIT_GRACE)
            except asyncio.TimeoutError:
                pass

            winner, conf = self._rank_winner(matches)
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
#  SCORING  (unchanged)
# ─────────────────────────────────────────────

class ScoringEngine:
    HOT_BRAND_PATTERNS = [
        r"shark\s*tank", r"as\s+seen\s+on\s+shark\s+tank", r"featured\s+on\s+shark\s+tank",
        r"#\s*1\s+best\s*seller", r"amazon\s+best\s*seller", r"flipkart\s+best\s*seller",
        r"bestselling\s+on\s+(amazon|flipkart)", r"top\s+rated\s+on\s+(amazon|flipkart)",
        r"amazon\s+choice", r"amazon[\u2019']?s\s+choice",
        r"as\s+seen\s+in\s+(vogue|forbes|elle|gq|harpers|tatler|nykaa)",
        r"featured\s+in\s+(vogue|forbes|elle|bbc|cnbc|ndtv)",
        r"award[\s\-]winning", r"india[\s\-]+brand[\s\-]+(award|winner)",
        r"(went\s+)?viral\s+on\s+(instagram|reels|youtube|twitter|tiktok)",
        r"[0-9]+\s*[km\+]+\s+happy\s+customers", r"[0-9]+\s+lakh\s+(customers|orders|sold)",
        r"crore\s+(in\s+)?revenue", r"trusted\s+by\s+[0-9,]+", r"celebrity[\s\-]endorsed",
    ]

    @staticmethod
    def detect_hot_brand(data: dict, html: str = "") -> bool:
        return bool(ScoringEngine.detect_hot_brand_reason(data, html))

    @staticmethod
    def detect_hot_brand_reason(data: dict, html: str = "") -> str:
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
            (r"celebrity[\s\-]endorsed", "Celebrity Endorsed"),
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
        if not data.get('shopify'):
            return 0
        score += 15
        if data.get('phone_numbers'):           score += 30
        if data.get('emails'):                  score += 15
        hist = data.get('historical_checkouts', [])
        if 'FlexyPe' in hist and data.get('live_checkout') != 'FlexyPe':
            score += 15
        s = data.get('socials', {})
        if s.get('linkedin'):                   score += 8
        if s.get('instagram'):                  score += 4
        if s.get('facebook'):                   score += 3
        if data.get('hot_brand'):               score += 20
        if data.get('live_checkout'):           score += 5
        hc = len(hist)
        score += {0: 0, 1: 5, 2: 8}.get(hc, 10)
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
#  DOMAIN SCAN  (single Playwright pass — no inline double-check)
# ─────────────────────────────────────────────

def _empty_live() -> dict:
    return {
        'live_checkout': None, 'live_confidence': 0, 'live_evidence': [],
        'has_kwikpass': False, 'kwikpass_evidence': [],
        'all_network_requests': [], 'nav_ok': False, 'live_ambiguous': False,
    }


def _cheap_fingerprint(src: dict) -> dict:
    html = (src.get('raw_html') or '').lower()
    scripts = []
    for kw in ("gokwik", "shopflo", "fastrr", "shiprocket", "kwikpass",
               "razorpay", "magiccheckout", "cashfree", "simpl", "antigravity",
               "flexype"):
        if kw in html:
            scripts.append(kw)
    return {
        "shopify": bool(src.get('shopify')),
        "historical_checkouts": sorted(src.get('historical_checkouts', [])),
        "checkout_scripts": sorted(set(scripts)),
        "tech_stack": sorted(src.get('tech_stack', [])),
    }


def _fingerprint_hash(fp: dict) -> str:
    return hashlib.sha256(json.dumps(fp, sort_keys=True).encode("utf-8")).hexdigest()[:16]


async def scan_domain(browser, domain: str, semaphore: Semaphore,
                      gate: bool = False) -> dict:
    async with semaphore:
        start = time.time()
        logger.info(f'Scanning  {domain}')

        if not load_mock_config(domain) and not await asyncio.to_thread(domain_resolves, domain):
            logger.warning(f'{domain}: DNS does not resolve — skipping scan')
            return _empty_result(domain, start, dns_dead=True)

        src = await asyncio.to_thread(SourceDetector().scan, domain)

        gated_skip = False
        carried_live = None
        new_hash = None
        if gate and HASH_GATE_ENABLED and src.get('fetch_ok') and not load_mock_config(domain):
            try:
                fp = _cheap_fingerprint(src)
                new_hash = _fingerprint_hash(fp)
                fp_doc = await asyncio.to_thread(merchant_fingerprints.find_one, {"merchant": domain})
                existing_doc = await asyncio.to_thread(merchants.find_one, {"domain": domain})
                stored_hash = (fp_doc or {}).get("checkout_fp_hash")
                if stored_hash and stored_hash == new_hash and existing_doc:
                    gated_skip = True
                    carried_live = existing_doc.get("live_checkout")
                    logger.info(f'{domain}: hash unchanged ({new_hash}) — skipping Playwright, '
                                f'carrying checkout={carried_live}')
            except Exception as e:
                logger.warning(f'{domain}: hash gate check failed ({e}); doing full scan')

        if not src.get('fetch_ok'):
            live = _empty_live()
        elif gated_skip:
            live = _empty_live()
            live['live_checkout'] = carried_live
            live['live_confidence'] = 90 if carried_live else 0
            live['nav_ok'] = True
        else:
            try:
                live = await asyncio.wait_for(
                    PlaywrightDetector(browser).scan(domain),
                    timeout=DOMAIN_HARD_CAP
                )
            except asyncio.TimeoutError:
                logger.warning(f'{domain}: Playwright phase hit {DOMAIN_HARD_CAP}s hard cap')
                live = _empty_live()

        if new_hash and not gated_skip and src.get('fetch_ok'):
            try:
                await asyncio.to_thread(
                    merchant_fingerprints.update_one,
                    {"merchant": domain},
                    {"$set": {"checkout_fp_hash": new_hash, "checkout_fp_at": datetime.utcnow()}},
                    upsert=True
                )
            except Exception as e:
                logger.warning(f'{domain}: failed to store checkout_fp_hash ({e})')

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
            '_fetch_ok':            src.get('fetch_ok', False),
            '_rate_limited':        src.get('rate_limited', False),
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
        '_fetch_ok': False, '_rate_limited': False, '_nav_ok': False,
        '_live_ambiguous': False, '_dns_dead': dns_dead,
    }


def reading_trustworthy(r: dict) -> bool:
    if not r:
        return False
    if r.get('_dns_dead'):
        return False
    if r.get('_rate_limited'):
        # A rate-limited reading carries no real signal — never let it move state.
        return False
    if not r.get('_fetch_ok'):
        return False
    return True


def is_suspicious(r: dict) -> bool:
    if not r:
        return True
    if r.get('_dns_dead'):
        return False
    if r.get('_rate_limited'):
        # Don't churn the verify pass on a throttle event; the run-level retry
        # handles it. Treat as not-suspicious so it isn't re-scanned immediately.
        return False
    if not r.get('_fetch_ok'):
        if r.get('shopify') or r.get('emails') or r.get('title') or r.get('historical_checkouts'):
            return False
        return True
    if r.get('_live_ambiguous'):
        return True
    if r.get('has_kwikpass') and not r.get('live_checkout'):
        return True
    return False


def _strip_internal(r: dict) -> dict:
    return {k: v for k, v in r.items() if not k.startswith('_')}


# ─────────────────────────────────────────────
#  DEAD / INACTIVE STORE HANDLING  (soft, confirmed, reversible)
# ─────────────────────────────────────────────

ABANDON_AFTER = int(os.getenv("ABANDON_AFTER", "3"))


def active_merchant_filter(extra: dict = None) -> dict:
    q = {"active": {"$ne": False}}
    if extra:
        q.update(extra)
    return q


def mark_dead_reading(domain: str, reason: str):
    canonical = canonical_domain(domain)
    existing = merchants.find_one({"domain": canonical}) or {}
    dead_count = existing.get("dead_count", 0) + 1
    set_fields = {
        "dead_count": dead_count,
        "last_dead_reason": reason,
        "last_dead_at": datetime.utcnow(),
    }
    just_deactivated = False
    if dead_count >= ABANDON_AFTER and existing.get("active", True) is not False:
        set_fields["active"] = False
        set_fields["inactive_since"] = datetime.utcnow()
        just_deactivated = True
        logger.warning(f'{canonical}: deactivated after {dead_count} dead scans ({reason})')
        old_live = existing.get("live_checkout")
        if old_live and old_live.lower() == "flexype":
            FLEXY_CLOSED.append(canonical)
            fingerprint_history.insert_one({
                "merchant": canonical, "old_hash": "", "new_hash": "",
                "changes": {"live_checkout": {
                    "old": old_live,
                    "new": "Closed"}},
                "confirmed": True,
                "timestamp": datetime.utcnow()
            })
    else:
        logger.info(f'{canonical}: dead reading {dead_count}/{ABANDON_AFTER} ({reason})')
    merchants.update_one({"domain": canonical}, {"$set": set_fields}, upsert=True)
    return just_deactivated


def mark_active(domain: str):
    canonical = canonical_domain(domain)
    merchants.update_one(
        {"domain": canonical},
        {"$set": {"active": True, "dead_count": 0}}
    )


# ─────────────────────────────────────────────
#  CHANGE STATE MACHINE  (capture → confirm → promote)
# ─────────────────────────────────────────────

FLEXY_LEFTERS = []
FLEXY_JOINERS = []
FLEXY_CLOSED = []


def _is_flexype_departure(old_chk, new_chk) -> bool:
    return bool(old_chk) and old_chk.lower() == "flexype" and (not new_chk or new_chk.lower() != "flexype")


def _promote_change(canonical, old_chk, new_chk, reason, r):
    old_lower = (old_chk or "").lower()
    new_lower = (new_chk or "").lower()
    
    is_closed = (new_lower in ["none", "unknown", ""])

    if is_closed:
        if old_lower == "flexype":
            FLEXY_CLOSED.append(canonical)
    else:
        send_slack_notification(canonical, old_chk, new_chk, reason, status="confirmed")
        if old_lower == "flexype":
            FLEXY_LEFTERS.append(canonical)
        elif new_lower == "flexype":
            FLEXY_JOINERS.append(canonical)

    fingerprint_history.insert_one({
        "merchant": canonical, "old_hash": "", "new_hash": "",
        "changes": {"live_checkout": {
            "old": old_chk if old_chk else "None",
            "new": "Closed" if is_closed else (new_chk if new_chk else "None")}},
        "confirmed": True,
        "timestamp": datetime.utcnow()
    })
    if old_chk and old_chk.lower() not in ["none", "unknown"]:
        hists = r.get("historical_checkouts", [])
        if old_chk not in hists:
            hists.append(old_chk)
            r["historical_checkouts"] = hists


def _persist_result(r: dict, exclude_master: bool):
    canonical = canonical_domain(r["domain"])
    r["domain"] = canonical

    trustworthy = reading_trustworthy(r)
    reading_chk = r.get("live_checkout")

    if exclude_master:
        return

    try:
        existing = merchants.find_one({"domain": canonical}) or {}
        confirmed_chk = existing.get("live_checkout")
        state         = existing.get("checkout_state", "stable")
        pending_chk   = existing.get("pending_checkout")
        pending_count = existing.get("pending_count", 0)

        set_fields = {}

        if not trustworthy:
            r["live_checkout"]    = confirmed_chk
            set_fields["last_unreadable"] = datetime.utcnow()
            logger.info(f'{canonical}: untrustworthy reading — preserving confirmed={confirmed_chk}')
        else:
            if reading_chk == confirmed_chk:
                if state != "stable":
                    logger.info(f'{canonical}: reverted to confirmed {confirmed_chk}; clearing pending')
                set_fields.update({
                    "checkout_state": "stable",
                    "pending_checkout": None,
                    "pending_count": 0,
                })
                r["live_checkout"] = confirmed_chk
            else:
                if state == "pending_change" and reading_chk == pending_chk:
                    pending_count += 1
                else:
                    pending_chk = reading_chk
                    pending_count = 1

                if pending_count >= CONFIRMATIONS_REQUIRED or r.get("_force_confirm"):
                    logger.info(f'{canonical}: CONFIRMED {confirmed_chk} -> {reading_chk} '
                                f'({pending_count} agreeing or forced)')
                    _promote_change(canonical, confirmed_chk, reading_chk, "Scan Run (confirmed)", r)
                    set_fields.update({
                        "checkout_state": "stable",
                        "pending_checkout": None,
                        "pending_count": 0,
                    })
                    r["live_checkout"] = reading_chk
                else:
                    logger.info(f'{canonical}: pending {confirmed_chk} -> {reading_chk} '
                                f'({pending_count}/{CONFIRMATIONS_REQUIRED})')
                    set_fields.update({
                        "checkout_state": "pending_change",
                        "pending_checkout": reading_chk,
                        "pending_count": pending_count,
                        "pending_since": existing.get("pending_since") or datetime.utcnow(),
                    })
                    r["live_checkout"] = confirmed_chk

                    fingerprint_history.insert_one({
                        "merchant": canonical, "old_hash": "", "new_hash": "",
                        "changes": {"live_checkout_pending": {
                            "confirmed": confirmed_chk if confirmed_chk else "None",
                            "candidate": reading_chk if reading_chk else "None",
                            "count": pending_count}},
                        "confirmed": False,
                        "timestamp": datetime.utcnow()
                    })

                    if (FLEXYPE_DEPARTURE_ALERT_ON_PENDING
                            and pending_count == 1
                            and _is_flexype_departure(confirmed_chk, reading_chk)
                            and (reading_chk or "").lower() not in ["none", "unknown", ""]):
                        send_slack_notification(
                            canonical, confirmed_chk, reading_chk,
                            "pending", status="pending"
                        )

        if not existing and trustworthy:
            set_fields.update({
                "checkout_state": "stable",
                "pending_checkout": None,
                "pending_count": 0,
            })
            if reading_chk and reading_chk.lower() == "flexype":
                FLEXY_JOINERS.append(canonical)

    except Exception as e:
        logger.warning(f"State machine error for {canonical}: {e}")
        set_fields = {}

    payload = _strip_internal(r)
    payload.update(set_fields)
    merchants.update_one({"domain": canonical}, {"$set": payload}, upsert=True)


def persist_single_reading(r: dict, exclude_master: bool = False):
    if reading_trustworthy(r):
        try:
            mark_active(r["domain"])
        except Exception as e:
            logger.warning(f"mark_active failed for {r.get('domain')}: {e}")
    _persist_result(r, exclude_master)


# ─────────────────────────────────────────────
#  RUNNER
# ─────────────────────────────────────────────

async def _maybe_recheck(browser, r: dict, exclude_master: bool) -> dict:
    if not RECHECK_ON_CHANGE or exclude_master:
        return r
    if not reading_trustworthy(r):
        return r
    canonical = canonical_domain(r["domain"])
    try:
        existing = await asyncio.to_thread(merchants.find_one, {"domain": canonical})
    except Exception:
        return r
    confirmed = (existing or {}).get("live_checkout")
    val1 = r.get("live_checkout")
    if val1 == confirmed:
        return r

    logger.info(f'{canonical}: reading {confirmed} -> {val1} disagrees with confirmed; running verification round 2...')
    solo = Semaphore(1)
    try:
        r2 = await scan_domain(browser, canonical, solo, gate=False)
    except Exception as e:
        logger.warning(f'{canonical}: round 2 recheck failed ({e}); using original reading')
        return r

    val2 = r2.get("live_checkout") if reading_trustworthy(r2) else None

    if reading_trustworthy(r2) and val2 == val1:
        logger.info(f'{canonical}: verification round 2 CONFIRMS {val1} (2/2 matching)')
        r2["_force_confirm"] = True
        return r2

    logger.info(f'{canonical}: round 2 value is {val2} (expected {val1}); running verification round 3...')
    try:
        r3 = await scan_domain(browser, canonical, solo, gate=False)
    except Exception as e:
        logger.warning(f'{canonical}: round 3 recheck failed ({e}); returning best available')
        return r2 if reading_trustworthy(r2) else r

    val3 = r3.get("live_checkout") if reading_trustworthy(r3) else None

    readings = []
    if reading_trustworthy(r):
        readings.append(val1)
    if reading_trustworthy(r2):
        readings.append(val2)
    if reading_trustworthy(r3):
        readings.append(val3)

    from collections import Counter
    counts = Counter(readings)
    most_common, freq = counts.most_common(1)[0] if counts else (None, 0)

    if freq >= 2:
        if most_common == confirmed:
            logger.info(f'{canonical}: verification loop settled back to confirmed={confirmed} ({freq}/3 matching)')
            final_r = r3 if reading_trustworthy(r3) else (r2 if reading_trustworthy(r2) else r)
            final_r["live_checkout"] = confirmed
            return final_r
        else:
            logger.info(f'{canonical}: verification loop CONFIRMS change to {most_common} ({freq}/3 matching)')
            final_r = r3 if (reading_trustworthy(r3) and val3 == most_common) else (r2 if (reading_trustworthy(r2) and val2 == most_common) else r)
            final_r["_force_confirm"] = True
            final_r["live_checkout"] = most_common
            return final_r
    else:
        logger.info(f'{canonical}: verification loop unstable (no agreement); keeping confirmed={confirmed}')
        final_r = r3 if reading_trustworthy(r3) else (r2 if reading_trustworthy(r2) else r)
        final_r["live_checkout"] = confirmed
        return final_r


async def run_scanner(domains: list, max_concurrent: int = 6, exclude_master: bool = False,
                      gate: bool = True) -> list:
    delete_old_logs()
    results   = []
    semaphore = Semaphore(max_concurrent)
    logger.info(f'Starting scan  domains={len(domains)}  concurrency={max_concurrent}  '
                f'throttle_base={THROTTLE.base_rate}/s')

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=['--disable-dev-shm-usage', '--no-sandbox']
        )
        try:
            tasks = [scan_domain(browser, canonical_domain(d), semaphore, gate=gate) for d in domains]
            total = len(tasks)
            pbar  = ProgressBar(total)
            done  = 0
            print()
            for coro in asyncio.as_completed(tasks):
                r = await coro
                if r:
                    if not is_suspicious(r):
                        r = await _maybe_recheck(browser, r, exclude_master)
                        persist_single_reading(r, exclude_master)
                    results.append(r)
                done += 1
                live_c = sum(1 for x in results if x.get('live_checkout'))
                hist_c = sum(1 for x in results if x.get('historical_checkouts'))
                pbar.update(done, live_c, hist_c)
            print('\n')

            suspect_domains = [r['domain'] for r in results if is_suspicious(r)]
            if suspect_domains:
                logger.info(f'Verification pass: re-scanning {len(suspect_domains)} ambiguous domains (concurrency={VERIFY_CONCURRENCY})')
                print(f'  Verifying {len(suspect_domains)} ambiguous result(s)...')
                verify_sem = Semaphore(VERIFY_CONCURRENCY)
                vtasks = [scan_domain(browser, d, verify_sem, gate=False) for d in suspect_domains]
                recovered = 0
                for coro in asyncio.as_completed(vtasks):
                    vr = await coro
                    if not vr:
                        continue
                    canonical = canonical_domain(vr['domain'])
                    results = [x for x in results if canonical_domain(x['domain']) != canonical]
                    results.append(vr)
                    if not is_suspicious(vr):
                        recovered += 1
                    else:
                        log_failed(canonical, 'still ambiguous after re-verify')
                    vr = await _maybe_recheck(browser, vr, exclude_master)
                    persist_single_reading(vr, exclude_master)
                logger.info(f'Verification pass complete: cleanly resolved={recovered}/{len(suspect_domains)}')
                print(f'  Verification done: cleanly resolved {recovered}/{len(suspect_domains)}\n')
        finally:
            await browser.close()

    results = [_strip_internal(r) for r in results]
    logger.info(f'Scan complete  total={len(results)}')
    return results


async def recheck_flexype_departures(max_concurrent: int = 1) -> None:
    delete_old_logs()
    query = {
        "$and": [
            {"historical_checkouts": "FlexyPe"},
            {"$or": [
                {"live_checkout": {"$ne": "FlexyPe"}},
                {"checkout_state": "pending_change"},
            ]},
            {"active": {"$ne": False}},
        ]
    }
    targets = [m["domain"] for m in merchants.find(query, {"domain": 1})]
    logger.info(f'Cleanup: re-checking {len(targets)} suspected FlexyPe departures serially')
    print(f'  Re-checking {len(targets)} suspected FlexyPe departures (serial, full time)...')
    if not targets:
        print('  Nothing to recheck.')
        return

    corrected = 0
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True, args=['--disable-dev-shm-usage', '--no-sandbox'])
        try:
            sem = Semaphore(max_concurrent)
            for d in targets:
                canonical = canonical_domain(d)
                before = (merchants.find_one({"domain": canonical}) or {}).get("live_checkout")
                r = await scan_domain(browser, canonical, sem, gate=False)
                if not is_suspicious(r):
                    persist_single_reading(r, exclude_master=False)
                    after = (merchants.find_one({"domain": canonical}) or {}).get("live_checkout")
                    if before != after and after == "FlexyPe":
                        corrected += 1
                        print(f'  ✓ {canonical}: corrected back to FlexyPe')
                    elif r.get("live_checkout") == "FlexyPe":
                        r2 = await scan_domain(browser, canonical, sem, gate=False)
                        if not is_suspicious(r2):
                            persist_single_reading(r2, exclude_master=False)
                            after2 = (merchants.find_one({"domain": canonical}) or {}).get("live_checkout")
                            if after2 == "FlexyPe":
                                corrected += 1
                                print(f'  ✓ {canonical}: corrected back to FlexyPe (2nd obs)')
        finally:
            await browser.close()

    logger.info(f'Cleanup complete: corrected {corrected}/{len(targets)} back to FlexyPe')
    print(f'\n  Cleanup done: {corrected}/{len(targets)} were still on FlexyPe and corrected.\n')


# ─────────────────────────────────────────────
#  MAIN
# ─────────────────────────────────────────────

def main():
    import argparse
    parser = argparse.ArgumentParser(description="FlexyPe Merchant Intelligence Scanner")
    parser.add_argument("--recheck-flexype-departures", action="store_true",
                        help="One-time cleanup: re-scan suspected FlexyPe departures serially and correct false ones")
    parser.add_argument("--no-gate", action="store_true",
                        help="Disable the hash-gate (force a full Playwright scan of every domain)")
    parser.add_argument("--concurrency", type=int, default=int(os.getenv("SCAN_CONCURRENCY", "6")),
                        help="Max concurrent domain scans (default 6)")
    args = parser.parse_args()

    print('\n' + '─' * 64)
    print('  FlexyPe  ·  Merchant Intelligence Scanner')
    print('─' * 64 + '\n')

    if args.recheck_flexype_departures:
        asyncio.run(recheck_flexype_departures(max_concurrent=1))
        return

    try:
        df = pd.read_csv(INPUT_CSV)
        domains = df['domain'].dropna().tolist()
        logger.info(f'Loaded {len(domains)} domains from {INPUT_CSV}')
    except FileNotFoundError:
        logger.error(f'Input file not found: {INPUT_CSV}')
        print(f'\n  Error: {INPUT_CSV} not found.')
        print('  Create a CSV with a "domain" column.\n')
        return

    results = asyncio.run(run_scanner(domains, max_concurrent=args.concurrency, gate=not args.no_gate))

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

    webhook_url = os.getenv("SLACK_WEBHOOK_URL")
    if webhook_url:
        flexype_count = sum(1 for r in results if r.get('live_checkout') == 'FlexyPe')
        left_str = ", ".join(f"`{d}`" for d in FLEXY_LEFTERS) if FLEXY_LEFTERS else "None"
        joined_str = ", ".join(f"`{d}`" for d in FLEXY_JOINERS) if FLEXY_JOINERS else "None"
        closed_str = ", ".join(f"`{d}`" for d in FLEXY_CLOSED) if FLEXY_CLOSED else "None"
        summary_text = (
            f"🚀 *Active Scan Completed Summary (`main_scraper.py`)*\n"
            f"• *Total Scanned:* {len(domains)}\n"
            f"• *Total with FlexyPe (confirmed):* {flexype_count}\n"
            f"• *Confirmed left FlexyPe this run:* {len(FLEXY_LEFTERS)} ({left_str})\n"
            f"• *Confirmed joined FlexyPe:* {len(FLEXY_JOINERS)} ({joined_str})\n"
            f"• *Store Closed (was FlexyPe):* {len(FLEXY_CLOSED)} ({closed_str})\n"
        )
        try:
            requests.post(webhook_url, json={"text": summary_text}, timeout=10)
            print("[Slack Success] Sent scan run completion summary.")
        except Exception as e:
            print(f"[Slack Error] Failed to send completion summary: {e}")

    sheet_id = os.getenv("GOOGLE_SHEET_ID")
    if sheet_id:
        try:
            from sync_to_sheets import sync_merchants
            print("\n[Google Sheets] Starting auto-sync...")
            sync_merchants()
        except Exception as e:
            print(f"\n[Google Sheets Error] Auto-sync failed: {e}")


if __name__ == '__main__':
    main()