import datetime
from pathlib import Path

from src.utils.logger import get_logger
from src.utils.agent_state import load_agent_status, load_usage
from src.services.graph_client import GraphClient
from src.utils.config import SUPPORT_EMAIL

logger = get_logger("status_reporter")

def _last_log_lines(n=5):
    log_file = Path("logs/agent.log")
    if log_file.exists():
        try:
            lines = log_file.read_text().splitlines()
            return lines[-n:]
        except Exception:
            return []
    return []

def send_status_email(manual=False):
    graph = GraphClient()
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

    body = []
    body.append(f"Sales Agent Status – {datetime.date.today().isoformat()}")
    body.append("")
    body.append(f"Daily Briefing: {last_briefing}")
    body.append(f"Meeting Prep: {last_prep}")
    body.append(f"Follow-ups today: {followups_today}")
    body.append(f"Drafts created today: {drafts_today}")
    body.append(f"HubSpot tasks created: {tasks_created}")
    body.append(f"Errors: {errors}")
    body.append("")
    body.append("Usage this month:")
    body.append(f"  Serper searches: {serper_month}")
    body.append(f"  SerpAPI searches: {serpapi_month}")
    body.append(f"  OpenRouter calls: {openrouter_month}")
    body.append("")
    body.append("Last log lines:")

    lines = _last_log_lines(5)
    if lines:
        body.extend(lines)
    else:
        body.append("  (no log entries)")

    subject = "Sales Agent Status (Manual)" if manual else "Sales Agent Status – Weekly"
    success = graph.send_mail(
        to=SUPPORT_EMAIL,
        subject=subject,
        body="\n".join(body),
        content_type="Text",
    )

    if success:
        logger.info("Status email sent to %s", SUPPORT_EMAIL)

    return success