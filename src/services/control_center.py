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
    report_link = f"{base}/report?token={DONE_SECRET}"
    request_link = f"{base}/request?token={DONE_SECRET}"
    health_link = f"{base}/health?token={DONE_SECRET}"
    control_link = f"{base}/control?token={DONE_SECRET}"

    html = f"""
    <html><body>
    <h2>🛰️ Your Sales Support Agent is ready</h2>
    <p>Pin this email — it's your control centre.</p>
    <ul>
      <li><a href="{tree_link}">📋 Open Conversation Tree</a></li>
      <li><a href="{control_link}">📂 Open Full Control Centre</a></li>
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
        subject="Your Sales Support Agent – Control Centre",
        body=html,
        content_type="HTML",
    )