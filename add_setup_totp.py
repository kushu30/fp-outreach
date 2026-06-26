import sys
import re

with open("backend/server.py", "r") as f:
    content = f.read()

new_endpoint = """
@app.route("/api/auth/setup-totp", methods=["POST"])
def auth_setup_totp():
    data = request.json or {}
    email = (data.get("email") or "").strip().lower()
    password = data.get("password", "")
    secret = data.get("secret", "").strip().upper()
    
    if not email or not password or not secret:
        return jsonify({"error": "Missing fields"}), 400
        
    user = db.fp_users.find_one({"email": email})
    if not user:
        return jsonify({"error": "Invalid credentials"}), 401
        
    import bcrypt
    if not bcrypt.checkpw(password.encode("utf-8"), user["password_hash"]):
        return jsonify({"error": "Invalid credentials"}), 401
        
    db.fp_users.update_one(
        {"email": email},
        {"$set": {
            "secret": secret,
            "twoFAEnabled": True
        }}
    )
    return jsonify({"ok": True})
"""

# Insert before @app.route("/api/users/change-password")
content = content.replace(
    '@app.route("/api/users/change-password", methods=["POST"])',
    new_endpoint + '\n@app.route("/api/users/change-password", methods=["POST"])'
)

with open("backend/server.py", "w") as f:
    f.write(content)

print("Endpoint added")
