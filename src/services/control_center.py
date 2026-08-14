"""
Control Centre email generator.
Sends Sasha a one-time email with useful links.
"""

from src.services.graph_client import GraphClient
from src.utils.config import DONE_SECRET, USER_EMAIL


def send_control_center_email():
    """Send the control centre email once. Returns True if sent."""
    graph = GraphClient()
    base = "http://localhost:8500"

    tree_link = f"{base}/tree?token={DONE_SECRET}"
    outreach_link = f"{base}/outreach?token={DONE_SECRET}"
    report_link = f"{base}/report?token={DONE_SECRET}"
    request_link = f"{base}/request?token={DONE_SECRET}"
    health_link = f"{base}/health?token={DONE_SECRET}"

    html = f"""
    <html><body>
    <h2>🛰️ Sasha's Sales Sidekick</h2>
    <ul>
      <li><a href="{tree_link}">🌴 Open Conversation Tree</a></li>
      <li><a href="{outreach_link}">🚀 Outreach Engine</a></li>
    </ul>
    <p>——— Advanced ———</p>
    <ul>
      <li><a href="{report_link}">⚠️ Report an Issue</a></li>
      <li><a href="{request_link}">💡 Request Automation</a></li>
      <li><a href="{health_link}">🩺 Health Check</a></li>
    </ul>
    </body></html>
    """
    return graph.create_draft(
        to=USER_EMAIL,
        subject="Sales Agent Tools & Control Center",
        body=html,
        content_type="HTML",
    )