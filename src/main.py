"""
Daily Briefing (Module 1) – 100% deterministic, no LLM hallucination risk.
Run from project root: python -m src.main
"""

import datetime
import re
from collections import defaultdict
from pathlib import Path
import json
from pydantic import BaseModel
from src.services.graph_client import GraphClient
import hashlib
from src.utils.config import DONE_SECRET, USER_EMAIL
import urllib.parse
from src.services.hubspot_client import HubSpotClient

# constants
DONE_FILE = "done_followups.json"

# ---------- Local data classes ----------
class FollowUpItem(BaseModel):
    action: str
    reasoning: str
    contact: str

class UpdateItem(BaseModel):
    name: str
    email: str
    new_title: str
    evidence: str

class VagueCommitmentItem(BaseModel):
    sender_name: str
    sender_email: str
    subject: str
    preview: str

# ---------- Calendar helpers ----------
def split_calendar_by_today(events):
    today_str = datetime.date.today().isoformat()
    today_events, upcoming_events = [], []
    for ev in events:
        start = ev.get("start", "")
        date_part = start[:10] if start else ""
        if date_part == today_str:
            today_events.append(ev)
        elif date_part > today_str:
            upcoming_events.append(ev)
    return today_events, upcoming_events

def format_event_list(events, include_date=False):
    if not events:
        return "None"
    lines = []
    for ev in events:
        start = ev.get("start", "")
        end = ev.get("end", "")
        try:
            start_dt = datetime.datetime.strptime(start, "%Y-%m-%dT%H:%M:%S")
            end_dt = datetime.datetime.strptime(end, "%Y-%m-%dT%H:%M:%S")
            start_time = start_dt.strftime("%I:%M %p").lstrip("0")
            end_time = end_dt.strftime("%I:%M %p").lstrip("0")
            if include_date:
                date_str = start_dt.strftime("%A, %B %d").replace(" 0", " ")
        except:
            start_time = start[11:16] if "T" in start else start
            end_time = end[11:16] if "T" in end else end
            date_str = ""
        subject = ev.get("subject", "No Subject")
        attendees = ", ".join(ev.get("attendees", [])) or "none"
        if include_date and date_str:
            lines.append(f"  • {date_str}: {subject} ({start_time}–{end_time}) – {attendees}")
        else:
            lines.append(f"  • {subject} ({start_time}–{end_time}) – {attendees}")
    return "\n".join(lines)

# Patterns to skip (add/remove as Sasha discovers false positives)
AUTOMATED_SENDERS = [
    "no-reply@", "noreply@", "donotreply@", "microsoftsecurity-noreply@",
    "no-reply@teams.mail.microsoft",
]
AUTOMATED_SUBJECT_PREFIXES = ("accepted:", "declined:", "tentative:")
AUTOMATED_SUBJECT_CONTAINS = [
    "is trying to reach you in Microsoft Teams",
    "out of office", "automatic reply",
    "your statement is ready", "weekly digest", "newsletter",
]

def is_automated_email(mail):
    """Return True if the email is from an automated sender or notification."""
    from_address = mail.get("from_address", "").lower()
    subject = mail.get("subject", "").lower()
    body = mail.get("body", "").lower()

    # Check sender
    for pattern in AUTOMATED_SENDERS:
        if pattern in from_address:
            return True

    # Check subject prefixes (meeting responses)
    if subject.startswith(AUTOMATED_SUBJECT_PREFIXES):
        return True

    # Check subject contains
    for phrase in AUTOMATED_SUBJECT_CONTAINS:
        if phrase in subject:
            return True

    # Check for calendar content
    if "content-type: text/calendar" in body or "BEGIN:VCALENDAR" in body:
        return True

    return False

# ---------- Deterministic follow‑up detection ----------
def detect_obvious_follow_ups(emails, conversations):
    items = []
    today = datetime.date.today()

    # Incoming emails with explicit questions/requests (skip automated)
    question_kw = ["?", "when should we meet", "can you", "please let me know", "what are your availabilities"]
        # Build a lookup: for each conversationId, the latest received date from recent inbox emails
    inbox_latest = {}
    for mail in emails:
        cid = mail.get("conversationId")
        if cid:
            received = mail.get("received", "")
            if cid not in inbox_latest or received > inbox_latest[cid]:
                inbox_latest[cid] = received
    for mail in emails:
        if is_automated_email(mail):
            continue
        body = mail.get("body", "").lower()
        if any(kw in body for kw in question_kw):
            name = mail.get("from_name") or mail.get("from_address")
            items.append(FollowUpItem(
                action=f"Reply to {name} re: {mail['subject']}",
                reasoning=f"Recent email asks: {mail['subject']}",
                contact=mail["from_address"]
            ))

    # Sent messages >= 3 days with no reply
    for conv in conversations:
        if conv.get("last_direction") == "you":
            cid = conv.get("conversationId")
            sent_dt_str = conv.get("last_sent", "")
            if not cid or not sent_dt_str:
                continue
            try:
                sent_dt = datetime.datetime.strptime(sent_dt_str[:19], "%Y-%m-%dT%H:%M:%S")
                sent_date = sent_dt.date()
                days_ago = (today - sent_date).days
                if days_ago < 3:
                    continue
                # Check if there is an incoming message in this thread after our sent date
                last_received = inbox_latest.get(cid)
                if last_received and last_received > sent_dt_str:
                    continue  # a reply exists, skip
                items.append(FollowUpItem(
                    action="Follow up on: " + conv.get("subject", ""),
                    reasoning=f"No reply for {days_ago} days",
                    contact="relevant contact"
                ))
            except:
                pass
    return items

# ---------- Promotion detection (100% deterministic) ----------
PROMOTION_PHRASES = [
    "promoted to", "new role", "title change",
    "starting a new position", "excited to share that i've joined"
]

def detect_promotions(emails):
    """Find promotion-related updates from recent emails."""
    items = []
    for mail in emails:
        if is_automated_email(mail):
            continue
        body = mail.get("body", "").lower()
        for phrase in PROMOTION_PHRASES:
            if phrase in body:
                idx = body.find(phrase)
                snippet = body[idx:idx+80]
                snippet = re.sub(r'\s+', ' ', snippet).strip()
                items.append(UpdateItem(
                    name=mail.get("from_name") or mail["from_address"],
                    email=mail["from_address"],
                    new_title="(see evidence)",
                    evidence=snippet
                ))
                break
    return items

# ---------- Vague commitment detection ----------
VAGUE_PHRASES = [
    "circle back", "touch base", "let's reconnect", "follow up in a few",
    "ping me after", "reconnect after", "catch up after", "chat next month",
    "talk later", "revisit this", "check in after", "set up a call later",
    "loop back", "bump this after", "let's revisit", "ping me when",
    "catch up soon", "talk soon", "reconnect in a bit", "sync up later",
    "lets circle back", "we should reconnect", "i'll follow up", "i will follow up",
    "i'll ping you", "i will ping you", "we'll catch up", "we will catch up",
    "let's chat", "let's sync", "let's talk", "let's connect",
    "after the holidays", "after the weekend", "after the conference",
    "next month", "next quarter", "in the new year",
    "when things settle", "when you're free",
]

def detect_vague_commitments(emails):
    """Flag emails with vague future plans that Sasha should review manually."""
    items = []
    for mail in emails:
        if is_automated_email(mail):
            continue
        body = mail.get("body", "").lower()
        if any(phrase in body for phrase in VAGUE_PHRASES):
            items.append(VagueCommitmentItem(
                sender_name=mail.get("from_name") or mail["from_address"],
                sender_email=mail["from_address"],
                subject=mail["subject"],
                preview=mail["body"][:150]
            ))
    return items

# ---------- Deduplication ----------
def deduplicate_follow_ups(items):
    seen = set()
    unique = []
    for item in items:
        key = (item.contact.lower(), item.action.lower().strip())
        if key not in seen:
            seen.add(key)
            unique.append(item)
    return unique

def deduplicate_updates(items):
    seen = set()
    unique = []
    for item in items:
        key = item.email.lower()
        if key not in seen:
            seen.add(key)
            unique.append(item)
    return unique

# ---------- Validation ----------
def validate_follow_ups(follow_ups, recent_emails, conversations):
    """Remove items where contact is not in any known email address or name."""
    known_emails = set()
    known_names = set()
    for mail in recent_emails:
        known_emails.add(mail["from_address"].lower())
        if mail.get("from_name"):
            known_names.add(mail["from_name"].lower())
    for conv in conversations:
        preview = conv.get("last_message_preview", "") + conv.get("subject", "")
        for e in re.findall(r'[\w\.-]+@[\w\.-]+', preview):
            known_emails.add(e.lower())

    return [f for f in follow_ups
            if f.contact.lower() in known_emails or f.contact.lower() in known_names]

def validate_updates(updates, recent_emails):
    """Remove updates where email not in recent emails, or evidence lacks promotion phrase."""
    known_emails = {mail["from_address"].lower() for mail in recent_emails}
    return [u for u in updates
            if u.email.lower() in known_emails and
            any(kw in u.evidence.lower() for kw in PROMOTION_PHRASES)]

def generate_followup_hash(contact, action):
    raw = f"{contact}|{action}".lower().strip()
    return hashlib.md5(raw.encode()).hexdigest()[:12]

def load_done_items():
    if Path(DONE_FILE).exists():
        with open(DONE_FILE, "r") as f:
            return json.load(f)
    return {}

def save_done_items(data):
    with open(DONE_FILE, "w") as f:
        json.dump(data, f, indent=2, default=str)

def filter_done_follow_ups(items):
    """Remove follow‑ups that are soft‑deleted, and hard‑delete entries older than 7 days."""
    done = load_done_items()
    cutoff = datetime.datetime.utcnow() - datetime.timedelta(days=7)
    cleaned = {}
    for item_id, info in done.items():
        if info.get("deleted"):
            deleted_at = datetime.datetime.fromisoformat(info["deleted_at"])
            if deleted_at < cutoff:
                continue  # hard delete
            cleaned[item_id] = info
        else:
            cleaned[item_id] = info
    save_done_items(cleaned)

    filtered = []
    for item in items:
        item_id = generate_followup_hash(item.contact, item.action)
        if item_id in cleaned and cleaned[item_id].get("deleted"):
            continue
        filtered.append(item)
    return filtered

# ---------- Main ----------
def main():
    print("🔄 Fetching data from Microsoft Graph...")
    graph = GraphClient()
    calendar_events = graph.get_calendar_events(days=7)
    recent_emails = graph.get_recent_emails(days=7, top=50)
    conversations = graph.get_recent_conversations(days=14)

    # HubSpot data
    print("🔄 Fetching data from HubSpot...")
    hubspot = HubSpotClient()
    outstanding_deals = hubspot.get_outstanding_deals()
    recent_contacts = hubspot.get_recent_contacts(days=7)
    
    # Build combined conversations (recent emails as virtual threads)
    combined_conversations = list(conversations)
    for mail in recent_emails:
        combined_conversations.append({
            "subject": mail["subject"],
            "last_direction": "them",
            "last_sent": mail["received"],
            "last_message_preview": mail["preview"],
        })

    # 1. Deterministic follow‑ups
    follow_ups = detect_obvious_follow_ups(recent_emails, combined_conversations)
    follow_ups = deduplicate_follow_ups(follow_ups)
    follow_ups = validate_follow_ups(follow_ups, recent_emails, combined_conversations)
    follow_ups = filter_done_follow_ups(follow_ups)

    # 2. Promotion updates
    updates = detect_promotions(recent_emails)
    updates = deduplicate_updates(updates)
    updates = validate_updates(updates, recent_emails)

    # 3. Vague commitments
    vague_items = detect_vague_commitments(recent_emails)

    # (LLM extraction commented out – no longer needed)
    # chain = build_insights_chain()
    # insights = chain.invoke({...})

    # Calendar sections
    today_events, upcoming_events = split_calendar_by_today(calendar_events)
    today_section = format_event_list(today_events)
    upcoming_section = format_event_list(upcoming_events, include_date=True)

    # Updates text
    updates_text = "\n".join(
        [f"• Potential update: {u.name} ({u.email}) – {u.new_title}. Evidence: {u.evidence}" for u in updates]
    ) if updates else "• No passive updates detected today."

    # Vague commitments text
    if vague_items:
        vague_text = "\n".join(
            [f"• {v.sender_name} ({v.sender_email}) – Subject: {v.subject} – Preview: {v.preview}" for v in vague_items]
        )
    else:
        vague_text = "• No vague commitments needing review."

    today_date_str = datetime.date.today().strftime("%A, %B %d").replace(" 0", " ")
    follow_up_lines = []
    for f in follow_ups:
        item_id = generate_followup_hash(f.contact, f.action)
        done_link = (
            f"http://localhost:8500/done?id={item_id}&token={DONE_SECRET}"
            f"&action={urllib.parse.quote(f.action)}"
            f"&reasoning={urllib.parse.quote(f.reasoning)}"
            f"&contact={urllib.parse.quote(f.contact)}"
        )
        follow_up_lines.append(
            f'<li>{f.action} — {f.reasoning} (Contact: {f.contact}) '
            f'<a href="{done_link}">[Mark Done]</a></li>'
        )
    if follow_up_lines:
        follow_ups_text = "<ul>" + "".join(follow_up_lines) + "</ul>"
    else:
        follow_ups_text = "<p>• No outstanding follow-ups identified.</p>"

    # Build updates as HTML
    if updates:
        update_lines = [f'<li>Potential update: {u.name} ({u.email}) – {u.new_title}. Evidence: {u.evidence}</li>' for u in updates]
        updates_text = "<ul>" + "".join(update_lines) + "</ul>"
    else:
        updates_text = "<p>• No passive updates detected today.</p>"

    # Build vague commitments as HTML
    if vague_items:
        vague_lines = [f'<li>{v.sender_name} ({v.sender_email}) – Subject: {v.subject} – Preview: {v.preview}</li>' for v in vague_items]
        vague_text = "<ul>" + "".join(vague_lines) + "</ul>"
    else:
        vague_text = "<p>• No vague commitments needing review.</p>"

    # Build today's meetings as HTML (preserve line breaks)
    today_section_html = today_section.replace("\n", "<br>")
    upcoming_section_html = upcoming_section.replace("\n", "<br>")

    undo_link = f'http://localhost:8500/recent?token={DONE_SECRET}'

        # HubSpot deals text
    if outstanding_deals:
        deal_lines = []
        for deal in outstanding_deals:
            stage_name = hubspot.get_deal_stage_name(deal["stage"])
            last_mod = deal["last_modified"][:10] if deal["last_modified"] else "unknown"
            amount = f" (${deal['amount']})" if deal["amount"] else ""
            deal_lines.append(f'<li>{deal["name"]}{amount} – Stage: {stage_name} – Last activity: {last_mod}</li>')
        deals_text = "<ul>" + "".join(deal_lines) + "</ul>"
    else:
        deals_text = "<p>• No outstanding deals needing attention.</p>"

    # HubSpot contacts text
    if recent_contacts:
        contact_lines = []
        for contact in recent_contacts:
            title = f" – {contact['jobtitle']}" if contact.get("jobtitle") else ""
            company = f" at {contact['company']}" if contact.get("company") else ""
            contact_lines.append(f'<li>{contact["name"]}{title}{company} ({contact["email"]}) – Updated: {contact["last_modified"][:10]}</li>')
        contacts_text = "<ul>" + "".join(contact_lines) + "</ul>"
    else:
        contacts_text = "<p>• No recently updated contacts.</p>"

    briefing = f"""<html><body>
<h2>Daily Briefing</h2>
<hr>

<h3>Agenda</h3>
<p><strong>Today's Meetings ({today_date_str}):</strong><br>
{today_section_html}<br>
<strong>Top Priority:</strong> Review the day's meetings and prepare any necessary materials.</p>

<h3>Follow-Ups</h3>
{follow_ups_text}

<h3>Upcoming Meetings This Week</h3>
<p>{upcoming_section_html}</p>

<h3>Updates</h3>
{updates_text}

<hr>
<h3>DEALS NEEDING ATTENTION</h3>
{deals_text}

<h3>RECENTLY UPDATED CONTACTS</h3>
{contacts_text}
<hr>

<h3>Needs Your Review (Vague commitments)</h3>
{vague_text}

<hr>
<p><em>Accidentally marked something done? <a href="{undo_link}">View recent changes.</a></em></p>
</body></html>"""

    print("\n" + "=" * 60)
    print("DAILY BRIEFING")
    print("=" * 60)
    print(briefing)

    # Create Outlook draft as HTML
    graph.create_draft(to=USER_EMAIL, subject="Daily Briefing", body=briefing, content_type="HTML")
    

if __name__ == "__main__":
    main()