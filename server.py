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
    SERPAPI_API_KEY,
)
from src.services.outreach_engine import (
    generate_outreach,
    fetch_live_leads,
    DEFAULT_TARGET_SECTOR,
    DEFAULT_COMPANY_SCALE,
    DEFAULT_LOCATION,
    DEFAULT_CAMPAIGN_GOAL,
    DEFAULT_PRICING_CONTEXT,
    DEFAULT_USER_BIO_CONTEXT,
    DEFAULT_WRITING_STYLE,
)
import uuid
from src.utils.logger import get_logger
from src.utils.agent_state import load_agent_status, load_usage
from src.services.status_reporter import send_status_email
from flask import Flask, request, render_template, redirect
import urllib.parse
from src.services.company_enricher import CompanyEnricher

app = Flask(__name__)

# ---------- File constants ----------
DATA_FILE = Path("done_followups.json")
DEAL_SUGGESTIONS_FILE = Path("deal_suggestions.json")
CREATED_TASKS_FILE = Path("created_tasks.json")
AGENT_STATUS_PATH = Path(AGENT_STATUS_FILE)
HIDDEN_CONTACTS_FILE = Path("hidden_contacts.json")
HIDDEN_DEALS_FILE = Path("hidden_deals.json")
OUTREACH_CACHE = {}

def load_hidden_contacts():
    if HIDDEN_CONTACTS_FILE.exists():
        with open(HIDDEN_CONTACTS_FILE, "r") as f:
            return json.load(f)
    return {}

def save_hidden_contacts(data):
    with open(HIDDEN_CONTACTS_FILE, "w") as f:
        json.dump(data, f, indent=2, default=str)

def load_hidden_deals():
    if HIDDEN_DEALS_FILE.exists():
        with open(HIDDEN_DEALS_FILE, "r") as f:
            return json.load(f)
    return {}

def save_hidden_deals(data):
    with open(HIDDEN_DEALS_FILE, "w") as f:
        json.dump(data, f, indent=2, default=str)


# ---------- Generic helpers ----------
def _is_noise_contact_addr(addr: str) -> bool:
    """
    Return True for system, noreply, daemon, and internal/automated addresses
    that should never appear as conversation contacts.
    """
    lower = addr.lower().strip()
    if not lower or "@" not in lower:
        return True

    # Internal / Sasha's own domains
    if lower.endswith("@reachpathways.com"):
        return True
    if lower.endswith("@chicagoscholars.org"):
        return True

    # Common automated/system senders
    noise_markers = [
        "no-reply@", "noreply@", "mailer-daemon@", "microsoftsecurity-noreply@",
        "mssecurity-noreply@", "drive-shares", "firebase-noreply@", "microsoft.exchange",
        "teams.mail.microsoft", "postmaster@", "digest-noreply@",
    ]
    for marker in noise_markers:
        if marker in lower:
            return True

    return False

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

SNOOZED_DEALS_FILE = Path("snoozed_deals.json")

def load_snoozed_deals():
    if SNOOZED_DEALS_FILE.exists():
        with open(SNOOZED_DEALS_FILE, "r") as f:
            return json.load(f)
    return {}

def save_snoozed_deals(data):
    with open(SNOOZED_DEALS_FILE, "w") as f:
        json.dump(data, f, indent=2, default=str)


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

    return render_template(
        "action_success.html",
        title="Marked as done",
        message="This item will no longer appear in your briefing. You can close this tab.",
    )

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

    items = []
    for item_id, info in data.items():
        if not info.get("deleted"):
            continue
        deleted_at = datetime.fromisoformat(info["deleted_at"])
        if deleted_at.tzinfo is None:
            deleted_at = deleted_at.replace(tzinfo=timezone.utc)
        if deleted_at > cutoff:
            action = info.get("action", item_id)
            reasoning = info.get("reasoning", "")
            contact = info.get("contact", "")
            display = f"{action} — {reasoning} (Contact: {contact})"
            items.append({
                "id": item_id,
                "display": display,
                "deleted_date": info["deleted_at"][:10],
            })

    return render_template("recent.html", token=token, items=items)


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
        return render_template(
            "action_success.html",
            title="Restored",
            message="The item has been restored and will reappear in your briefing if still relevant.",
        )
    return "Item not found", 404

@app.route("/new_contact")
def new_contact():
    token = request.args.get("token")
    if token != DONE_SECRET:
        return "Unauthorized", 401

    email = request.args.get("email", "")
    name = request.args.get("name", "")
    company = request.args.get("company", "")
    jobtitle = request.args.get("jobtitle", "")

    # If company is missing, try to enrich from the email domain
    if not company and email and "@" in email:
        domain = email.split("@")[-1].lower()
        free_domains = {
            "gmail.com", "yahoo.com", "hotmail.com", "outlook.com",
            "icloud.com", "me.com", "aol.com", "protonmail.com"
        }
        if domain not in free_domains:
            try:
                enricher = CompanyEnricher()
                info = enricher.enrich_company(domain)
                if info:
                    company = info.get("name") or company
            except Exception:
                pass

    redirect_to = request.args.get("redirect_to", "")
    item_hash = request.args.get("hash", "")
    title = request.args.get("title", "")
    duedays = request.args.get("duedays", "3")

    return render_template(
        "new_contact.html",
        token=token,
        email=email,
        name=name,
        company=company,
        jobtitle=jobtitle,
        redirect_to=redirect_to,
        item_hash=item_hash,
        title=title,
        duedays=duedays,
    )

@app.route("/create_contact", methods=["POST"])
def create_contact():
    token = request.form.get("token")
    if token != DONE_SECRET:
        return "Unauthorized", 401

    full_name = request.form.get("full_name", "").strip()
    email = request.form.get("email", "").strip()
    jobtitle = request.form.get("jobtitle", "").strip()

    if not full_name or not email:
        return render_template(
            "action_success.html",
            title="Missing information",
            message="Name and email are required.",
        )

    parts = full_name.split(" ", 1)
    first_name = parts[0]
    last_name = parts[1] if len(parts) > 1 else ""

    hubspot = HubSpotClient()
    contact_id = hubspot.create_contact(
        first_name=first_name,
        last_name=last_name,
        email=email,
        jobtitle=jobtitle,
    )

    print("DEBUG contact_id returned:", contact_id)

    if not contact_id:
        return render_template(
            "action_success.html",
            title="Contact creation failed",
            message="HubSpot could not create this contact. Please try again.",
        )

    redirect_to = request.form.get("redirect_to")
    if redirect_to == "create_task":
        item_hash = request.form.get("hash")
        title = request.form.get("title")
        duedays = request.form.get("duedays", "3")
        params = {
            "hash": item_hash,
            "email": email,
            "title": title,
            "duedays": duedays,
            "token": token,
        }
        return redirect(f"/create_task?{urllib.parse.urlencode(params)}")

    return render_template(
        "action_success.html",
        title="Contact added",
        message=f"{full_name} has been added to HubSpot.",
    )

# ---------- HubSpot task creation ----------
@app.route("/create_task")
def create_task():
    item_hash = request.args.get("hash")
    email = request.args.get("email")
    title = request.args.get("title")
    duedays = request.args.get("duedays", "3")
    sender_name = request.args.get("name", "")
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
        # Build a prefilled add-contact page and bring Sasha there.
        name = request.args.get("name", "")
        params = {
            "email": email,
            "name": sender_name,
            "hash": item_hash,
            "title": title,
            "duedays": duedays,
            "token": token,
            "redirect_to": "create_task",
        }
        return redirect(f"/new_contact?{urllib.parse.urlencode(params)}")

    from datetime import date

    due_date = (date.today() + timedelta(days=int(duedays))).isoformat()

    success = hubspot.create_task(contact_id, title, due_date)
    if success:
        tasks[item_hash] = {"title": title, "due_date": due_date, "created": str(date.today())}
        save_created_tasks(tasks)
        return render_template(
            "action_success.html",
            title="Task created in HubSpot",
            message="You can close this tab.",
        )
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
        return render_template(
            "action_success.html",
            title="Deal stage updated",
            message="The deal has been moved to the suggested stage.",
        )
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
    return render_template(
        "action_success.html",
        title="Suggestion denied",
        message="This suggestion will no longer appear.",
    )


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
        if _is_noise_contact_addr(addr):
            continue
        if addr in seen:
            continue
        seen.add(addr)

        name = mail.get("from_name", "").strip()
        if not name or name.lower() == addr.lower():
            display = addr
        else:
            display = f"{name} ({addr})"

        contacts.append({
            "email": addr,
            "name": name,
            "display": display,
        })

        if len(contacts) >= 30:
            break

    # Sort alphabetically by display name / email
    contacts.sort(key=lambda x: x["email"].lower())

    return render_template("tree_search.html", token=token, contacts=contacts)

@app.route("/health")
def health_page():
    token = request.args.get("token")
    if token != DONE_SECRET:
        return "Unauthorized", 401

    status = load_agent_status()
    usage = load_usage()

    last_briefing = status.get("last_briefing_time", "Never")
    last_prep = status.get("last_meeting_prep_time", "Never")
    followups_today = status.get("followups_today", 0)
    drafts_today = status.get("drafts_today", 0)
    tasks_created = status.get("tasks_created", 0)
    errors = status.get("error_count", 0)

    serper_month = usage.get("serper", {}).get("month", 0)
    serpapi_month = usage.get("serpapi", {}).get("month", 0)
    openrouter_month = usage.get("openrouter", {}).get("month", 0)

    log_file = Path("logs/agent.log")
    last_logs = []
    if log_file.exists():
        last_logs = log_file.read_text().splitlines()[-5:]

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
            "label": "Serper",
            "detail": f"{serper_month} searches this month",
            "status": "Green" if SERPER_API_KEY else "Yellow",
            "css": "status-green" if SERPER_API_KEY else "status-yellow",
        },
        {
            "label": "SerpAPI",
            "detail": f"{serpapi_month} searches this month",
            "status": "Green" if SERPAPI_API_KEY else "Yellow",
            "css": "status-green" if SERPAPI_API_KEY else "status-yellow",
        },
        {
            "label": "OpenRouter",
            "detail": f"{openrouter_month} calls this month",
            "status": "Green",
            "css": "status-green",
        },
        {
            "label": "Errors",
            "detail": f"{errors} recent errors",
            "status": "Green" if errors == 0 else "Red",
            "css": "status-green" if errors == 0 else "status-red",
        },
    ]

    return render_template(
        "health.html",
        token=token,
        status_cards=status_cards,
        last_logs=last_logs,
    )

@app.route("/send_status")
def send_status_route():
    token = request.args.get("token")
    if token != DONE_SECRET:
        return "Unauthorized", 401

    success = send_status_email(manual=True)
    if success:
        return render_template(
            "action_success.html",
            title="Status sent",
            message="The current status has been emailed to Rishi.",
        )
    return render_template(
        "action_success.html",
        title="Status failed",
        message="Could not send the status email. Please try again.",
    )

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

@app.route("/hide_contact")
def hide_contact():
    email = request.args.get("email")
    lastmod = request.args.get("lastmod")
    token = request.args.get("token")
    if token != DONE_SECRET:
        return "Unauthorized", 401
    if not email or not lastmod:
        return "Missing parameters", 400

    data = load_hidden_contacts()
    data[email] = lastmod
    save_hidden_contacts(data)
    return render_template(
        "action_success.html",
        title="Contact hidden",
        message="This contact will reappear if their information changes.",
    )

@app.route("/hide_deal")
def hide_deal():
    deal_id = request.args.get("id")
    stage = request.args.get("stage")
    token = request.args.get("token")
    if token != DONE_SECRET:
        return "Unauthorized", 401
    if not deal_id or not stage:
        return "Missing parameters", 400

    data = load_hidden_deals()
    data[deal_id] = stage
    save_hidden_deals(data)
    return render_template(
        "action_success.html",
        title="Deal hidden",
        message="This deal will reappear if its stage changes.",
    )

@app.route("/snooze_deal")
def snooze_deal():
    deal_id = request.args.get("id")
    token = request.args.get("token")
    if token != DONE_SECRET:
        return "Unauthorized", 401
    if not deal_id:
        return "Missing id", 400

    from datetime import datetime, timedelta, timezone
    snoozed_until = datetime.now(timezone.utc) + timedelta(days=7)
    data = load_snoozed_deals()
    data[deal_id] = snoozed_until.timestamp()
    save_snoozed_deals(data)
    return render_template(
        "action_success.html",
        title="Deal snoozed",
        message="This deal will reappear in 7 days.",
    )

@app.route("/outreach")
def outreach_form():
    token = request.args.get("token")
    if token != DONE_SECRET:
        return "Unauthorized", 401

    defaults = {
        "target_sector": DEFAULT_TARGET_SECTOR,
        "company_scale": DEFAULT_COMPANY_SCALE,
        "location": DEFAULT_LOCATION,
        "campaign_goal": DEFAULT_CAMPAIGN_GOAL,
        "pricing_context": DEFAULT_PRICING_CONTEXT,
        "user_bio_context": DEFAULT_USER_BIO_CONTEXT,
        "writing_style": DEFAULT_WRITING_STYLE,
    }
    return render_template("outreach.html", token=token, defaults=defaults)

@app.route("/generate_outreach", methods=["POST"])
def generate_outreach_route():
    token = request.form.get("token")
    if token != DONE_SECRET:
        return "Unauthorized", 401

    inputs = {
        "target_sector": request.form.get("target_sector") or DEFAULT_TARGET_SECTOR,
        "company_scale": request.form.get("company_scale") or DEFAULT_COMPANY_SCALE,
        "target_location": request.form.get("target_location") or DEFAULT_LOCATION,
        "campaign_goal": request.form.get("campaign_goal") or DEFAULT_CAMPAIGN_GOAL,
        "pricing_context": request.form.get("pricing_context") or DEFAULT_PRICING_CONTEXT,
        "user_bio_context": request.form.get("user_bio_context") or DEFAULT_USER_BIO_CONTEXT,
        "writing_style": request.form.get("writing_style") or DEFAULT_WRITING_STYLE,
    }

    result = generate_outreach(inputs)

    if result["xray_query"]:
        loc = inputs["target_location"]
        if loc == "Chicago":
            modifier = ' AND ("Chicago, Illinois" OR "Chicago Area")'
        elif loc == "Chicagoland Area":
            modifier = ' AND ("Chicago Area" OR "Chicagoland")'
        else:
            modifier = f' AND "{loc}"'
        full_query = f"{result['xray_query']}{modifier}"
        leads, error = fetch_live_leads(full_query)
    else:
        full_query = ""
        leads, error = [], "AI did not generate an X-Ray query."

    # Keep the raw query/error for the developer in the terminal log
    print(f"DEBUG OUTREACH QUERY: {full_query}")
    if error:
        print(f"DEBUG OUTREACH ERROR: {error}")

    # Store in cache for pagination
    cache_id = str(uuid.uuid4())
    OUTREACH_CACHE[cache_id] = {
        "query": full_query,
        "leads": leads,
        "offset": len(leads),
        "email_text": result["email_text"],
    }

    # User-friendly message
    if error:
        message = "No leads found. Please try adjusting your search or try again later."
    elif not leads:
        message = "No leads found for this search."
    else:
        message = None

    return render_template(
        "outreach_results.html",
        token=token,
        email_text=result["email_text"],
        leads=leads,
        cache_id=cache_id,
        message=message,
    )

@app.route("/more_leads", methods=["POST"])
def more_leads():
    token = request.form.get("token")
    if token != DONE_SECRET:
        return "Unauthorized", 401

    cache_id = request.form.get("cache_id")
    if not cache_id or cache_id not in OUTREACH_CACHE:
        return "Session expired. Please run the search again.", 400

    cache = OUTREACH_CACHE[cache_id]
    new_leads, error = fetch_live_leads(cache["query"], start_offset=cache["offset"])

    if error:
        print(f"DEBUG OUTREACH ERROR (more): {error}")
        message = "No additional leads found or an error occurred. Please try again later."
        new_leads = []
    else:
        message = None

    cache["leads"].extend(new_leads)
    cache["offset"] = len(cache["leads"])

    return render_template(
        "outreach_results.html",
        token=token,
        email_text=cache["email_text"],
        leads=cache["leads"],
        cache_id=cache_id,
        message=message,
    )

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=8500, debug=False)