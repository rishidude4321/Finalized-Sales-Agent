"""
Conversation Tree Builder for Module 6.
Generates a deterministic HTML timeline of interactions with a contact.
Cached per contact for 24 hours.
"""

import json
import time
from pathlib import Path
from typing import List, Dict
from src.services.graph_client import GraphClient
from src.services.hubspot_client import HubSpotClient

CACHE_FILE = "conversation_cache.json"
CACHE_TTL = 86400  # 24 hours
MAX_EVENTS = 25


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

    def build_tree(self, email: str) -> str:
        """Return an HTML tree for the given contact email. Cached."""
        email_lower = email.lower().strip()

        # Check cache
        entry = self.cache.get(email_lower)
        if entry and (time.time() - entry.get("ts", 0)) < CACHE_TTL:
            return entry["html"]

        # Fetch data
        contact_id = self.hubspot.get_contact_by_email(email)
        emails = self.graph.get_emails_with_contact(email)
        engagements = self.hubspot.get_contact_engagements(contact_id) if contact_id else []

        # Merge events
        events = []
        for mail in emails:
            direction = "→" if mail["direction"] == "to" else "←"
            events.append({
                "date": mail.get("received", "")[:10],
                "icon": "📧",
                "text": f'{direction} {mail["subject"]}',
            })

        for eng in engagements:
            icon = "📝" if eng["type"] == "note" else "📋"
            events.append({
                "date": eng.get("timestamp", "")[:10],
                "icon": icon,
                "text": eng.get("content", ""),
            })

        events.sort(key=lambda x: x.get("date", ""))

        # Limit and optionally summarise
        total = len(events)
        if total > MAX_EVENTS:
            events = events[:MAX_EVENTS]
            summary = f"(Showing first {MAX_EVENTS} of {total} events)"
        else:
            summary = ""

        # Build HTML
        html_parts = [f'<h2>📋 Conversation History: {email}</h2>']
        if summary:
            html_parts.append(f'<p><em>{summary}</em></p>')

        html_parts.append('<table style="border-collapse:collapse; width:100%">')
        for ev in events:
            html_parts.append(
                f'<tr>'
                f'<td style="padding:4px 8px; white-space:nowrap">{ev["date"]}</td>'
                f'<td>{ev["icon"]}</td>'
                f'<td>{ev["text"]}</td>'
                f'</tr>'
            )
        html_parts.append('</table>')

        html = "\n".join(html_parts)

        # Save cache
        self.cache[email_lower] = {"ts": time.time(), "html": html}
        self._save_cache()
        return html

    def build_collapsible_section(self, email: str) -> str:
        """Return a collapsible HTML block for the meeting prep note."""
        tree_html = self.build_tree(email)
        return (
            "<details>"
            "<summary>📋 Conversation History (click to expand)</summary>"
            f"{tree_html}"
            "</details>"
        )