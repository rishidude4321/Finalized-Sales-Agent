"""
Minimal Flask server for Sasha's Sales Support Agent.
Handles marking follow‑ups as done and undoing them.
Runs on localhost:8500.
"""

import json
import hashlib
import time
from datetime import datetime, timedelta
from pathlib import Path
from flask import Flask, request, jsonify

from src.utils.config import DONE_SECRET

app = Flask(__name__)
DATA_FILE = Path("done_followups.json")

def load_data():
    if DATA_FILE.exists():
        with open(DATA_FILE, "r") as f:
            return json.load(f)
    return {}

def save_data(data):
    with open(DATA_FILE, "w") as f:
        json.dump(data, f, indent=2)

def generate_hash(contact, action):
    raw = f"{contact}|{action}".lower().strip()
    return hashlib.md5(raw.encode()).hexdigest()[:12]

@app.route("/done")
def mark_done():
    """Soft‑delete a follow‑up: /done?id=HASH&token=SECRET&action=...&reasoning=...&contact=..."""
    item_id = request.args.get("id")
    token = request.args.get("token")
    if token != DONE_SECRET:
        return "Unauthorized", 401
    if not item_id:
        return "Missing id", 400
    data = load_data()
    data[item_id] = {
        "deleted": True,
        "deleted_at": datetime.utcnow().isoformat(),
        "action": request.args.get("action", ""),
        "reasoning": request.args.get("reasoning", ""),
        "contact": request.args.get("contact", ""),
    }
    save_data(data)
    return """<h2>✅ Marked as done. This tab will close automatically.</h2>
<script>setTimeout(function(){ window.close(); }, 1500);</script>"""

@app.route("/recent")
def recent_deletions():
    """Show recently deleted items with Undo links."""
    token = request.args.get("token")
    if token != DONE_SECRET:
        return "Unauthorized", 401
    data = load_data()
    recent = {}
    cutoff = datetime.utcnow() - timedelta(days=7)
    for item_id, info in data.items():
        if info.get("deleted"):
            deleted_at = datetime.fromisoformat(info["deleted_at"])
            if deleted_at > cutoff:
                recent[item_id] = info
    if not recent:
        return "<h2>No recently marked‑done items.</h2>"
    html = "<h2>Recently marked done (click to undo):</h2><ul>"
    for item_id, info in recent.items():
        action = info.get("action", item_id)
        reasoning = info.get("reasoning", "")
        contact = info.get("contact", "")
        display = f"{action} — {reasoning} (Contact: {contact})"
        html += f'<li>{display} <a href="/undo?id={item_id}&token={token}">[Undo]</a> (deleted {info["deleted_at"][:10]})</li>'
    html += "</ul>"
    return html

@app.route("/undo")
def undo():
    """Restore a soft‑deleted item: /undo?id=HASH&token=SECRET"""
    item_id = request.args.get("id")
    token = request.args.get("token")
    if token != DONE_SECRET:
        return "Unauthorized", 401
    data = load_data()
    if item_id in data:
        del data[item_id]
        save_data(data)
        return "<h2>✅ Restored! You can close this window.</h2>"
    return "Item not found", 404

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=8500, debug=False)