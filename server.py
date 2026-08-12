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
from src.services.hubspot_client import HubSpotClient
from src.utils.config import DONE_SECRET, SUPPORT_EMAIL, AGENT_STATUS_FILE, CONTROL_CENTER_FLAG
from src.services.conversation_tree import ConversationTreeBuilder
import datetime
from src.services.graph_client import GraphClient
from src.services.hubspot_client import HubSpotClient

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

DEAL_SUGGESTIONS_FILE = "deal_suggestions.json"

def load_deal_suggestions():
    path = Path(DEAL_SUGGESTIONS_FILE)
    if path.exists():
        with open(path, "r") as f:
            return json.load(f)
    return {}

def save_deal_suggestions(data):
    with open(DEAL_SUGGESTIONS_FILE, "w") as f:
        json.dump(data, f, indent=2, default=str)

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
    return """<h2>✅ Marked as done. You can close this tab.</h2>
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

@app.route("/create_task")
def create_task():
    """Create a HubSpot task for a follow‑up item."""
    item_hash = request.args.get("hash")
    email = request.args.get("email")
    title = request.args.get("title")
    duedays = request.args.get("duedays", "3")
    token = request.args.get("token")

    if token != DONE_SECRET:
        return "Unauthorized", 401
    if not item_hash or not email or not title:
        return "Missing parameters", 400

    # Prevent duplicate tasks
    tasks = load_created_tasks()
    if item_hash in tasks:
        return "<h2>⚠️ Task already created.</h2>"

    # Find contact in HubSpot
    hubspot = HubSpotClient()
    contact_id = hubspot.get_contact_by_email(email)
    if not contact_id:
        return "<h2>❌ Contact not found in HubSpot. Cannot create task.</h2>", 404

    # Calculate due date
    from datetime import date, timedelta
    due_date = (date.today() + timedelta(days=int(duedays))).isoformat()

    success = hubspot.create_task(contact_id, title, due_date)
    if success:
        tasks[item_hash] = {"title": title, "due_date": due_date, "created": str(date.today())}
        save_created_tasks(tasks)
        return "<h2>✅ Task created in HubSpot! You can close this window.</h2>"
    else:
        return "<h2>❌ Failed to create task. Please try again.</h2>", 500

CREATED_TASKS_FILE = "created_tasks.json"

def load_created_tasks():
    path = Path(CREATED_TASKS_FILE)
    if path.exists():
        with open(path, "r") as f:
            return json.load(f)
    return {}

def save_created_tasks(data):
    with open(CREATED_TASKS_FILE, "w") as f:
        json.dump(data, f, indent=2, default=str)

@app.route("/approve_deal")
def approve_deal():
    """Approve a deal stage suggestion: /approve_deal?id=...&token=..."""
    suggestion_id = request.args.get("id")
    token = request.args.get("token")
    if token != DONE_SECRET:
        return "Unauthorized", 401

    data = load_deal_suggestions()
    if suggestion_id not in data:
        return "Suggestion not found", 404

    suggestion = data[suggestion_id]
    if suggestion.get("status") != "pending":
        return "Already processed", 200

    # Update deal stage in HubSpot
    hubspot = HubSpotClient()
    success = hubspot.update_deal_stage(suggestion["deal_id"], suggestion["next_stage_id"])

    if success:
        suggestion["status"] = "approved"
        save_deal_suggestions(data)
        return "<h2>✅ Deal stage updated! You can close this window.</h2>"
    else:
        return "<h2>❌ Failed to update deal stage. Please try again.</h2>", 500

@app.route("/deny_deal")
def deny_deal():
    """Deny a deal stage suggestion: /deny_deal?id=...&token=..."""
    suggestion_id = request.args.get("id")
    token = request.args.get("token")
    if token != DONE_SECRET:
        return "Unauthorized", 401

    data = load_deal_suggestions()
    if suggestion_id in data:
        data[suggestion_id]["status"] = "denied"
        save_deal_suggestions(data)
    return "<h2>✅ Suggestion denied. You can close this window.</h2>"

@app.route("/conversation")
def view_conversation():
    """Serve the conversation tree as a standalone HTML page."""
    email = request.args.get("email")
    token = request.args.get("token")
    days = request.args.get("days", default=30, type=int)
    if token != DONE_SECRET:
        return "Unauthorized", 401
    if not email:
        return "Missing email", 400

    builder = ConversationTreeBuilder()
    html = builder.build_tree_html(email, days)
    return html

def load_agent_status():
    path = Path(AGENT_STATUS_FILE)
    if path.exists():
        with open(path, "r") as f:
            return json.load(f)
    return {}

def save_agent_status(data):
    with open(AGENT_STATUS_FILE, "w") as f:
        json.dump(data, f, indent=2, default=str)

@app.route("/control")
def control_center_page():
    token = request.args.get("token")
    if token != DONE_SECRET:
        return "Unauthorized", 401
    return """
    <html><body>
    <h2>🛰️ Sales Support Agent – Control Centre</h2>
    <ul>
      <li><a href="/tree?token={token}">📋 Open Conversation Tree</a></li>
      <li><a href="/report?token={token}">⚠️ Report an Issue</a></li>
      <li><a href="/request?token={token}">💡 Request Automation</a></li>
      <li><a href="/health?token={token}">🩺 Health Check</a></li>
    </ul>
    </body></html>
    """.format(token=token)

@app.route("/tree")
def tree_search_page():
    token = request.args.get("token")
    if token != DONE_SECRET:
        return "Unauthorized", 401

    # Build list of recent external contacts to populate datalist
    graph = GraphClient()
    recent_emails = graph.get_recent_emails(days=30, top=200)
    contact_options = []
    seen = set()
    for mail in recent_emails:
        addr = mail.get("from_address", "")
        if addr and "@" in addr and not addr.endswith("@reachpathways.com") and addr not in seen:
            seen.add(addr)
            contact_options.append(f'<option value="{addr}">')
    datalist = "\n".join(contact_options)

    return f"""
    <html><body>
    <h2>📋 Conversation Tree</h2>
    <p>Enter the contact's email address:</p>
    <form action="/conversation" method="GET">
      <input type="hidden" name="token" value="{token}">
      <input list="contact-list" name="email" placeholder="e.g. bob@acme.com" required>
      <datalist id="contact-list">
        {datalist}
      </datalist>
      <button type="submit">View Tree</button>
    </form>
    </body></html>
    """

@app.route("/health")
def health_page():
    token = request.args.get("token")
    if token != DONE_SECRET:
        return "Unauthorized", 401
    status = load_agent_status()
    last_briefing = status.get("last_briefing_time", "Never")
    last_prep = status.get("last_meeting_prep_time", "Never")
    followups_today = status.get("followups_today", 0)
    drafts_today = status.get("drafts_today", 0)
    tasks_created = status.get("tasks_created", 0)
    errors = status.get("error_count", 0)
    return f"""
    <html><body>
    <h2>🩺 Agent Health</h2>
    <ul>
      <li><strong>Server:</strong> Running</li>
      <li><strong>Last Daily Briefing:</strong> {last_briefing}</li>
      <li><strong>Last Meeting Prep:</strong> {last_prep}</li>
      <li><strong>Follow‑ups detected today:</strong> {followups_today}</li>
      <li><strong>Drafts created today:</strong> {drafts_today}</li>
      <li><strong>HubSpot tasks created:</strong> {tasks_created}</li>
      <li><strong>Recent error count:</strong> {errors}</li>
    </ul>
    </body></html>
    """

@app.route("/report")
def report_form():
    token = request.args.get("token")
    if token != DONE_SECRET:
        return "Unauthorized", 401
    return f"""
    <html><body>
    <h2>⚠️ Report an Issue</h2>
    <form action="/submit_report" method="POST">
      <input type="hidden" name="token" value="{token}">
      <p>What went wrong?</p>
      <textarea name="message" rows="6" cols="60" required></textarea><br>
      <button type="submit">Submit to Rishi</button>
    </form>
    </body></html>
    """

@app.route("/submit_report", methods=["POST"])
def submit_report():
    token = request.form.get("token")
    if token != DONE_SECRET:
        return "Unauthorized", 401
    message = request.form.get("message", "")
    if not message.strip():
        return "Message cannot be empty", 400
    graph = GraphClient()
    success = graph.send_mail(
        to=SUPPORT_EMAIL,
        subject="Sales Agent Issue Report",
        body=f"Issue reported at {datetime.datetime.now()}:\n\n{message}",
        content_type="Text",
    )
    if success:
        return "<h2>✅ Report sent. Thank you!</h2>"
    return "<h2>❌ Failed to send report. Please try again.</h2>", 500

@app.route("/request")
def request_form():
    token = request.args.get("token")
    if token != DONE_SECRET:
        return "Unauthorized", 401
    return f"""
    <html><body>
    <h2>💡 Request Automation</h2>
    <form action="/submit_request" method="POST">
      <input type="hidden" name="token" value="{token}">
      <p>What repetitive task do you want automated?</p>
      <textarea name="message" rows="6" cols="60" required></textarea><br>
      <button type="submit">Send Request</button>
    </form>
    </body></html>
    """

@app.route("/submit_request", methods=["POST"])
def submit_request():
    token = request.form.get("token")
    if token != DONE_SECRET:
        return "Unauthorized", 401
    message = request.form.get("message", "")
    if not message.strip():
        return "Message cannot be empty", 400
    graph = GraphClient()
    success = graph.send_mail(
        to=SUPPORT_EMAIL,
        subject="Sales Agent Automation Request",
        body=f"Automation request at {datetime.datetime.now()}:\n\n{message}",
        content_type="Text",
    )
    if success:
        return "<h2>✅ Request sent. Thank you!</h2>"
    return "<h2>❌ Failed to send request. Please try again.</h2>", 500

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=8500, debug=False)