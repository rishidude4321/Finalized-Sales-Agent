"""
Meeting Preparation Processor for Module 2.
Finds new calendar events, enriches attendees, and updates event body with prep notes.
"""

from datetime import datetime
import email
import json
import re
from pathlib import Path
from typing import List, Dict, Set, Optional
from src.services.graph_client import GraphClient
from src.services.hubspot_client import HubSpotClient
from src.services.company_enricher import CompanyEnricher
import urllib.parse
from src.services.conversation_tree import ConversationTreeBuilder
from src.utils.config import DONE_SECRET


PROCESSED_EVENTS_FILE = "processed_events.json"
INVITE_SUGGESTIONS_FILE = "invite_suggestions.json"
PREP_MARKER = "<!-- sales-agent-meeting-prep -->"
ICP_CRITERIA_FILE = "icp_criteria.json"


class MeetingPrepProcessor:
    def __init__(self):
        self.graph = GraphClient()
        self.hubspot = HubSpotClient()
        self.enricher = CompanyEnricher()
        self.processed_ids = self._load_processed_ids()
        self.invite_suggestions = self._load_invite_suggestions()
        self.icp_criteria = self._load_icp_criteria()
    def _load_icp_criteria(self) -> List[Dict]:
        path = Path(ICP_CRITERIA_FILE)
        if path.exists():
            try:
                return json.loads(path.read_text())
            except:
                pass
        return {}

    def _match_icp(self, company_info: Dict, meeting_subject: str) -> Optional[Dict]:
        """
        Determine which ICP segment best matches the meeting context.
        Returns a dict with segment label, match count, talking points, and relevant people.
        """
        if not self.icp_criteria:
            return None

        # Build a combined text blob from all available company info and subject
        text = meeting_subject.lower() + " "
        if company_info.get("hubspot_company"):
            hs = company_info["hubspot_company"]
            text += ((hs.get("description") or "") + " " + (hs.get("industry") or "")).lower() + " "
        if company_info.get("enriched"):
            text += (company_info["enriched"].get("description") or "").lower()

        best_segment = None
        best_score = 0
        for seg_key, seg_data in self.icp_criteria.items():
            keywords = seg_data.get("keywords", [])
            hits = sum(1 for kw in keywords if kw.lower() in text)
            if hits > best_score:
                best_score = hits
                best_segment = seg_key

        if best_segment and best_score >= 1:  # require at least 1 keyword hit
            seg_data = self.icp_criteria[best_segment]
            return {
                "segment": seg_data.get("label", best_segment),
                "score": best_score,
                "talking_points": seg_data.get("talking_points", ""),
                "relevant_people": seg_data.get("relevant_people", []),
            }
        return None
    def _load_processed_ids(self) -> Set[str]:
        path = Path(PROCESSED_EVENTS_FILE)
        if path.exists():
            try:
                return set(json.loads(path.read_text()))
            except:
                pass
        return set()

    def _save_processed_ids(self):
        with open(PROCESSED_EVENTS_FILE, "w") as f:
            json.dump(list(self.processed_ids), f)

    def _load_invite_suggestions(self) -> List[Dict]:
        path = Path(INVITE_SUGGESTIONS_FILE)
        if path.exists():
            try:
                return json.loads(path.read_text())
            except:
                pass
        return []

    def _extract_external_domains(self, attendees: List[str]) -> List[str]:
        """Return unique email domains that are not Sasha's own domain."""
        # Sasha's domain is hardcoded for now; could be from config.
        internal_domain = "reachpathways.com"
        domains = set()
        for email in attendees:
            if "@" in email:
                domain = email.split("@")[-1].lower()
                if domain != internal_domain:
                    domains.add(domain)
        return sorted(domains)

    def _get_company_info(self, domain: str) -> Dict:
        """Gather company info from HubSpot and enrichment."""
        info = {"domain": domain}
        # HubSpot company
        hs_company = self.hubspot.get_company_by_domain(domain)
        if hs_company:
            info["hubspot_company"] = hs_company
        # External enrichment (Serper)
        enriched = self.enricher.enrich_company(domain)
        if enriched:
            info["enriched"] = enriched
        return info

    def _get_attendee_details(self, attendees: List[str]) -> List[Dict]:
        """For each attendee, pull HubSpot contact if exists."""
        details = []
        for email in attendees:
            contact = None
            # Search HubSpot by email
            endpoint = f"/crm/v3/objects/contacts/search"
            payload = {
                "filterGroups": [{"filters": [{"propertyName": "email", "operator": "EQ", "value": email}]}],
                "properties": ["firstname", "lastname", "email", "jobtitle", "company"],
                "limit": 1,
            }
            data = self.hubspot._make_request("POST", endpoint, json=payload)
            if data and data.get("results"):
                c = data["results"][0]["properties"]
                contact = {
                    "name": f"{c.get('firstname','')} {c.get('lastname','')}".strip(),
                    "email": email,
                    "jobtitle": c.get("jobtitle", ""),
                    "company": c.get("company", ""),
                }
            details.append({"email": email, "hubspot_contact": contact})
        return details

    def _get_suggested_invitees(self, company_info: Dict, meeting_subject: str) -> List[Dict]:
        """Return people to invite based on criteria matching."""
        hs = company_info.get("hubspot_company", {})
        enriched = company_info.get("enriched", {})
        combined_text = (
            (hs.get("description") or "") +
            (hs.get("industry") or "") +
            (enriched.get("description") or "") +
            meeting_subject
        ).lower()
        suggestions = []
        for person in self.invite_suggestions:
            for criterion in person.get("criteria", []):
                if criterion.lower() in combined_text:
                    suggestions.append({
                        "name": person["name"],
                        "email": person["email"],
                        "role": person["role"],
                        "reason": criterion,
                    })
                    break
        return suggestions

    def _build_prep_note(self, event: Dict) -> str:
        """Assemble the full HTML prep note for a given event."""
        subject = event.get("subject", "Meeting")
        attendees = event.get("attendees", [])
        domains = self._extract_external_domains(attendees)
        
        # Company info (first domain only for simplicity)
        company_info = {}
        if domains:
            company_info = self._get_company_info(domains[0])

        # Attendee details
        attendee_details = self._get_attendee_details(attendees)

        # ICP Match
        icp_match = self._match_icp(company_info, subject)

        # Suggested invitees: combine criteria-based from company info + ICP people
        invitees = self._get_suggested_invitees(company_info, subject)
        if icp_match:
            for person_name in icp_match.get("relevant_people", []):
                # Look up person in invite_suggestions.json to get full details
                existing = next((p for p in self.invite_suggestions if p["name"].lower() == person_name.lower()), None)
                if existing and existing not in invitees:
                    invitees.append(existing)

        # If any attendee's company is empty, fill it from the domain-based company lookup
        if company_info:
            hs_company = company_info.get("hubspot_company", {})
            enriched_company = company_info.get("enriched", {})
            for ad in attendee_details:
                contact = ad.get("hubspot_contact")
                if contact and not contact.get("company"):
                    contact["company"] = hs_company.get("name") or enriched_company.get("name") or "Unknown"

        # Start building HTML
        html = f"{PREP_MARKER}\n<h2>📋 Meeting Prep: {subject}</h2>\n"

        # Attendees section
        html += "<h3>Attendees</h3><ul>\n"
        for ad in attendee_details:
            email = ad["email"]
            contact = ad.get("hubspot_contact")
            if contact:
                company_display = contact.get("company") or "Unknown"
                html += f'<li><strong>{contact["name"]}</strong> – {contact.get("jobtitle","")} at {company_display} ({email})</li>\n'
            else:
                html += f'<li>{email} (not in HubSpot)</li>\n'
        html += "</ul>\n"

        # Company background
        if company_info:
            html += "<h3>Company Background</h3>\n"
            hs = company_info.get("hubspot_company")
            if hs:
                html += f'<p><strong>{hs["name"]}</strong>'
                if hs.get("industry"):
                    html += f' – {hs["industry"]}'
                if hs.get("employees"):
                    html += f' – {hs["employees"]} employees'
                html += '</p>\n'
                if hs.get("description"):
                    html += f'<p>{hs["description"][:300]}</p>\n'
            enriched = company_info.get("enriched")
            if enriched and enriched.get("description"):
                html += f'<p><em>Web summary:</em> {enriched["description"][:300]}</p>\n'

        html += "<h3>🎯 ICP Alignment</h3>\n"
        if icp_match:
            html += f"<p><strong>Matched Segment:</strong> {icp_match['segment']} ({icp_match['score']} keyword matches)</p>\n"
            html += f"<p><strong>Talking Points:</strong> {icp_match['talking_points']}</p>\n"
        else:
            html += "<p>No strong ICP match detected.</p>\n"

        if invitees:
            invitees = self._deduplicate_invitees(invitees)
            html += "<h3>🔗 Consider Inviting</h3><ul>\n"
            for inv in invitees:
                reason = inv.get("reason") or "ICP match"
                html += f'<li><strong>{inv["name"]}</strong> ({inv.get("role", "")}) – because of "{reason}"</li>\n'
            html += "</ul>\n"

        # Conversation tree for the first external attendee
        external_emails = [
            a for a in attendees
            if "@" in a and not a.endswith("@reachpathways.com")
        ]
        if external_emails:
            primary_email = external_emails[0]
            view_link = (
                "http://localhost:8500/conversation"
                f"?email={urllib.parse.quote(primary_email)}"
                f"&token={DONE_SECRET}"
            )
            html += f'<p><a href="{view_link}">Open full conversation history in a new window</a></p>\n'

        html += f"<p><em>Generated by Sales Support Agent</em></p>"
        return html

    def process_new_events(self) -> int:
        """Find unprocessed events, generate prep notes, update them. Returns count of processed events."""
        events = self.graph.get_recent_events(days_back=7)
        processed_count = 0
        for event in events:
        # Skip events that have already ended
            if event.get("start"):
                try:
                    event_start = datetime.fromisoformat(event["start"].replace("Z", "+00:00"))
                    if event_start < datetime.now(datetime.timezone.utc):
                        continue
                except Exception:
                    pass  # if we can't parse, still process (safe fallback)
            if event["id"] in self.processed_ids:
                continue
            # Check if already has prep marker
            if PREP_MARKER in event.get("body", ""):
                self.processed_ids.add(event["id"])
                continue

            print(f"🔄 Processing meeting: {event['subject']}")
            prep_note = self._build_prep_note(event)
            success = self.graph.update_event_body(event["id"], prep_note)
            if success:
                self.processed_ids.add(event["id"])
                processed_count += 1
                print(f"   ✅ Prep note added.")
            else:
                print(f"   ❌ Failed to update event.")
        self._save_processed_ids()
        return processed_count
    
    def process_post_meetings(self):
        """
        Create post-meeting drafts for meetings that recently ended.
        Uses DraftGenerator; prevents duplicates via drafts_created.json.
        """
        from src.services.draft_generator import DraftGenerator
        draft_gen = DraftGenerator()
        recent = self.graph.get_recently_ended_meetings(minutes=5)
        if recent:
            print(f"   Found {len(recent)} recently ended meeting(s).")
        for event in recent:
            draft_gen.create_post_meeting_draft(event)

    def _deduplicate_invitees(self, invitees: List[Dict]) -> List[Dict]:
        """
        Merge duplicate invite suggestions by email.
        If the same person appears multiple times, combine their reasons into one line.
        """
        merged = {}
        for inv in invitees:
            key = inv.get("email", "").lower()
            if not key:
                key = inv.get("name", "").lower()
            if key in merged:
                existing = merged[key]
                if inv.get("reason") and inv["reason"] not in existing["reason"]:
                    existing["reason"] += "; " + inv["reason"]
            else:
                merged[key] = {
                    "name": inv["name"],
                    "email": inv.get("email", ""),
                    "role": inv.get("role", ""),
                    "reason": inv.get("reason", ""),
                }
        return list(merged.values())