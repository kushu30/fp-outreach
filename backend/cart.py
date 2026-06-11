import requests
import pandas as pd
from bs4 import BeautifulSoup
from concurrent.futures import ThreadPoolExecutor

INPUT_CSV = "data/domains.csv"
OUTPUT_CSV = "cart_detection.csv"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 "
        "(Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 "
        "(KHTML, like Gecko) "
        "Chrome/137.0.0.0 Safari/537.36"
    )
}

CART_PROVIDERS = {
    "UpCart": [
        "upcart"
    ],
    "iCart": [
        "icart"
    ],
    "qikify": [
        "qikify"
    ],
    "Cartly": [
        "cartly"
    ],
    "Monster Cart": [
        "monster-cart"
    ],
    "Slide Cart": [
        "slide-cart"
    ],
    "Cart Drawer": [
        "cart-drawer"
    ]
}

def normalize_url(domain):

    domain = str(domain).strip()

    if not domain.startswith(("http://", "https://")):
        domain = f"https://{domain}"

    return domain

def fetch_html(url):

    try:

        response = requests.get(
            url,
            headers=HEADERS,
            timeout=10
        )

        return response.text.lower()

    except Exception:

        return ""

def detect_cart(domain):

    print(f"Scanning {domain}")

    homepage = fetch_html(
        normalize_url(domain)
    )

    cart_page = fetch_html(
        normalize_url(domain) + "/cart"
    )

    combined = homepage + "\n" + cart_page

    detected = []

    for provider, patterns in CART_PROVIDERS.items():

        if any(
            pattern.lower() in combined
            for pattern in patterns
        ):

            detected.append(provider)

    cart_type = "Unknown"

    if "cart-drawer" in combined:
        cart_type = "Drawer Cart"

    elif "/cart" in combined:
        cart_type = "Native Cart"

    return {
        "domain": domain,
        "cart_provider": ", ".join(detected),
        "cart_type": cart_type
    }

domains = pd.read_csv(
    INPUT_CSV
)

domain_col = domains.columns[0]

stores = (
    domains[domain_col]
    .dropna()
    .astype(str)
    .tolist()
)

results = []

with ThreadPoolExecutor(
    max_workers=30
) as executor:

    results = list(
        executor.map(
            detect_cart,
            stores
        )
    )

pd.DataFrame(
    results
).to_csv(
    OUTPUT_CSV,
    index=False
)

print(
    f"Saved {OUTPUT_CSV}"
)