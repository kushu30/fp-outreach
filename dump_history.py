import sys
from backend.db import fingerprint_history
from bson.json_util import dumps

records = fingerprint_history.find().sort("timestamp", -1).limit(5)
print(dumps(records, indent=2))
