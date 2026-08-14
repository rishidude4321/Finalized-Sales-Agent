"""
Microsoft Graph API client for the Sales Support Agent.
Handles authentication via cached tokens and provides methods
to read calendar events, emails, and later send mail.
"""

import os
import msal
import requests
from typing import List, Dict, Optional
from datetime import datetime as dt, timezone as dt_timezone
from zoneinfo import ZoneInfo
from src.utils.config import (
    GRAPH_CLIENT_ID,
    GRAPH_TENANT_ID,
    GRAPH_AUTHORITY,
    GRAPH_SCOPES,
    GRAPH_CACHE_FILE,
)


class GraphClient:
    """
    Wraps Microsoft Graph API calls with automatic token management.
    Uses MSAL with a serializable token cache so authentication is needed only once.
    """

    def __init__(self):
        self.client_id = GRAPH_CLIENT_ID
        self.tenant_id = GRAPH_TENANT_ID
        self.authority = GRAPH_AUTHORITY
        self.scopes = GRAPH_SCOPES
        self.cache_file = GRAPH_CACHE_FILE

        # Create a serializable token cache and load from disk if it exists
        self.token_cache = msal.SerializableTokenCache()
        if os.path.exists(self.cache_file):
            with open(self.cache_file, "r") as f:
                self.token_cache.deserialize(f.read())

        # Build the MSAL public client application
        self.app = msal.PublicClientApplication(
            client_id=self.client_id,
            authority=self.authority,
            token_cache=self.token_cache,
        )

    @staticmethod
    def _utc_to_local(iso_string: str) -> str:
        """Convert a UTC datetime string (without timezone suffix) to local time."""
        if not iso_string:
            return ""

        # Parse the ISO string as a naive datetime, then attach UTC timezone
        if '.' in iso_string:
            base, frac = iso_string.split('.', 1)
            iso_string = base + '.' + frac[:6]
        utc_dt = dt.fromisoformat(iso_string).replace(tzinfo=dt_timezone.utc)

        # Convert to the desired local timezone (use config value)
        from src.utils.config import LOCAL_TIMEZONE   # "America/Chicago"
        local_tz = ZoneInfo(LOCAL_TIMEZONE)
        local_dt = utc_dt.astimezone(local_tz)

        return local_dt.strftime("%Y-%m-%dT%H:%M:%S")

    def _get_access_token(self) -> Optional[str]:
        """
        Silently acquire an access token from the cache.
        Returns the access token string or None if authentication is required.
        """
        accounts = self.app.get_accounts()
        if accounts:
            result = self.app.acquire_token_silent(self.scopes, account=accounts[0])
            if result and "access_token" in result:
                # Token cache may have been refreshed – save it back to disk
                with open(self.cache_file, "w") as f:
                    f.write(self.token_cache.serialize())
                return result["access_token"]
        return None

    def _make_request(self, method: str, url: str, **kwargs) -> Optional[Dict]:
        """
        Internal helper to send an authenticated request to Microsoft Graph.
        Handles token acquisition and retries once if token is expired.
        """
        token = self._get_access_token()
        if not token:
            raise RuntimeError(
                "No valid Graph token found. Run auth_graph.py to authenticate first."
            )

        headers = kwargs.pop("headers", {})
        headers["Authorization"] = f"Bearer {token}"
        response = requests.request(method, url, headers=headers, **kwargs)

        if response.status_code == 401:
            # Token might have expired – try to refresh and retry once
            token = self._get_access_token()
            if token:
                headers["Authorization"] = f"Bearer {token}"
                response = requests.request(method, url, headers=headers, **kwargs)

        if response.ok:
            try:
                return response.json()
            except ValueError:
                return {}
        else:
            # Log the error and return None; caller should handle gracefully
            print(f"Graph API error {response.status_code}: {response.text}")
            return None

    def get_recent_emails(self, days: int = 3, top: int = 20) -> List[Dict]:
        """
        Retrieve recent messages from the inbox (read and unread) from the last `days` days.
        Returns up to `top` messages, ordered by receivedDateTime desc.
        """
        from datetime import datetime, timedelta, timezone
        since = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")

        endpoint = (
            "https://graph.microsoft.com/v1.0/me/mailFolders('Inbox')/messages"
            f"?$filter=receivedDateTime ge {since}"
            f"&$orderby=receivedDateTime desc"
            f"&$top={top}"
            f"&$select=from,subject,bodyPreview,receivedDateTime,isRead,conversationId"
        )
        data = self._make_request("GET", endpoint)
        if not data:
            return []

        emails = []
        for msg in data.get("value", []):
            sender_obj = msg.get("from", {}).get("emailAddress", {})
            emails.append({
                "from_name": sender_obj.get("name", ""),
                "from_address": sender_obj.get("address", "unknown"),
                "subject": msg.get("subject", "(no subject)"),
                "body": msg.get("bodyPreview", "")[:800],
                "preview": msg.get("bodyPreview", "")[:300],
                "received": msg.get("receivedDateTime", ""),
                "isRead": msg.get("isRead", False),
                "conversationId": msg.get("conversationId", ""),
            })
        return emails

    def get_emails_with_contact(self, email: str, days: int = 180, top: int = 1000) -> List[Dict]:
        """
        Retrieve emails exchanged with a specific contact, including CC.
        Uses simple supported filters only; recipient/CC and date filtering are done locally.
        """
        import datetime

        email_lower = email.lower().strip()
        since = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")

        # 1) Inbox messages from this sender (no date or orderby filter)
        inbox_endpoint = (
            "https://graph.microsoft.com/v1.0/me/mailFolders('Inbox')/messages"
            f"?$filter=from/emailAddress/address eq '{email_lower}'"
            f"&$top={top}"
            "&$select=conversationId,from,toRecipients,ccRecipients,subject,bodyPreview,receivedDateTime,webLink"
        )
        inbox_data = self._make_request("GET", inbox_endpoint)
        inbox_messages = inbox_data.get("value", []) if inbox_data else []

        # 2) Sent items from last `days` days; recipient/CC matched locally
        sent_endpoint = (
            "https://graph.microsoft.com/v1.0/me/mailFolders('SentItems')/messages"
            f"?$filter=sentDateTime ge {since}"
            f"&$top={top}"
            "&$select=conversationId,from,toRecipients,ccRecipients,subject,bodyPreview,receivedDateTime,sentDateTime,webLink"
        )
        sent_data = self._make_request("GET", sent_endpoint)
        sent_messages = sent_data.get("value", []) if sent_data else []

        messages = []

        # Inbox messages are all from the contact; apply local date filter only
        for msg in inbox_messages:
            received = msg.get("receivedDateTime", "")
            if received and received < since:
                continue
            sender_obj = msg.get("from", {}).get("emailAddress", {})
            sender = sender_obj.get("address", "unknown")
            sender_name = sender_obj.get("name", "")
            messages.append(self._format_email_message(msg, email_lower, sender, sender_name, "from", date_value=received))

        # Sent messages where the contact is in To or CC
        for msg in sent_messages:
            sent_dt = msg.get("sentDateTime", "") or msg.get("receivedDateTime", "")
            if sent_dt and sent_dt < since:
                continue
            to_emails = [r.get("emailAddress", {}).get("address", "").lower() for r in msg.get("toRecipients", [])]
            cc_emails = [r.get("emailAddress", {}).get("address", "").lower() for r in msg.get("ccRecipients", [])]
            if email_lower in to_emails or email_lower in cc_emails:
                sender_obj = msg.get("from", {}).get("emailAddress", {})
                sender = sender_obj.get("address", "unknown")
                sender_name = sender_obj.get("name", "")
                messages.append(self._format_email_message(msg, email_lower, sender, sender_name, "to", date_value=sent_dt))

        # Deduplicate by message ID, then sort by date ascending
        seen = set()
        unique = []
        for m in messages:
            if m["id"] not in seen:
                seen.add(m["id"])
                unique.append(m)
        unique.sort(key=lambda x: x.get("received", ""))
        return unique

    def _format_email_message(self, msg: Dict, contact_email: str, sender: str, sender_name: str, direction: str, date_value: str = None) -> Dict:
        """Format a Graph message into our conversation event structure."""
        recipients = [r.get("emailAddress", {}).get("address", "") for r in msg.get("toRecipients", [])]
        cc = [r.get("emailAddress", {}).get("address", "") for r in msg.get("ccRecipients", [])]
        return {
            "id": msg["id"],
            "conversationId": msg.get("conversationId", ""),
            "subject": msg.get("subject", "(no subject)"),
            "sender": sender,
            "sender_name": sender_name,
            "direction": direction,
            "recipients": recipients,
            "cc": cc,
            "preview": msg.get("bodyPreview", "")[:200],
            "received": date_value or msg.get("receivedDateTime", ""),
            "webLink": msg.get("webLink", ""),
        }

    def get_recent_conversations(self, days=14) -> List[Dict]:
        """
        Retrieve recent sent emails and the corresponding replies to build
        a conversation log for follow-up tracking.
        Returns a list of threads, each with participants, last message date, and a snippet.
        """
        from datetime import datetime, timedelta, timezone
        since = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")

        # Fetch recent sent emails
        sent_endpoint = (
            "https://graph.microsoft.com/v1.0/me/mailFolders('SentItems')/messages"
            f"?$filter=sentDateTime ge {since}"
            "&$orderby=sentDateTime desc"
            "&$select=conversationId,subject,toRecipients,sentDateTime,bodyPreview"
        )
        sent_data = self._make_request("GET", sent_endpoint)
        if not sent_data:
            return []

        threads = {}
        # Process sent emails
        for msg in sent_data.get("value", []):
            conv_id = msg.get("conversationId")
            if not conv_id:
                continue
            recipients = [r.get("emailAddress", {}).get("address") for r in msg.get("toRecipients", [])]
            threads[conv_id] = {
                "subject": msg.get("subject", ""),
                "participants": recipients,
                "last_sent": msg.get("sentDateTime", ""),
                "last_message_preview": msg.get("bodyPreview", "")[:100],
                "last_direction": "you",
                "conversationId": conv_id,
            }

        # Also fetch recent received emails from those same contacts (if they replied)
        # Simplified: just get all unread emails from the same period and match conversationIds
        # (We already have unread; we can reuse that call if we pass to this function)
        # For now, we'll integrate in the chain.
        return list(threads.values())

    def get_calendar_events(self, days: int = 1) -> List[Dict]:
        """Retrieve today's calendar events."""
        import datetime

        # Use timezone-aware datetime objects (UTC)
        now = datetime.datetime.now(datetime.timezone.utc)
        start_time = now.replace(hour=0, minute=0, second=0, microsecond=0)
        end_time = start_time + datetime.timedelta(days=days)

        # Format as ISO 8601 with 'Z' suffix (UTC)
        start_iso = start_time.strftime("%Y-%m-%dT%H:%M:%SZ")
        end_iso = end_time.strftime("%Y-%m-%dT%H:%M:%SZ")

        params = {
            "startDateTime": start_iso,
            "endDateTime": end_iso,
            "$select": "subject,start,end,attendees",
        }
        endpoint = "https://graph.microsoft.com/v1.0/me/calendarView"
        data = self._make_request("GET", endpoint, params=params)


        if not data:
            return []

        events = []
        for event in data.get("value", []):
            attendees = event.get("attendees", [])
            attendee_emails = [
                att.get("emailAddress", {}).get("address", "")
                for att in attendees
            ]
            events.append({
                "subject": event.get("subject", "No Subject"),
                "start": self._utc_to_local(event.get("start", {}).get("dateTime", "")),
                "end": self._utc_to_local(event.get("end", {}).get("dateTime", "")),
                "attendees": attendee_emails,
            })
        return events

    def get_unread_emails(self, top: int = 10) -> List[Dict]:
        endpoint = (
            f"https://graph.microsoft.com/v1.0/me/messages"
            f"?$filter=isRead eq false"
            f"&$orderby=receivedDateTime desc"
            f"&$top={top}"
            f"&$select=from,subject,body,receivedDateTime"
        )
        data = self._make_request("GET", endpoint)
        if not data:
            return []

        emails = []
        for msg in data.get("value", []):
            sender_obj = msg.get("from", {}).get("emailAddress", {})
            sender_name = sender_obj.get("name", "")
            sender_address = sender_obj.get("address", "unknown")
            # Get full body content (first 800 chars is safe for prompt)
            body_content = msg.get("body", {}).get("content", "")[:800]
            emails.append({
                "from_name": sender_name,
                "from_address": sender_address,
                "subject": msg.get("subject", "(no subject)"),
                "preview": body_content[:300],   # shorter preview for context
                "body": body_content,            # full (truncated) body for Claude
                "received": msg.get("receivedDateTime", ""),
            })
        return emails

    def send_mail(self, to: str, subject: str, body: str, content_type: str = "Text") -> bool:
        """
        Send an email via Microsoft Graph. (Used later for delivering the briefing.)

        Args:
            to: Recipient email address.
            subject: Email subject.
            body: Email body.
            content_type: "Text" or "HTML".

        Returns:
            True if successful, False otherwise.
        """
        email_json = {
            "message": {
                "subject": subject,
                "body": {
                    "contentType": content_type,
                    "content": body,
                },
                "toRecipients": [
                    {"emailAddress": {"address": to}}
                ],
            },
            "saveToSentItems": "true",
        }
        endpoint = "https://graph.microsoft.com/v1.0/me/sendMail"
        result = self._make_request("POST", endpoint, json=email_json)
        return result is not None


    def create_draft(self, to: str, subject: str, body: str, content_type: str = "Text") -> bool:
        """
        Create an email draft in the user's Outlook drafts folder.
        The message is saved but NOT sent.
        """
        draft_json = {
            "subject": subject,
            "body": {
                "contentType": content_type,
                "content": body,
            },
            "toRecipients": [
                {"emailAddress": {"address": to}}
            ],
        }
        endpoint = "https://graph.microsoft.com/v1.0/me/messages"
        result = self._make_request("POST", endpoint, json=draft_json)
        if result:
            print(f"📧 Draft created in Outlook: {subject}")
            return True
        else:
            print("⚠️ Failed to create draft.")
            return False
        
    def update_event_body(self, event_id: str, new_body: str) -> bool:
        """
        Overwrite the body of an existing calendar event.
        Prepends the new content to any existing body (so prep note appears first).
        """
        endpoint = f"https://graph.microsoft.com/v1.0/me/events/{event_id}"
        # First, get current body to preserve existing notes
        current = self._make_request("GET", endpoint)
        current_body = ""
        if current:
            current_body = current.get("body", {}).get("content", "")

        # Combine: new prep note on top, old body below, separated by a divider.
        combined = new_body + "\n\n---\n\n" + current_body if current_body.strip() else new_body

        payload = {
            "body": {
                "contentType": "HTML",
                "content": combined,
            }
        }
        result = self._make_request("PATCH", endpoint, json=payload)
        return result is not None

    def get_recent_events(self, days_back: int = 7) -> List[Dict]:
        """Get calendar events from the last `days_back` days (up to 50)."""
        import datetime
        now = datetime.datetime.now(datetime.timezone.utc)
        start = (now - datetime.timedelta(days=days_back)).strftime("%Y-%m-%dT%H:%M:%SZ")
        end = now.strftime("%Y-%m-%dT%H:%M:%SZ")

        params = {
            "startDateTime": start,
            "endDateTime": end,
            "$select": "id,subject,start,end,attendees,createdDateTime,body",
            "$top": 50,
            "$orderby": "createdDateTime desc",
        }
        endpoint = "https://graph.microsoft.com/v1.0/me/events"
        data = self._make_request("GET", endpoint, params=params)
        if not data:
            return []
        events = []
        for event in data.get("value", []):
            attendees = [a.get("emailAddress", {}).get("address", "") for a in event.get("attendees", [])]
            events.append({
                "id": event["id"],
                "subject": event.get("subject", "No Subject"),
                "start": event.get("start", {}).get("dateTime", ""),
                "end": event.get("end", {}).get("dateTime", ""),
                "attendees": attendees,
                "created": event.get("createdDateTime", ""),
                "body": event.get("body", {}).get("content", ""),
            })
        return events
    
    def get_recently_ended_meetings(self, minutes: int = 30) -> List[Dict]:
        """
        Retrieve meetings that ended within the last `minutes` minutes.
        Returns events with id, subject, start, end, and attendees.
        """
        import datetime
        now = datetime.datetime.now(datetime.timezone.utc)
        window_start = (now - datetime.timedelta(minutes=minutes)).strftime("%Y-%m-%dT%H:%M:%SZ")
        end_window = now.strftime("%Y-%m-%dT%H:%M:%SZ")

        params = {
            "startDateTime": window_start,
            "endDateTime": end_window,
            "$select": "id,subject,start,end,attendees",
            "$top": 50,
            "$orderby": "end/dateTime desc",
        }
        endpoint = "https://graph.microsoft.com/v1.0/me/calendarView"
        data = self._make_request("GET", endpoint, params=params)
        if not data:
            return []

        import datetime as dt_module
        now_utc = dt_module.datetime.now(dt_module.timezone.utc)
        events = []
        for event in data.get("value", []):
            end_str = event.get("end", {}).get("dateTime", "")
            if end_str:
                try:
                    end_dt = dt_module.datetime.fromisoformat(end_str.replace("Z", "+00:00"))
                    if end_dt.tzinfo is None:
                        end_dt = end_dt.replace(tzinfo=dt_module.timezone.utc)
                    if end_dt >= now_utc:
                        continue
                except Exception as e:
                    print(f"Time parse error for '{end_str}': {e}")
            attendees = [a.get("emailAddress", {}).get("address", "") for a in event.get("attendees", [])]
            events.append({
                "id": event["id"],
                "subject": event.get("subject", "No Subject"),
                "start": event.get("start", {}).get("dateTime", ""),
                "end": end_str,
                "attendees": attendees,
            })
        return events

    def get_events_with_attendee(self, email: str, days: int = 365, top: int = 500) -> List[Dict]:
        """
        Retrieve calendar events where the given email was an attendee.
        Returns event id, subject, start, end, attendees, webLink.
        """
        import datetime
        now = datetime.datetime.now(datetime.timezone.utc)
        start_dt = now - datetime.timedelta(days=days)
        end_dt = now + datetime.timedelta(days=30)   # include upcoming near-term meetings

        params = {
            "startDateTime": start_dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "endDateTime": end_dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "$select": "id,subject,start,end,attendees,webLink",
            "$top": top,
        }
        endpoint = "https://graph.microsoft.com/v1.0/me/calendarView"
        data = self._make_request("GET", endpoint, params=params)
        if not data:
            return []

        target = email.lower().strip()
        events = []
        for event in data.get("value", []):
            attendees = event.get("attendees", [])
            attendee_emails = [a.get("emailAddress", {}).get("address", "").lower() for a in attendees]
            if target not in attendee_emails:
                continue
            events.append({
                "id": event["id"],
                "subject": event.get("subject", "No Subject"),
                "start": event.get("start", {}).get("dateTime", ""),
                "end": event.get("end", {}).get("dateTime", ""),
                "attendees": [a.get("emailAddress", {}).get("address", "") for a in attendees],
                "webLink": event.get("webLink", ""),
            })
        # Sort by start date descending (most recent first)
        events.sort(key=lambda x: x.get("start", ""), reverse=True)
        return events