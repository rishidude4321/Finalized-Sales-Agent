"""
Draft Generator for Module 3.
Creates personalized draft emails using deterministic facts and optional LLM phrasing.
Validates LLM output; falls back to templates if anything is missing.
"""

import json
from pathlib import Path
from typing import Dict, Optional

from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate

from src.services.graph_client import GraphClient
from src.services.hubspot_client import HubSpotClient
from src.services.model_selector import get_best_model
from src.utils.config import OPENROUTER_API_KEY, OPENROUTER_BASE_URL
from src.utils.logger import get_logger

logger = get_logger("draft_generator")


class DraftGenerator:
    TEMPLATES_FILE = "email_templates.json"
    DRAFTS_CREATED_FILE = "drafts_created.json"

    def __init__(self):
        self.graph = GraphClient()
        self.hubspot = HubSpotClient()
        self.templates = self._load_templates()
        self.drafts_created = self._load_drafts_created()

    # ------------------------------------------------------------------
    # File helpers
    # ------------------------------------------------------------------
    def _load_templates(self) -> Dict:
        path = Path(self.TEMPLATES_FILE)
        if path.exists():
            try:
                return json.loads(path.read_text())
            except Exception:
                pass
        return {}

    def _load_drafts_created(self) -> Dict:
        path = Path(self.DRAFTS_CREATED_FILE)
        if path.exists():
            try:
                return json.loads(path.read_text())
            except Exception:
                pass
        return {}

    def _save_drafts_created(self):
        with open(self.DRAFTS_CREATED_FILE, "w") as f:
            json.dump(self.drafts_created, f, indent=2)

    # ------------------------------------------------------------------
    # Data gathering
    # ------------------------------------------------------------------
    def _get_contact_info(self, email: str) -> Dict:
        """Fetch first name and company from HubSpot.
        first_name will be empty if not found, so the LLM/template can use a neutral greeting.
        """
        info = {"first_name": "", "company": ""}
        try:
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

    def _get_original_email_preview(self, thread_id: str, contact_email: str) -> str:
        """
        Fetch the most recent email from the given conversation thread that involves the contact.
        Returns a short preview string, or empty string if not available.
        """
        if not thread_id:
            return ""
        try:
            emails = self.graph.get_emails_with_contact(contact_email, days=30)
            for mail in reversed(emails):
                if mail.get("conversationId") == thread_id:
                    return (mail.get("preview") or mail.get("body") or "")[:200]
        except Exception as e:
            logger.warning("Could not fetch original email preview for thread %s: %s", thread_id, e)
        return ""

    # ------------------------------------------------------------------
    # LLM email writer
    # ------------------------------------------------------------------
    def _build_llm(self):
        model_id = get_best_model()
        return ChatOpenAI(
            model=model_id,
            openai_api_key=OPENROUTER_API_KEY,
            openai_api_base=OPENROUTER_BASE_URL,
            temperature=0.5,
            max_tokens=300,
        )

    def _generate_draft_with_llm(self, context: Dict) -> str:
        """Generate a natural email body using the LLM. Returns the body text."""
        system_prompt = (
            "You are Sasha's executive assistant. Write a brief, natural, professional email "
            "from Sasha Peña, Head of Emploability, REACH Pathways.\n"
            "Use ONLY the following verified facts. Do not invent names, dates, numbers, promises, links, or offers.\n"
            "Keep the email under 120 words.\n"
            "Use a warm, peer-to-peer tone. Avoid generic text that doesn't feel like from an actual human.\n"
            "If the recipient first name is empty, start with 'Good Morning' or 'Good Afternoon' without a name. Do not use email prefixes as names.\n"
            "Preserve the signature exactly as provided.\n\n"
            "VERIFIED FACTS:\n"
            f"Recipient first name: {context.get('first_name') or 'Unknown'}\n"
            f"Recipient company: {context.get('company') or 'Unknown'}\n"
            f"Original email subject: {context.get('original_subject')}\n"
            f"Original email preview: {context.get('email_preview') or 'N/A'}\n"
            f"Follow-up type: {context.get('follow_up_type')}\n"
            f"Days since last contact: {context.get('days_since', 'unknown')}\n\n"
            "Signature to use exactly:\n"
            "Best,\n"
            "Sasha Peña\n"
            "Head of Emploability\n"
            "REACH Pathways"
        )

        prompt = ChatPromptTemplate.from_messages([
            ("system", system_prompt),
            ("human", "Write the email body now please."),
        ])

        llm = self._build_llm()
        chain = prompt | llm
        response = chain.invoke({})
        return response.content if hasattr(response, "content") else str(response)

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------
    def _validate_llm_draft(self, draft: str, context: Dict) -> bool:
        """Return True if the LLM draft contains the minimal required signature."""
        if not draft:
            return False
        required = [
            "Sasha Peña".lower(),
            "REACH Pathways".lower(),
            "Head of Employability".lower(),
        ]
        # If we have a first name, ensure it appears in the greeting
        if context.get("first_name"):
            required.append(context["first_name"].lower())

        return all(part in draft.lower() for part in required)

    # ------------------------------------------------------------------
    # Public methods
    # ------------------------------------------------------------------
    def create_follow_up_draft(self, follow_up_item, original_email: Optional[Dict] = None) -> bool:
        """
        Create a personalized follow-up draft in Outlook.
        Uses LLM-generated body if it validates, otherwise falls back to templates.
        """
        contact_email = follow_up_item.contact
        key = f"followup-{contact_email}-{follow_up_item.action[:50]}"
        if key in self.drafts_created:
            return False

        contact_info = self._get_contact_info(contact_email)

        # Determine follow-up type from reasoning
        reasoning = follow_up_item.reasoning.lower()
        if "no reply for" in reasoning and "14" in reasoning:
            follow_up_type = "long_term_nudge"
        elif "no reply for" in reasoning:
            follow_up_type = "gentle_nudge"
        else:
            follow_up_type = "follow_up"

        # Extract the real email subject from the follow-up action
        original_subject = self._extract_original_subject(follow_up_item.action)

        email_preview = self._get_original_email_preview(
            follow_up_item.thread_id, contact_email
        )

        context = {
            "first_name": contact_info["first_name"],
            "company": contact_info.get("company", ""),
            "original_subject": original_subject,
            "email_preview": email_preview,
            "follow_up_type": follow_up_type,
            "days_since": "unknown",
        }

        # Try LLM generation
        body = None
        try:
            body = self._generate_draft_with_llm(context)
            if not self._validate_llm_draft(body, context):
                logger.warning("LLM draft failed validation, using template fallback.")
                body = None
        except Exception as e:
            logger.warning("LLM draft generation failed: %s", e)
            body = None

        # Fallback to template if needed
        if not body:
            template = self.templates.get(follow_up_type)
            greeting_name = context["first_name"] or ""
            if greeting_name:
                greeting = f"Hi {greeting_name}"
            else:
                greeting = "Good Morning"
            if template:
                subject_template = template.get("subject", "Follow-up")
                body_template = template.get("body", "")
                subject = subject_template.replace("{original_subject}", original_subject)
                body = body_template.replace("{first_name}", greeting_name)
                body = body.replace("{original_subject}", original_subject)
                body = body.replace("{email_preview}", email_preview)
                body = body.replace("{greeting}", greeting)
            else:
                subject = f"Re: {original_subject}"
                body = f"{greeting},\n\nJust following up on this.\n\nBest,\nSasha Peña\nFounder & Partnerships Lead\nREACH Pathways"

        # Build subject deterministically
        if follow_up_type == "gentle_nudge":
            subject = f"Checking in – {original_subject}"
        elif follow_up_type == "long_term_nudge":
            subject = f"Reconnecting – {original_subject}"
        else:
            subject = f"Following up – {original_subject}"

        success = self.graph.create_draft(
            to=contact_email,
            subject=subject,
            body=body,
            content_type="Text",
        )

        if success:
            self.drafts_created[key] = True
            self._save_drafts_created()
            logger.info("Draft created for %s – %s", contact_email, subject)
        return success

    def create_post_meeting_draft(self, event: Dict) -> bool:
        """
        Create a thank-you draft after a meeting ends.
        Uses LLM with validation; falls back to template.
        """
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
                if (now - end_dt).total_seconds() > 1800:
                    return False
            except Exception:
                pass

        external_attendees = [
            email for email in event.get("attendees", [])
            if "@" in email and not email.endswith("@reachpathways.com")
        ]
        if not external_attendees:
            return False

        created_any = False
        for email in external_attendees:
            contact_info = self._get_contact_info(email)
            context = {
                "first_name": contact_info["first_name"],
                "company": contact_info.get("company", ""),
                "original_subject": event.get("subject", "our meeting"),
                "email_preview": "",
                "follow_up_type": "post_meeting",
                "days_since": "0",
            }

            body = None
            try:
                body = self._generate_draft_with_llm(context)
                if not self._validate_llm_draft(body, context):
                    body = None
            except Exception as e:
                logger.warning("LLM post-meeting draft failed: %s", e)

            if not body:
                template = self.templates.get("post_meeting")
                if template:
                    subject_template = template.get("subject", "Great meeting you")
                    body_template = template.get("body", "")
                    subject = subject_template.replace("{meeting_subject}", event.get("subject", "our conversation"))
                    body = body_template.replace("{first_name}", context["first_name"])
                else:
                    subject = f"Great meeting you – {event.get('subject', 'our conversation')}"
                    body = f"Hi {context['first_name']},\n\nIt was great meeting you today. Thank you for the time.\n\nBest,\nSasha Peña"

            subject = f"Great meeting you – {event.get('subject', 'our conversation')}"

            success = self.graph.create_draft(
                to=email,
                subject=subject,
                body=body,
                content_type="Text",
            )
            if success:
                created_any = True
                logger.info("Post-meeting draft created for %s", email)

        if created_any:
            self.drafts_created[key] = True
            self._save_drafts_created()
        return created_any

    def _extract_original_subject(self, action: str) -> str:
        """
        Clean the follow-up action text down to the real email subject.
        Examples:
          'Reply to Rishi Vira re: ahhhhhh' -> 'ahhhhhh'
          'Follow up on: some subject'      -> 'some subject'
          'Review: circling v2'             -> 'circling v2'
        """
        subject = action.strip()

        # 'Reply to <name> re: <subject>'
        if subject.lower().startswith("reply to "):
            if " re: " in subject:
                subject = subject.split(" re: ", 1)[1].strip()
            else:
                # No re: marker, take everything after 'Reply to <name>'? keep as is.
                subject = subject[len("Reply to "):].strip()
                # Remove the name part if present, e.g. 'Rishi Vira re: ahhhhhh'
                if " re: " in subject:
                    subject = subject.split(" re: ", 1)[1].strip()

        # 'Follow up on: <subject>'
        elif subject.lower().startswith("follow up on:"):
            subject = subject[len("Follow up on:"):].strip()

        # 'Review: <subject>'
        elif subject.lower().startswith("review:"):
            subject = subject[len("Review:"):].strip()

        # Strip common prefixes one more time
        lower = subject.lower()
        for prefix in ["re:", "fw:", "fwd:"]:
            if lower.startswith(prefix):
                subject = subject[3:].strip()
                break

        return subject.strip()