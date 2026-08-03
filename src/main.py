"""
Daily Briefing (Module 1) – hybrid deterministic + LLM extraction.
Run from project root: python -m src.main
"""

import datetime
import re
from collections import defaultdict
from src.services.graph_client import GraphClient
from src.chains.briefing_chain import build_insights_chain, FollowUpItem, UpdateItem

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

def remove_contradictory_lines(text: str) -> str:
    if "No passive updates detected today." in text:
        lines = text.splitlines()
        new_lines, in_updates, seen_update = [], False, False
        for line in lines:
            if line.strip().startswith("=== UPDATES ==="):
                in_updates = True
                new_lines.append(line)
                continue
            if in_updates:
                if line.strip().startswith("•"):
                    seen_update = True
                    new_lines.append(line)
                elif "No passive updates detected today." in line and seen_update:
                    continue
                else:
                    new_lines.append(line)
            else:
                new_lines.append(line)
        return "\n".join(new_lines)
    return text

# ---------- Deterministic follow‑up detection ----------
def detect_obvious_follow_ups(emails, conversations):
    items = []
    today = datetime.date.today()

    # Unread emails with questions/meeting requests
    question_kw = ["?", "when should we meet", "can you", "please let me know", "what are your availabilities"]
    for mail in emails:
        body = mail.get("body", "").lower()
        if any(kw in body for kw in question_kw):
            name = mail.get("from_name") or mail.get("from_address")
            items.append(FollowUpItem(
                action=f"Reply to {name}",
                reasoning=f"Unread email asks: {mail['subject']}",
                contact=mail["from_address"]
            ))

    # Sent messages >3 days with no reply
    for conv in conversations:
        if conv.get("last_direction") == "you":
            try:
                sent_date = datetime.datetime.strptime(conv.get("last_sent", "")[:10], "%Y-%m-%d").date()
                days_ago = (today - sent_date).days
                if days_ago > 3:
                    items.append(FollowUpItem(
                        action="Follow up on: " + conv.get("subject", ""),
                        reasoning=f"No reply for {days_ago} days",
                        contact="relevant contact"
                    ))
            except:
                pass
    return items

# ---------- Promotion fallback scanner ----------
PROMOTION_PHRASES = [
    "promoted to", "new role", "title change",
    "starting a new position", "excited to share that i've joined"
]

def detect_promotions_fallback(emails, existing_updates):
    existing_emails = {u.email.lower() for u in existing_updates}
    new_items = []
    for mail in emails:
        body = mail.get("body", "").lower()
        for phrase in PROMOTION_PHRASES:
            if phrase in body and mail["from_address"].lower() not in existing_emails:
                idx = body.find(phrase)
                snippet = body[idx:idx+80].replace("\n", " ")
                new_items.append(UpdateItem(
                    name=mail.get("from_name") or mail["from_address"],
                    email=mail["from_address"],
                    new_title="(see evidence)",
                    evidence=snippet
                ))
                break
    return new_items

# ---------- Deduplication ----------
def deduplicate_follow_ups(items):
    """Remove duplicates by comparing (contact, action) similarity."""
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
def validate_insights(insights, recent_emails, conversations):
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

    insights.follow_ups = [f for f in insights.follow_ups
                           if f.contact.lower() in known_emails or f.contact.lower() in known_names]
    insights.potential_updates = [u for u in insights.potential_updates
                                  if u.email.lower() in known_emails]

# ---------- Main ----------
def main():
    print("🔄 Fetching data from Microsoft Graph...")
    graph = GraphClient()

    calendar_events = graph.get_calendar_events(days=7)
    recent_emails = graph.get_recent_emails(days=3, top=20)
        # --- DIAGNOSTIC: dump what we actually got from Graph ---
    print(f"\n🔎 DEBUG: Number of recent emails fetched: {len(recent_emails)}")
    for i, mail in enumerate(recent_emails):
        print(f"Email {i+1}:")
        print(f"  From: {mail.get('from_name','')} <{mail.get('from_address','')}>")
        print(f"  Subject: {mail.get('subject','')}")
        print(f"  Body preview: {mail.get('body','')[:200]}")
        print()
    # -----------------------------------------------------------
    conversations = graph.get_recent_conversations(days=14)

    # Build combined conversations (unread emails as virtual threads)
    combined_conversations = list(conversations)
    for mail in recent_emails:
        combined_conversations.append({
            "subject": mail["subject"],
            "last_direction": "them",
            "last_sent": mail["received"],
            "last_message_preview": mail["preview"],
        })

    # 1. Deterministic follow‑ups (Python)
    deterministic_follow_ups = detect_obvious_follow_ups(recent_emails, combined_conversations)

    # 2. LLM extraction (only supplementary)
    def format_conv(convs):
        lines = []
        for c in convs:
            lines.append(f"- {c.get('subject','')} | dir:{c.get('last_direction','?')} "
                         f"on {c.get('last_sent','')[:10]} | {c.get('last_message_preview','')[:80]}")
        return "\n".join(lines) if lines else "No conversations."

    def format_emails(emails):
        lines = []
        for mail in emails:
            sender = mail.get("from_name") or mail.get("from_address")
            lines.append(f"---\nFrom: {sender} ({mail['from_address']})\nSubject: {mail['subject']}\nBody: {mail['body']}\n")
        return "\n".join(lines) if lines else "No unread emails."

    conv_text = format_conv(combined_conversations)
    emails_text = format_emails(recent_emails)

    chain = build_insights_chain()
    print("🧠 Extracting supplementary insights...")
    insights = chain.invoke({
        "conversations": conv_text,
        "emails": emails_text,
    })

    # 3. Merge deterministic + LLM follow‑ups, then deduplicate
    all_follow_ups = deterministic_follow_ups + insights.follow_ups
    insights.follow_ups = deduplicate_follow_ups(all_follow_ups)

    # 4. Add fallback promotions, deduplicate updates
    extra_updates = detect_promotions_fallback(recent_emails, insights.potential_updates)
    all_updates = insights.potential_updates + extra_updates
    insights.potential_updates = deduplicate_updates(all_updates)

    # 5. Validate against real data
    validate_insights(insights, recent_emails, combined_conversations)

    # 6. Calendar sections
    today_events, upcoming_events = split_calendar_by_today(calendar_events)
    today_section = format_event_list(today_events)
    upcoming_section = format_event_list(upcoming_events, include_date=True)

    # 7. Build text sections
    follow_ups_text = "\n".join(
        [f"• {f.action} — {f.reasoning} (Contact: {f.contact})" for f in insights.follow_ups]
    ) if insights.follow_ups else "• No outstanding follow-ups identified."

    updates_text = "\n".join(
        [f"• Potential update: {u.name} ({u.email}) – {u.new_title}. Evidence: {u.evidence}" for u in insights.potential_updates]
    ) if insights.potential_updates else "• No passive updates detected today."

    today_date_str = datetime.date.today().strftime("%A, %B %d").replace(" 0", " ")
    briefing = f"""=== DAILY BRIEFING ===

AGENDA & FOCUS
• Today's Meetings ({today_date_str}):
{today_section}
• Top Priority: Review the day's meetings and prepare any necessary materials.

FOLLOW-UPS & DROPS
{follow_ups_text}

UPCOMING MEETINGS (This Week)
{upcoming_section}

UPDATES
{updates_text}
"""
    briefing = remove_contradictory_lines(briefing)

    print("\n" + "=" * 60)
    print("DAILY BRIEFING")
    print("=" * 60)
    print(briefing)

    # Create Outlook draft
    graph.create_draft(to="rvira@reachpathways.com", subject="Daily Briefing", body=briefing)

if __name__ == "__main__":
    main()