import os
from dotenv import load_dotenv

# Load .env file into environment variables
load_dotenv()

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")

# Model you want to use via OpenRouter (Claude 3.5 Sonnet)
LLM_MODEL = "openai/gpt-3.5-turbo"

# OpenRouter base URL
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"

# Microsoft Graph credentials (from Azure App Registration)
GRAPH_CLIENT_ID = "c0a8deae-3619-4d5d-a52c-10545bece7a7"
GRAPH_TENANT_ID = "b78d3169-afd5-458c-a4b6-6105fa0006da"
GRAPH_AUTHORITY = f"https://login.microsoftonline.com/{GRAPH_TENANT_ID}"
GRAPH_SCOPES = ["Calendars.ReadWrite", "Mail.ReadWrite", "Mail.Send", "User.Read"]
GRAPH_CACHE_FILE = "token_cache.json"   # Saved in project root

# Hubspot credentials 



# Local timezone for converting UTC datetimes from Microsoft Graph
LOCAL_TIMEZONE = "America/Chicago"

# Model selection requirements
MODEL_REQUIREMENTS = {
    "capabilities": [],                    # no mandatory capabilities needed
    "type": "chat",                        # must be a chat model
    "max_price_per_1k_tokens": 0.05,       # generous cap (covers Claude 3.5, GPT-4o, etc.)
}

# List of models we know work well (quality boost) – will be augmented by live discovery
KNOWN_GOOD_MODELS = [
    "openai/gpt-4-turbo",
    "anthropic/claude-opus-4",
    "openai/gpt-4o",
]