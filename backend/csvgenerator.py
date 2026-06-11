import pandas as pd

INPUT_FILE = "data/results.csv"

VALID_PROVIDERS = {
    "GoKwik",
    "Shopflo",
    "Fastrr",
    "Razorpay",
    "Simpl",
    "FlexyPe",
    "ecom360",
    "Magic Checkout"
}

df = pd.read_csv(
    INPUT_FILE,
    on_bad_lines="skip"
)

df = df.drop_duplicates(
    subset=["domain"],
    keep="first"
)

df = df[
    df["domain"]
    .astype(str)
    .str.contains(r"\.")
]

df = df[
    ~df["domain"]
    .astype(str)
    .str.startswith("–")
]

df = df[
    ~df["domain"]
    .astype(str)
    .str.startswith("-")
]

def clean_shopify(value):

    return (
        "Yes"
        if str(value).lower() == "true"
        else "No"
    )

def clean_live(value):

    if pd.isna(value):
        return ""

    value = str(value).strip()

    if value in VALID_PROVIDERS:
        return value

    return ""

def clean_historical(value):

    if pd.isna(value):
        return ""

    providers = []

    for item in str(value).split(","):

        item = item.strip()

        if item in VALID_PROVIDERS:
            providers.append(item)

    providers = list(
        dict.fromkeys(providers)
    )

    return ", ".join(providers)

output = pd.DataFrame({
    "name": df["domain"],
    "shopify": df["shopify"].apply(
        clean_shopify
    ),
    "live_checkout": df[
        "live_checkout"
    ].apply(clean_live),
    "historical_checkouts": df[
        "historical_checkouts"
    ].apply(clean_historical)
})

output = output[
    output["name"]
    .astype(str)
    .str.contains(r"\.")
]

output.to_csv(
    "checkout_summary.csv",
    index=False
)

print(
    f"Saved {len(output)} rows"
)