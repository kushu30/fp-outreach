import sys
sys.path.append('backend')
from server import db, _hash_password
from datetime import datetime

users = [
    {"name": "Kushagra", "email": "kushagra@flexype.in", "role": "admin"},
    {"name": "Roshan", "email": "roshan@flexype.in", "role": "admin"},
    {"name": "Aman", "email": "aman@flexype.in", "role": "salesteammember"},
    {"name": "Rahul", "email": "rahul@flexype.in", "role": "salesteammember"},
    {"name": "Priya", "email": "priya@flexype.in", "role": "salesteammember"},
    {"name": "Harsh", "email": "harsh@flexype.in", "role": "supportteammember"},
    {"name": "Rohit", "email": "rohit@flexype.in", "role": "supportteammember"},
    {"name": "Ankit", "email": "ankit@flexype.in", "role": "supportteammember"}
]

default_hash = _hash_password("flexype123")
now = datetime.utcnow()

for u in users:
    existing = db.fp_users.find_one({"email": u["email"]})
    if not existing:
        u["password_hash"] = default_hash
        u["created_at"] = now
        u["active"] = True
        db.fp_users.insert_one(u)
        print(f"Inserted {u['email']} as {u['role']}")
    else:
        print(f"User {u['email']} already exists")
