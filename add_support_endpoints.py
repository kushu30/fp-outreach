import sys

with open("backend/server.py", "r") as f:
    content = f.read()

new_endpoints = """
@app.route("/api/support/churned", methods=["GET"])
@require_role("admin", "supportteammember")
def get_churned_stores():
    from datetime import datetime
    # Find all fingerprint changes where old_checkout was FlexyPe and new is different
    # Or just any change where FlexyPe is removed
    cursor = fingerprint_history.find({
        "old_checkout": {"$regex": ".*FlexyPe.*", "$options": "i"},
        "new_checkout": {"$not": {"$regex": ".*FlexyPe.*", "$options": "i"}}
    }).sort("timestamp", -1)
    
    out = []
    for d in cursor:
        d["_id"] = str(d["_id"])
        if isinstance(d.get("timestamp"), datetime):
            d["timestamp"] = d["timestamp"].isoformat()
        
        # We need notes and assigned fields
        if "notes" not in d:
            d["notes"] = ""
        if "assigned" not in d:
            d["assigned"] = ""
            
        out.append(d)
    return jsonify(out)

@app.route("/api/support/churned/<change_id>", methods=["PATCH"])
@require_role("admin", "supportteammember")
def update_churned_store(change_id):
    from bson.objectid import ObjectId
    data = request.json or {}
    
    updates = {}
    if "notes" in data:
        updates["notes"] = str(data["notes"])
    if "assigned" in data:
        updates["assigned"] = str(data["assigned"])
        
    if not updates:
        return jsonify({"ok": True})
        
    res = fingerprint_history.update_one(
        {"_id": ObjectId(change_id)},
        {"$set": updates}
    )
    
    if res.matched_count == 0:
        return jsonify({"error": "Change not found"}), 404
        
    return jsonify({"ok": True})
"""

# Append just before the end or before error handlers
content += "\n" + new_endpoints + "\n"

with open("backend/server.py", "w") as f:
    f.write(content)

print("Support endpoints added")
