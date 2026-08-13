"""
Minimal Flask server for Sasha's Sales Support Agent.
Handles marking follow‑ups as done, undoing them,
deal stage approvals/denials, HubSpot task creation,
conversation tree, control centre, report/request forms, and health checks.
Runs on localhost:8500.
"""

import json
import hashlib
from datetime import datetime, timedelta, timezone
from pathlib import Path

from flask import Flask, request, render_template

from src.services.graph_client import GraphClient
from src.services.hubspot_client import HubSpotClient
from src.services.conversation_tree import ConversationTreeBuilder
from src.utils.config import (
    DONE_SECRET,
    SUPPORT_EMAIL,
    AGENT_STATUS_FILE,
    SERPER_API_KEY,
)

app = Flask(__name__)

# ---------- File constants ----------
DATA_FILE = Path("done_followups.json")
DEAL_SUGGESTIONS_FILE = Path("deal_suggestions.json")
CREATED_TASKS_FILE = Path("created_tasks.json")
AGENT_STATUS_PATH = Path(AGENT_STATUS_FILE)

# ---------- Generic helpers ----------
def _now_iso():
    return datetime.now(timezone.utc).isoformat()


def load_done_followups():
    if DATA_FILE.exists():
        with open(DATA_FILE, "r") as f:
            return json.load(f)
    return {}


def save_done_followups(data):
    with open(DATA_FILE, "w") as f:
        json.dump(data, f, indent=2, default=str)


def load_deal_suggestions():
    if DEAL_SUGGESTIONS_FILE.exists():
        with open(DEAL_SUGGESTIONS_FILE, "r") as f:
            return json.load(f)
    return {}


def save_deal_suggestions(data):
    with open(DEAL_SUGGESTIONS_FILE, "w") as f:
        json.dump(data, f, indent=2, default=str)


def load_created_tasks():
    if CREATED_TASKS_FILE.exists():
        with open(CREATED_TASKS_FILE, "r") as f:
            return json.load(f)
    return {}


def save_created_tasks(data):
    with open(CREATED_TASKS_FILE, "w") as f:
        json.dump(data, f, indent=2, default=str)


def load_agent_status():
    if AGENT_STATUS_PATH.exists():
        with open(AGENT_STATUS_PATH, "r") as f:
            return json.load(f)
    return {}


def save_agent_status(data):
    with open(AGENT_STATUS_PATH, "w") as f:
        json.dump(data, f, indent=2, default=str)


def generate_hash(contact, action):
    raw = f"{contact}|{action}".lower().strip()
    return hashlib.md5(raw.encode()).hexdigest()[:12]


# ---------- Follow-up done/undo ----------
@app.route("/done")
def mark_done():
    item_id = request.args.get("id")
    token = request.args.get("token")
    if token != DONE_SECRET:
        return "Unauthorized", 401
    if not item_id:
        return "Missing id", 400

    data = load_done_followups()
    data[item_id] = {
        "deleted": True,
        "deleted_at": _now_iso(),
        "action": request.args.get("action", ""),
        "reasoning": request.args.get("reasoning", ""),
        "contact": request.args.get("contact", ""),
    }
    save_done_followups(data)
    return (
        "<h2>✅ Marked as done. You can close this tab.</h2>"
    )


@app.route("/recent")
def recent_deletions():
    token = request.args.get("token")
    if token != DONE_SECRET:
        return "Unauthorized", 401

    data = load_done_followups()
    cutoff = datetime.now(timezone.utc) - timedelta(days=7)
    recent = {}
    for item_id, info in data.items():
        if info.get("deleted"):
            deleted_at = datetime.fromisoformat(info["deleted_at"])
            if deleted_at.tzinfo is None:
                deleted_at = deleted_at.replace(tzinfo=timezone.utc)
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
        html += (
            f'<li>{display} <a href="/undo?id={item_id}&token={token}">[Undo]</a>'
            f' (deleted {info["deleted_at"][:10]})</li>'
        )
    html += "</ul>"
    return html


@app.route("/undo")
def undo():
    item_id = request.args.get("id")
    token = request.args.get("token")
    if token != DONE_SECRET:
        return "Unauthorized", 401

    data = load_done_followups()
    if item_id in data:
        del data[item_id]
        save_done_followups(data)
        return "<h2>✅ Restored! You can close this window.</h2>"
    return "Item not found", 404


# ---------- HubSpot task creation ----------
@app.route("/create_task")
def create_task():
    item_hash = request.args.get("hash")
    email = request.args.get("email")
    title = request.args.get("title")
    duedays = request.args.get("duedays", "3")
    token = request.args.get("token")

    if token != DONE_SECRET:
        return "Unauthorized", 401
    if not item_hash or not email or not title:
        return "Missing parameters", 400

    tasks = load_created_tasks()
    if item_hash in tasks:
        return "<h2>⚠️ Task already created.</h2>"

    hubspot = HubSpotClient()
    contact_id = hubspot.get_contact_by_email(email)
    if not contact_id:
        return "<h2>❌ Contact not found in HubSpot. Cannot create task.</h2>", 404

    from datetime import date

    due_date = (date.today() + timedelta(days=int(duedays))).isoformat()

    success = hubspot.create_task(contact_id, title, due_date)
    if success:
        tasks[item_hash] = {"title": title, "due_date": due_date, "created": str(date.today())}
        save_created_tasks(tasks)
        return "<h2>✅ Task created in HubSpot! You can close this window.</h2>"
    return "<h2>❌ Failed to create task. Please try again.</h2>", 500


# ---------- Deal stage suggestions ----------
@app.route("/approve_deal")
def approve_deal():
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

    hubspot = HubSpotClient()
    success = hubspot.update_deal_stage(suggestion["deal_id"], suggestion["next_stage_id"])

    if success:
        suggestion["status"] = "approved"
        save_deal_suggestions(data)
        return "<h2>✅ Deal stage updated! You can close this window.</h2>"
    return "<h2>❌ Failed to update deal stage. Please try again.</h2>", 500


@app.route("/deny_deal")
def deny_deal():
    suggestion_id = request.args.get("id")
    token = request.args.get("token")
    if token != DONE_SECRET:
        return "Unauthorized", 401

    data = load_deal_suggestions()
    if suggestion_id in data:
        data[suggestion_id]["status"] = "denied"
        save_deal_suggestions(data)
    return "<h2>✅ Suggestion denied. You can close this window.</h2>"


# ---------- Conversation tree ----------
@app.route("/conversation")
def view_conversation():
    email = request.args.get("email")
    token = request.args.get("token")
    days = request.args.get("days", default=30, type=int)

    if token != DONE_SECRET:
        return "Unauthorized", 401
    if not email:
        return "Missing email", 400

    builder = ConversationTreeBuilder()
    # Ensure you have build_tree_content() in conversation_tree.py, not build_tree_html()
    tree_html = builder.build_tree_content(email, days)
    return render_template(
        "conversation.html",
        email=email,
        days=days,
        token=token,
        tree_html=tree_html,
    )


# ---------- Control centre / tree search / report / request / health ----------
@app.route("/control")
def control_center_page():
    token = request.args.get("token")
    if token != DONE_SECRET:
        return "Unauthorized", 401
    return render_template("control.html", token=token)


@app.route("/tree")
def tree_search_page():
    token = request.args.get("token")
    if token != DONE_SECRET:
        return "Unauthorized", 401

    graph = GraphClient()
    recent_emails = graph.get_recent_emails(days=30, top=200)
    contacts = []
    seen = set()
    for mail in recent_emails:
        addr = mail.get("from_address", "")
        if addr and "@" in addr and not addr.endswith("@reachpathways.com") and addr not in seen:
            seen.add(addr)
            contacts.append(addr)

    return render_template("tree_search.html", token=token, contacts=contacts)


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

    status_cards = [
        {
            "label": "Daily Briefing",
            "detail": f"Last run: {last_briefing}",
            "status": "Green" if last_briefing != "Never" else "Yellow",
            "css": "status-green" if last_briefing != "Never" else "status-yellow",
        },
        {
            "label": "Meeting Prep",
            "detail": f"Last run: {last_prep}",
            "status": "Green" if last_prep != "Never" else "Yellow",
            "css": "status-green" if last_prep != "Never" else "status-yellow",
        },
        {
            "label": "Graph API",
            "detail": "Token present",
            "status": "Green",
            "css": "status-green",
        },
        {
            "label": "HubSpot API",
            "detail": "Token present",
            "status": "Green",
            "css": "status-green",
        },
        {
            "label": "Serper API",
            "detail": "Optional enrichment",
            "status": "Yellow" if not SERPER_API_KEY else "Green",
            "css": "status-yellow" if not SERPER_API_KEY else "status-green",
        },
        {
            "label": "Today's Activity",
            "detail": f"{followups_today} follow‑ups, {drafts_today} drafts, {tasks_created} tasks",
            "status": "Green" if errors == 0 else "Red",
            "css": "status-green" if errors == 0 else "status-red",
        },
    ]

    return render_template("health.html", token=token, status_cards=status_cards)


@app.route("/report")
def report_form():
    token = request.args.get("token")
    if token != DONE_SECRET:
        return "Unauthorized", 401
    return render_template("report.html", token=token)


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
        body=f"Issue reported at {_now_iso()}:\n\n{message}",
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
    return render_template("request.html", token=token)


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
        body=f"Automation request at {_now_iso()}:\n\n{message}",
        content_type="Text",
    )
    if success:
        return "<h2>✅ Request sent. Thank you!</h2>"
    return "<h2>❌ Failed to send request. Please try again.</h2>", 500


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=8500, debug=False)