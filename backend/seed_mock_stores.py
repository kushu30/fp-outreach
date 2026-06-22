import json
import os
import sys
from datetime import datetime

# Setup path so it finds db.py
sys.path.insert(0, '/Users/kushagrashukla/coding/gokwik-leads/backend')
from db import merchants

def seed():
    json_path = "/Users/kushagrashukla/coding/gokwik-leads/backend/mock_stores.json"
    if not os.path.exists(json_path):
        print(f"Error: {json_path} not found.")
        return

    with open(json_path, 'r', encoding='utf-8') as f:
        mock_data = json.load(f)

    for domain, config in mock_data.items():
        existing = merchants.find_one({"domain": domain})
        
        # Build base merchant doc
        doc = {
            "domain": domain,
            "shopify": config["shopify"],
            "live_checkout": config["live_checkout"],
            "live_confidence": 95 if config["live_checkout"] else 0,
            "live_evidence": config["checkout_scripts"] if config["live_checkout"] else [],
            "historical_checkouts": config["checkout_providers"],
            "has_kwikpass": config["has_kwikpass"],
            "kwikpass_evidence": [],
            "emails": config["emails"],
            "phone_numbers": config["phone_numbers"],
            "whatsapp_link": "",
            "whatsapp_number": "",
            "myshopify_domain": f"{domain.split('.')[0]}.myshopify.com" if config["shopify"] else "",
            "socials": {"linkedin": "", "instagram": "", "facebook": "", "twitter": "", "youtube": ""},
            "tech_stack": config["app_signatures"],
            "title": config["title"],
            "description": config["description"],
            "page_hash": "mockhash12345",
            "hot_brand": False,
            "lead_score": 0,
            "priority": "LOW",
            "last_scan": datetime.utcnow().strftime('%Y-%m-%d %H:%M'),
            "scan_duration": 0.5,
            "status": "Not Contacted",
            "notes": "Mock Test Store"
        }
        
        # Calculate initial score
        from main_scraper import ScoringEngine
        doc["hot_brand"] = ScoringEngine.detect_hot_brand(doc)
        doc["hot_brand_reason"] = ScoringEngine.detect_hot_brand_reason(doc)
        doc["lead_score"] = ScoringEngine.calculate_score(doc)
        doc["priority"] = ScoringEngine.get_priority(doc["lead_score"])

        if existing:
            # Update
            merchants.update_one({"domain": domain}, {"$set": doc})
            print(f"Updated mock store: {domain} (Score: {doc['lead_score']}, Priority: {doc['priority']})")
        else:
            # Insert
            merchants.insert_one(doc)
            print(f"Seeded mock store: {domain} (Score: {doc['lead_score']}, Priority: {doc['priority']})")

if __name__ == "__main__":
    seed()
