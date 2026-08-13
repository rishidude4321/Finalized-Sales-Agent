"""
Conversation Tree Builder – Module 6
Groups emails into threads, shows meetings and HubSpot activity,
and produces a clean HTML view with time-range selector.
"""

import json
import time
from pathlib import Path
from typing import List, Dict
from src.services.graph_client import GraphClient
from src.services.hubspot_client import HubSpotClient
from src.utils.config import DONE_SECRET

CACHE_FILE = "conversation_cache.json"
CACHE_TTL = 86400  # 24 hours per (email, days) cache entry
MAX_EVENTS_PER_THREAD = 20


class ConversationTreeBuilder:
    def __init__(self):
        self.graph = GraphClient()
        self.hubspot = HubSpotClient()
        self.cache = self._load_cache()

    def _load_cache(self):
        path = Path(CACHE_FILE)
        if path.exists():
            try:
                return json.loads(path.read_text())
            except Exception:
                pass
        return {}

    def _save_cache(self):
        with open(CACHE_FILE, "w") as f:
            json.dump(self.cache, f, indent=2, default=str)

    def _is_noise_email(self, mail: Dict) -> bool:
        """Filter automated meeting notifications and other noise."""
        subject = mail.get("subject", "").lower()
        preview = mail.get("preview", "").lower()
        # Meeting invitation/cancellation/response subjects
        noise_phrases = [
            "accepted:", "declined:", "tentative:", "canceled:", "cancelled:",
            "meeting-id", "passcode", "teams meeting", "join the meeting",
            "microsoft teams meeting", "meeting invitation",
        ]
        for phrase in noise_phrases:
            if phrase in subject:
                return True
        # If it looks like a Teams meeting body, skip
        if "teams.microsoft.com" in preview:
            return True
        return False

    def _group_emails_into_threads(self, emails: List[Dict]) -> List[Dict]:
        """Group messages into conversation threads using conversationId or subject."""
        threads_by_id = {}
        threads_by_subject = {}

        for mail in emails:
            if self._is_noise_email(mail):
                continue
            cid = mail.get("conversationId", "")
            if cid:
                if cid not in threads_by_id:
                    threads_by_id[cid] = {
                        "conversationId": cid,
                        "subject": mail.get("subject", "(no subject)"),
                        "messages": [],
                    }
                threads_by_id[cid]["messages"].append(mail)
                # update subject to the most recent (last message after sort)
            else:
                # fallback group by normalized subject
                norm_subject = mail.get("subject", "").strip()
                # strip common prefixes
                for prefix in ["re:", "fw:", "fwd:"]:
                    if norm_subject.lower().startswith(prefix):
                        norm_subject = norm_subject[3:].strip()
                if norm_subject not in threads_by_subject:
                    threads_by_subject[norm_subject] = {
                        "conversationId": None,
                        "subject": mail.get("subject", "(no subject)"),
                        "messages": [],
                    }
                threads_by_subject[norm_subject]["messages"].append(mail)

        # Merge fallback threads into main list
        all_threads = list(threads_by_id.values())
        for subject_key, thread in threads_by_subject.items():
            all_threads.append(thread)

        # Sort messages within each thread by received date ascending
        for thread in all_threads:
            thread["messages"].sort(key=lambda x: x.get("received", ""))

        # Sort threads by most recent message date descending
        all_threads.sort(
            key=lambda t: t["messages"][-1].get("received", "") if t["messages"] else "",
            reverse=True,
        )
        return all_threads

    def _format_thread_html(self, thread: Dict) -> str:
        """Return an HTML details/summary card for an email thread."""
        msgs = thread.get("messages", [])
        if not msgs:
            return ""

        first_date = msgs[0].get("received", "")[:10] if msgs else ""
        last_date = msgs[-1].get("received", "")[:10] if msgs else ""
        count = len(msgs)
        subject = thread.get("subject", "(no subject)")

        # Build summary text
        summary = f"📧 {subject} | Messages: {count}"
        if first_date and last_date:
            summary += f" | {first_date} → {last_date}"

        html_parts = [f'<details><summary style="cursor:pointer; font-weight:600;">{summary}</summary>']

        html_parts.append('<table style="border-collapse:collapse; width:100%; margin-top:6px;">')
        for msg in msgs:
            direction = msg.get("direction", "to")
            if direction == "to":
                chip = '<span style="color:#2b6cb0; font-weight:600;">You → Them</span>'
            else:
                chip = '<span style="color:#2f855a; font-weight:600;">Them → You</span>'
            date = msg.get("received", "")[:10]
            subject_line = msg.get("subject", "")
            web_link = msg.get("webLink", "")
            if web_link:
                subject_link = f'<a href="{web_link}" target="_blank">{subject_line}</a>'
            else:
                subject_link = subject_line
            html_parts.append(
                f'<tr>'
                f'<td style="padding:4px 8px; white-space:nowrap; color:#666;">{date}</td>'
                f'<td>{chip}</td>'
                f'<td>{subject_link}</td>'
                f'</tr>'
            )
        html_parts.append('</table>')
        html_parts.append('</details>')
        return "\n".join(html_parts)

    def _format_meeting_html(self, event: Dict) -> str:
        """Return an HTML card for a calendar meeting."""
        subject = event.get("subject", "No Subject")
        start = event.get("start", "")
        date = start[:10] if start else "unknown"
        attendees = ", ".join(event.get("attendees", []))
        web_link = event.get("webLink", "")
        if web_link:
            open_link = f'<a href="{web_link}" target="_blank">[Open in Outlook]</a>'
        else:
            open_link = ""
        html = f'<div style="margin:8px 0;">📅 <strong>{subject}</strong><br>'
        html += f'<span style="color:#555;">Date: {date}</span><br>'
        html += f'<span style="color:#555;">Attendees: {attendees}</span> '
        html += open_link
        html += '</div>'
        return html

    def _format_hubspot_html(self, engagements: List[Dict]) -> str:
        """Return HTML for HubSpot notes/tasks."""
        if not engagements:
            return ""
        html_parts = ['<div style="margin-top:12px;">']
        html_parts.append('<h3>📝 HubSpot Activity</h3>')
        for eng in engagements:
            date = eng.get("timestamp", "")[:10]
            icon = "📋" if eng.get("type") == "task" else "📝"
            content = eng.get("content", "")
            html_parts.append(f'<div style="margin:4px 0;">{icon} {date}: {content}</div>')
        html_parts.append('</div>')
        return "\n".join(html_parts)

    def build_tree_content(self, email: str, days: int = 30) -> str:
        """
        Build the conversation tree inner HTML fragment for the contact.
        Includes time-range selector, email threads, meetings, and HubSpot activity.
        """
        email_lower = email.lower().strip()
        cache_key = f"{email_lower}:{days}"
        cached = self.cache.get(cache_key)
        if cached and (time.time() - cached.get("ts", 0)) < CACHE_TTL:
            return cached["html"]

        # Fetch data
        contact_id = self.hubspot.get_contact_by_email(email_lower)
        emails = self.graph.get_emails_with_contact(email_lower, days=days)
        meetings = self.graph.get_events_with_attendee(email_lower, days=days)
        engagements = self.hubspot.get_contact_engagements(contact_id) if contact_id else []

        # Group emails
        threads = self._group_emails_into_threads(emails)

        # Build inner HTML
        html = []

        # Email threads
        html.append('<h3>📁 Email Threads</h3>')
        if threads:
            for thread in threads:
                html.append(self._format_thread_html(thread))
        else:
            html.append('<p>No email history found in this range.</p>')

        html.append('<hr>')

        # Meetings
        html.append('<h3>📅 Meetings</h3>')
        if meetings:
            for event in meetings:
                html.append(self._format_meeting_html(event))
        else:
            html.append('<p>No meetings found.</p>')

        html.append('<hr>')

        # HubSpot activity
        html.append(self._format_hubspot_html(engagements))

        inner_html = "\n".join(html)

        # Save cache
        self.cache[cache_key] = {"ts": time.time(), "html": inner_html}
        self._save_cache()
        return inner_html