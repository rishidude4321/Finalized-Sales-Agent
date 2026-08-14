import os
from dotenv import load_dotenv

load_dotenv()

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
LLM_MODEL = "openai/gpt-3.5-turbo"   # fallback; model selector overrides
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"

# Microsoft Graph
GRAPH_CLIENT_ID = os.getenv("GRAPH_CLIENT_ID", "c0a8deae-3619-4d5d-a52c-10545bece7a7")
GRAPH_TENANT_ID = os.getenv("GRAPH_TENANT_ID", "b78d3169-afd5-458c-a4b6-6105fa0006da")
GRAPH_AUTHORITY = f"https://login.microsoftonline.com/{GRAPH_TENANT_ID}"
GRAPH_SCOPES = ["Calendars.ReadWrite", "Mail.ReadWrite", "Mail.Send", "User.Read"]
GRAPH_CACHE_FILE = "token_cache.json"

# HubSpot
HUBSPOT_ACCESS_TOKEN = os.getenv("HUBSPOT_ACCESS_TOKEN", "")

# Serper / SerpAPI
SERPER_API_KEY = os.getenv("SERPER_API_KEY", "")
SERPAPI_API_KEY = os.getenv("SERPAPI_API_KEY", "")

# User / support
USER_EMAIL = os.getenv("USER_EMAIL", "rvira@reachpathways.com")
SUPPORT_EMAIL = os.getenv("SUPPORT_EMAIL", "rishivira4321@gmail.com")
DONE_SECRET = os.getenv("DONE_SECRET", "sasha-sales-agent-2026")
LOCAL_TIMEZONE = os.getenv("LOCAL_TIMEZONE", "America/Chicago")

# Status / control files
CONTROL_CENTER_FLAG = "control_center_sent.flag"
AGENT_STATUS_FILE = "agent_status.json"