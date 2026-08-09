"""
Draft Generator for Module 3.
Creates personalized draft emails from templates and real data.
Deterministic – no LLM.
"""

import json
from pathlib import Path
from typing import Dict, Optional
from src.services.graph_client import GraphClient
from src.services.hubspot_client import HubSpotClient


class DraftGenerator:
    TEMPLATES_FILE = "email_templates.json"
    DRAFTS_CREATED_FILE = "drafts_created.json"

    def __init__(self):
        self.graph = GraphClient()
        self.hubspot = HubSpotClient()
        self.templates = self._load_templates()
        self.drafts_created = self._load_drafts_created()

    def _load_templates(self) -> Dict:
        path = Path(self.TEMPLATES_FILE)
        if path.exists():
            try:
                return json.loads(path.read_text())
            except:
                pass
        return {}

    def _load_drafts_created(self) -> Dict:
        path = Path(self.DRAFTS_CREATED_FILE)
        if path.exists():
            try:
                return json.loads(path.read_text())
            except:
                pass
        return {}

    def _save_drafts_created(self):
        with open(self.DRAFTS_CREATED_FILE, "w") as f:
            json.dump(self.drafts_created, f, indent=2)

    def _get_contact_info(self, email: str) -> Dict:
        """Fetch first name and company from HubSpot, fallback to email display name."""
        info = {"first_name": email.split("@")[0], "company": ""}
        try:
            # Search HubSpot contact
            endpoint = "/crm/v3/objects/contacts/search"
            payload = {
                "filterGroups": [{"filters": [{"propertyName": "email", "operator": "EQ", "value": email}]}],
                "properties": ["firstname", "company"],
                "limit": 1,
            }
            data = self.hubspot._make_request("POST", endpoint, json=payload)
            if data and data.get("results"):
                props = data["results"][0]["properties"]
                first = props.get("firstname", "")
                if first:
                    info["first_name"] = first
                company = props.get("company", "")
                if company:
                    info["company"] = company
        except Exception:
            pass
        return info

    def _fill_placeholders(self, template_body: str, data: Dict) -> str:
        """Replace {placeholders} with actual values."""
        filled = template_body
        for key, value in data.items():
            filled = filled.replace("{" + key + "}", str(value))
        return filled

    def _get_template(self, template_type: str) -> Optional[Dict]:
        return self.templates.get(template_type)

    def create_follow_up_draft(self, follow_up_item, original_email: Optional[Dict] = None) -> bool:
        """
        Create a draft for a follow-up item (from Module 1).
        Returns True if a new draft was created, False if already exists.
        """
        contact_email = follow_up_item.contact
        # Unique key to prevent duplicates
        key = f"followup-{contact_email}-{follow_up_item.action[:50]}"
        if key in self.drafts_created:
            return False

        # Determine template type based on reasoning
        reasoning = follow_up_item.reasoning.lower()
        if "no reply for" in reasoning and "14" in reasoning:
            template_type = "long_term_nudge"
        elif "no reply for" in reasoning:
            template_type = "gentle_nudge"
        else:
            template_type = "follow_up"

        template = self._get_template(template_type)
        if not template:
            return False

        contact_info = self._get_contact_info(contact_email)
        original_subject = follow_up_item.action.replace("Reply to ", "").replace("Follow up on: ", "")
        # Attempt to get original email subject from the follow-up's reasoning
        # It's stored in reasoning field now; we can extract it.
        original_subject = follow_up_item.reasoning.replace("Recent email asks: ", "").replace("No reply for", "Re: ")
        if original_subject.startswith("Re: "):
            original_subject = original_subject[4:]

        meeting_subject = follow_up_item.action if "Follow up on:" in follow_up_item.action else ""
        extra_context = ""
        if contact_info.get("company"):
            extra_context = f"I've been impressed by {contact_info['company']}'s work in the space."

        data = {
            "first_name": contact_info["first_name"],
            "original_subject": original_subject,
            "meeting_subject": meeting_subject,
            "extra_context": extra_context,
        }

        subject = self._fill_placeholders(template["subject"], data)
        body = self._fill_placeholders(template["body"], data)

        # Create draft in Outlook
        success = self.graph.create_draft(to=contact_email, subject=subject, body=body, content_type="Text")
        if success:
            self.drafts_created[key] = True
            self._save_drafts_created()
            print(f"   📧 Draft created for {contact_email} – {subject}")
        return success

    def create_post_meeting_draft(self, event: Dict) -> bool:
        """
        Create a thank-you draft after a meeting ends.
        event: dict with 'id', 'subject', 'attendees', 'start', 'end'
        """
        # Check if we already processed this meeting
        key = f"postmeeting-{event['id']}"
        if key in self.drafts_created:
            return False

        # Only process if meeting ended within last 30 minutes
        import datetime
        end_time_str = event.get("end", "")
        if end_time_str:
            try:
                end_dt = datetime.datetime.fromisoformat(end_time_str.replace("Z", "+00:00"))
                now = datetime.datetime.now(datetime.timezone.utc)
                if (now - end_dt).total_seconds() > 1800:  # 30 minutes
                    return False
            except:
                pass  # if parsing fails, proceed anyway (safe)

        # Generate draft for each external attendee
        external_attendees = []
        for email in event.get("attendees", []):
            if "@" in email and not email.endswith("@reachpathways.com"):
                external_attendees.append(email)

        if not external_attendees:
            return False

        template = self._get_template("post_meeting")
        if not template:
            return False

        created_any = False
        for email in external_attendees:
            contact_info = self._get_contact_info(email)
            extra_context = ""
            if contact_info.get("company"):
                extra_context = f"I particularly enjoyed learning more about {contact_info['company']}."

            data = {
                "first_name": contact_info["first_name"],
                "meeting_subject": event.get("subject", "our conversation"),
                "extra_context": extra_context,
            }
            subject = self._fill_placeholders(template["subject"], data)
            body = self._fill_placeholders(template["body"], data)

            success = self.graph.create_draft(to=email, subject=subject, body=body, content_type="Text")
            if success:
                created_any = True
                print(f"   📧 Post‑meeting draft for {email} – {subject}")

        if created_any:
            self.drafts_created[key] = True
            self._save_drafts_created()
        return created_any