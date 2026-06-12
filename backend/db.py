from pymongo import MongoClient
from dotenv import load_dotenv
import os

load_dotenv()

uri = os.getenv("MONGO_URI")

client = MongoClient(uri)

db = client["merchant_intelligence"]

merchants = db["merchants"]
merchant_fingerprints = db["merchant_fingerprints"]
fingerprint_history = db["fingerprint_history"]
playwright_runs = db["playwright_runs"]