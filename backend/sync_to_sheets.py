import os
import sys
from datetime import datetime
from dotenv import load_dotenv
from googleapiclient.discovery import build
from google.oauth2 import service_account
from db import merchants, fingerprint_history

# Load environment variables
load_dotenv()


def get_sheets_service():
    scopes = ["https://www.googleapis.com/auth/spreadsheets"]
    creds_path = os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE", "credentials.json")
    if not os.path.exists(creds_path):
        # check parent directory too
        creds_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "credentials.json")
        if not os.path.exists(creds_path):
            raise FileNotFoundError(
                f"Service account file not found at '{creds_path}'. "
                "Please place your credentials.json file in the backend directory."
            )

    creds = service_account.Credentials.from_service_account_file(
        creds_path, scopes=scopes
    )
    return build("sheets", "v4", credentials=creds)


def extract_store_name(domain: str) -> str:
    """Helper to clean domain names into readable Title Case store names."""
    d = domain.lower().strip()
    if d.startswith("www."):
        d = d[4:]
    if d.endswith(".myshopify.com"):
        name = d[:-14]
    else:
        parts = d.split('.')
        # If subdomain like shop.company.com, extract "company"
        if len(parts) > 2 and parts[0] in ["shop", "store", "app", "prod", "checkout"]:
            name = parts[1]
        else:
            name = parts[0]
    return name.replace("-", " ").replace("_", " ").title()


def sync_merchants():
    sheet_id = os.getenv("GOOGLE_SHEET_ID")
    if not sheet_id:
        print("Error: GOOGLE_SHEET_ID environment variable is not set in .env file.")
        sys.exit(1)

    try:
        service = get_sheets_service()
    except Exception as e:
        print(f"Authentication Error: {e}")
        sys.exit(1)

    sheet_title = 'Notes'

    print("Fetching change history and merchant details from database...")
    # Fetch all confirmed checkout changes sorted by newest first
    cursor = fingerprint_history.find({}).sort("timestamp", -1)
    rows = []

    # Headers
    headers = ["Date", "Store Name", "Domain", "Churned to"]
    rows.append(headers)

    for h in cursor:
        domain = h.get("merchant", "")
        if not domain:
            continue

        lc_change = h.get("changes", {}).get("live_checkout", {})
        old_val = lc_change.get("old") or "None"
        new_val = lc_change.get("new") or "None"

        if str(old_val).strip().lower() != "flexype":
            continue

        store_name = extract_store_name(domain)

        t = h.get("timestamp")
        t_str = t.strftime('%Y-%m-%d %H:%M') if isinstance(t, datetime) else str(t or "")

        rows.append([
            t_str,
            store_name,
            domain,
            new_val
        ])

    print(f"Syncing {len(rows) - 1} confirmed changes to Google Sheet '{sheet_title}' (ID: {sheet_id})...")

    try:
        # Clear existing data first
        service.spreadsheets().values().clear(
            spreadsheetId=sheet_id,
            range=f"'{sheet_title}'!A1:D",
            body={}
        ).execute()

        # Write new values
        body = {
            "values": rows
        }
        result = service.spreadsheets().values().update(
            spreadsheetId=sheet_id,
            range=f"'{sheet_title}'!A1",
            valueInputOption="RAW",
            body=body
        ).execute()

        print(f"Success! Updated {result.get('updatedRows')} rows in Google Sheets.")
    except Exception as e:
        print(f"API Error updating spreadsheet: {e}")
        print("Please verify that the service account email has been shared as an 'Editor' on the Google Sheet.")


if __name__ == "__main__":
    sync_merchants()
