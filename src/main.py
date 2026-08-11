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
from src.services.draft_generator import DraftGenerator
import json
from pathlib import Path
import urllib.parse

# constants
DONE_FILE = "done_followups.json"
DEAL_SUGGESTIONS_FILE = "deal_suggestions.json"
CREATED_TASKS_FILE = "created_tasks.json"

# ---------- Local data classes ----------
class FollowUpItem(BaseModel):
    action: str
    reasoning: str
    contact: str
    thread_id: str = ""

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
    thread_id: str = ""

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
    question_kw = ["?", "when should we meet", "can you", "you can" "could you", "you could", "please let me know", "what are your availabilities"]
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
                contact=mail["from_address"],
                thread_id=mail.get("conversationId", "")
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
                    contact="relevant contact",
                    thread_id=mail.get("conversationId", "")
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
                preview=mail["body"][:150],
                thread_id=mail.get("conversationId", "")
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

def generate_followup_hash(contact, action, thread_id=""):
    raw = f"{contact}|{action}|{thread_id}".lower().strip()
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
    cutoff = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=7)
    cleaned = {}
    for item_id, info in done.items():
        if info.get("deleted"):
            deleted_at = datetime.datetime.fromisoformat(info["deleted_at"]).replace(tzinfo=datetime.timezone.utc)
            if deleted_at < cutoff:
                continue  # hard delete
            cleaned[item_id] = info
        else:
            cleaned[item_id] = info
    save_done_items(cleaned)

    filtered = []
    for item in items:
        item_id = generate_followup_hash(item.contact, item.action, item.thread_id)
        if item_id in cleaned and cleaned[item_id].get("deleted"):
            continue
        filtered.append(item)
    return filtered

# ---------- Deal stage suggestions ----------
def load_deal_suggestions():
    path = Path(DEAL_SUGGESTIONS_FILE)
    if path.exists():
        with open(path, "r") as f:
            return json.load(f)
    return {}

def save_deal_suggestions(data):
    with open(DEAL_SUGGESTIONS_FILE, "w") as f:
        json.dump(data, f, indent=2, default=str)

def generate_deal_suggestions(graph_client, hubspot_client):
    """
    Scan meetings from the last 24h, find associated open deals,
    and generate stage‑advancement suggestions.
    Returns a list of suggestion dicts.
    """
    import datetime
    recent_events = graph_client.get_recent_events(days_back=1)
    suggestions = []
    now_utc = datetime.datetime.now(datetime.timezone.utc)

    for event in recent_events:
        # Only process events that have already started (meeting happened)
        start_str = event.get("start", "")
        if start_str:
            try:
                event_start = datetime.datetime.fromisoformat(start_str.replace("Z", "+00:00"))
                if event_start > now_utc:
                    continue  # future meeting
            except:
                pass

        for email in event.get("attendees", []):
            if "@" not in email or email.endswith("@reachpathways.com"):
                continue

            # Find HubSpot contact by email
            search_payload = {
                "filterGroups": [{"filters": [{"propertyName": "email", "operator": "EQ", "value": email}]}],
                "properties": ["firstname", "lastname"],
                "limit": 1,
            }
            search_data = hubspot_client._make_request(
                "POST", "/crm/v3/objects/contacts/search", json=search_payload
            )
            if not search_data or not search_data.get("results"):
                continue

            contact = search_data["results"][0]
            contact_id = contact["id"]
            first = contact["properties"].get("firstname", "")
            last = contact["properties"].get("lastname", "")
            contact_name = f"{first} {last}".strip()

            deals = hubspot_client.get_contact_deals(contact_id)
            for deal in deals:
                next_stage_id = hubspot_client.get_next_stage_id(deal["stage_id"])
                if not next_stage_id:
                    continue  # already at final stage

                suggestion_id = f"{event['id']}-{contact_id}-{deal['id']}"
                existing_all = load_deal_suggestions()
                existing = existing_all.get(suggestion_id)
                if existing:
                    # If the deal stage has changed since the last suggestion, allow a new one
                    if existing.get("current_stage") == deal["stage_label"]:
                        continue  # already suggested for this stage, skip
                    # Stage changed — remove old and allow new suggestion
                    del existing_all[suggestion_id]
                    save_deal_suggestions(existing_all)

                suggestion = {
                    "id": suggestion_id,
                    "deal_id": deal["id"],
                    "deal_name": deal["name"],
                    "contact_name": contact_name,
                    "contact_email": email,
                    "current_stage": deal["stage_label"],
                    "next_stage_id": next_stage_id,
                    "next_stage_label": hubspot_client.get_deal_stage_name(next_stage_id),
                    "meeting_subject": event.get("subject", ""),
                    "timestamp": datetime.datetime.now().isoformat(),
                    "status": "pending",
                }
                suggestions.append(suggestion)

    if suggestions:
        current = load_deal_suggestions()
        for s in suggestions:
            current[s["id"]] = s
        save_deal_suggestions(current)

    return suggestions

def load_created_tasks():
    path = Path(CREATED_TASKS_FILE)
    if path.exists():
        with open(path, "r") as f:
            return json.load(f)
    return {}

def save_created_tasks(data):
    with open(CREATED_TASKS_FILE, "w") as f:
        json.dump(data, f, indent=2, default=str)

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
        # DEBUG
    print(f"DEBUG: recent_emails count = {len(recent_emails)}")
    for i, mail in enumerate(recent_emails):
        print(f"  {i}: {mail.get('subject','?')} from {mail.get('from_address','?')}")
    print(f"DEBUG: raw follow_ups count = {len(follow_ups)}")
    for f in follow_ups:
        print(f"  - {f.action} ({f.contact})")
    follow_ups = deduplicate_follow_ups(follow_ups)
    print(f"DEBUG after dedup: {len(follow_ups)}")
    follow_ups = validate_follow_ups(follow_ups, recent_emails, combined_conversations)
    print(f"DEBUG after validate: {len(follow_ups)}")
    follow_ups = filter_done_follow_ups(follow_ups)
    print(f"DEBUG after filter_done: {len(follow_ups)}")

    # 2. Promotion updates
    updates = detect_promotions(recent_emails)
    updates = deduplicate_updates(updates)
    updates = validate_updates(updates, recent_emails)

    # 3. Vague commitments
    vague_items = detect_vague_commitments(recent_emails)

    # If an email appears in both follow-ups and vague commitments, keep only the vague entry
    vague_thread_ids = {v.thread_id for v in vague_items if v.thread_id}
    if vague_thread_ids:
        follow_ups = [f for f in follow_ups if f.thread_id not in vague_thread_ids]

    # (LLM extraction commented out – no longer needed)
    # chain = build_insights_chain()
    # insights = chain.invoke({...})

    # 3.5 Generate follow‑up drafts
    draft_gen = DraftGenerator()
    for follow_up in follow_ups:
        draft_gen.create_follow_up_draft(follow_up)

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

    # Deal stage suggestions
    deal_suggestions = generate_deal_suggestions(graph, hubspot)
    # Deal stage suggestions HTML
    if deal_suggestions:
        deal_lines = []
        for s in deal_suggestions:
            approve = f"http://localhost:8500/approve_deal?id={s['id']}&token={DONE_SECRET}"
            deny = f"http://localhost:8500/deny_deal?id={s['id']}&token={DONE_SECRET}"
            deal_lines.append(
                f'<li>{s["contact_name"]} ({s["contact_email"]}) – Deal: {s["deal_name"]} '
                f'({s["current_stage"]} → {s["next_stage_label"]}) '
                f'<a href="{approve}">[Approve]</a> <a href="{deny}">[Deny]</a></li>'
            )
        deal_sugg_text = "<ul>" + "".join(deal_lines) + "</ul>"
    else:
        deal_sugg_text = "<p>• No deal stage suggestions.</p>"

    today_date_str = datetime.date.today().strftime("%A, %B %d").replace(" 0", " ")
    created_tasks = load_created_tasks()
    follow_up_lines = []
    for f in follow_ups:
        item_id = generate_followup_hash(f.contact, f.action, f.thread_id)
        # Mark Done link
        done_link = (
            f"http://localhost:8500/done?id={item_id}&token={DONE_SECRET}"
            f"&action={urllib.parse.quote(f.action)}"
            f"&reasoning={urllib.parse.quote(f.reasoning)}"
            f"&contact={urllib.parse.quote(f.contact)}"
        )
        # Create Task link (only if not already created)
        if item_id in created_tasks:
            task_html = "[Task &#10003;]"
        else:
            # Determine due days from reasoning
            reasoning_lower = f.reasoning.lower()
            if "recent email asks" in reasoning_lower:
                due_days = 1
            elif "no reply for" in reasoning_lower:
                # extract days if possible
                import re
                days_match = re.search(r"(\d+)\s*days?", reasoning_lower)
                days_num = int(days_match.group(1)) if days_match else 3
                if days_num >= 14:
                    due_days = 7
                elif days_num >= 7:
                    due_days = 7
                else:
                    due_days = 3
            else:
                due_days = 3
            task_link = (
                f"http://localhost:8500/create_task?hash={item_id}&email={urllib.parse.quote(f.contact)}"
                f"&title={urllib.parse.quote(f.action)}&duedays={due_days}&token={DONE_SECRET}"
            )
            task_html = f'<a href="{task_link}">[Create Task]</a>'

        follow_up_lines.append(
            f'<li>{f.action} — {f.reasoning} (Contact: {f.contact})<br>'
            f'<a href="{done_link}">[Mark Done]</a>  {task_html}</li>'
        )
    follow_ups_text = "<ul>" + "".join(follow_up_lines) + "</ul>" if follow_up_lines else "<p>• No outstanding follow-ups identified.</p>"

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

<h3>DEAL STAGE SUGGESTIONS</h3>
{deal_sugg_text}

<hr>
<p><em>Accidentally marked something done? <a href="{undo_link}">View recent changes.</a></em></p>
</body></html>"""

    print("\n" + "=" * 60)
    print("DAILY BRIEFING")
    print("=" * 60)
    print(briefing)

    # Create Outlook draft as HTML
    graph.create_draft(to=USER_EMAIL, subject="Daily Briefing", body=briefing, content_type="HTML")
    
    # 5. Meeting Prep (process new events)
    print("🔄 Running meeting prep...")
    from src.services.meeting_prep import MeetingPrepProcessor
    try:
        prep = MeetingPrepProcessor()
        processed = prep.process_new_events()
        if processed:
            print(f"   ✅ Added prep notes to {processed} meeting(s).")
        else:
            print("   ℹ️ No new meetings to prep.")
    except RuntimeError as e:
        print(f"⚠️ Meeting prep failed: {e}")
        # Also catch any post‑meeting drafts
    prep.process_post_meetings()

    # 5.5 Post‑meeting drafts (catch any recently ended meetings)
    if processed > 0:  # only if we processed new meetings (meaning watcher missed them)
        # The watcher should handle this, but we can also run it now
        pass  # We'll rely on the watcher for real‑time, but can add a manual run here later

if __name__ == "__main__":
    main()