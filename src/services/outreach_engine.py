"""
Automated Outreach Engine – Flask integrated version.
Generates targeted outreach copy and live LinkedIn lead searches.
Uses OpenRouter via LangChain and SerpAPI for lead lookups.
"""

import os
import re
import requests
from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate
from src.utils.config import OPENROUTER_API_KEY, OPENROUTER_BASE_URL, SERPAPI_API_KEY
from src.services.model_selector import get_best_model
from src.utils.agent_state import increment_usage

# ----------------------------------------------------------------------
# Sasha's ICP defaults – prefill the form, keep editable
# ----------------------------------------------------------------------
DEFAULT_TARGET_SECTOR = "Corporate HR & Diversity Leaders at companies with $10M-$1B+ revenue"
DEFAULT_COMPANY_SCALE = "Any scale"
DEFAULT_LOCATION = "Chicago"
DEFAULT_CAMPAIGN_GOAL = "Book an introductory discovery call"
DEFAULT_PRICING_CONTEXT = "$1,500 per job placement fee, or multi-year district/institutional SaaS scaling models ranging from $5K-$15K/yr."
DEFAULT_USER_BIO_CONTEXT = ""
DEFAULT_WRITING_STYLE = ""
# Sasha's profile – used as sender context in the email drafts
SENDER_NAME = "Sasha Peña"
SENDER_TITLE = "Head of Employability"
SENDER_COMPANY = "REACH Pathways"
SENDER_LINKEDIN = "https://www.linkedin.com/in/sashapena/"  # replace with real URL
DEFAULT_USER_BIO_CONTEXT = (
    f"{SENDER_NAME}, {SENDER_TITLE} at {SENDER_COMPANY}. "
    f"LinkedIn: {SENDER_LINKEDIN}."
)

# Additional ICP context injected into the prompt, not shown in the form.
ICP_EXACT_TITLES = [
    "Chief Human Resources Officer (CHRO)",
    "Head of Diversity & Inclusion",
    "University Relations Director",
    "Campus Recruitment Manager",
    "K-12 District Procurement Officer",
    "Charter School Network Executive",
    "University Provost or Dean of Admissions",
]

ICP_PAIN_POINTS = (
    "High recruitment spend, low retention rates for early/diverse talent, and trouble "
    "consistently accessing highly vetted talent pools from under-resourced communities "
    "to hit diverse hiring quotas."
)

ICP_VALUE_PROP = (
    "Direct B2B pipeline access to a highly curated, tech-tracked, diverse early talent "
    "pool with verified performance data metrics built into REACH."
)


def _build_llm():
    """Create a LangChain ChatOpenAI instance using the best available OpenRouter model."""
    model_id = get_best_model()
    return ChatOpenAI(
        model=model_id,
        openai_api_key=OPENROUTER_API_KEY,
        openai_api_base=OPENROUTER_BASE_URL,
        temperature=0.7,
        max_tokens=1200,
    )


SYSTEM_PROMPT = (
    "You are an elite B2B growth copywriter and database strategist.\n"
    "Your objective is to generate clear, non-robotic outreach copy and an operational Google X-Ray search query.\n\n"
    "--- STRATEGIC INPUTS (FOR EMAIL CONTENT ONLY) ---\n"
    "Target Client Profile: {target_sector}\n"
    "Scale/Size: {company_scale}\n"
    "Target Location: {target_location}\n"
    "Campaign Goal: {campaign_goal}\n"
    "Offer / Pricing: {pricing_context}\n\n"
    "--- SENDER PROFILE (STYLE GUIDE ONLY) ---\n"
    "Sender Context/Links: {user_bio_context}\n"
    "Sender Voice/Writing Style Sample: {writing_style}\n"
    "---------------------\n\n"
    "CRITICAL COPYWRITING VOICE RULES:\n"
    "1. DO NOT SIMPLY REWRITE OR EDIT THE SENDER WRITING STYLE SAMPLE. The sample is provided ONLY as a baseline for tone, pacing, and vocabulary. You must draft a COMPLETELY NEW, original outreach email targeted directly at the 'Target Client Profile' that fulfills the 'Campaign Goal'.\n"
    "2. If 'Sender Voice/Writing Style Sample' is empty, use a modern, human, peer-to-peer tone (no fake corporate platitudes like 'I hope this email finds you well').\n"
    "3. Seamlessly weave the 'Sender Context/Links' naturally into the call-to-action blocks of your new email variations.\n\n"
    "CRITICAL X-RAY QUERY RULES:\n"
    "1. TOTAL ISOLATION: The search query must be constructed using ONLY the 'Target Client Profile' and 'Target Location'. COMPLETELY IGNORE the Sender Context, Voice, and Campaign Goal when building the search query.\n"
    "2. SCALE VS PERSONA MAPPING:\n"
    "   - If Scale is Enterprise ($1B+) or Large Scale ($100M-$1B), prioritize high-ranking decision-maker titles inside quotes (e.g., \"Chief Human Resources Officer\", \"Head of Diversity\", \"University Relations Director\").\n"
    "   - If the Target Profile implies a hands-on operator or specific program manager, look specifically for those functional operating titles.\n"
    "3. CONCEPT-TO-KEYWORD TRANSLATION:\n"
    "   - Translate abstract problem descriptions into exact industry buzzwords that people put on their LinkedIn profiles.\n"
    "4. KEEP IT LINEAR & FAST:\n"
    "   - Do not use complex nested parentheses or long chains of OR operators. Pick the best title phrase, pair it with the translated industry sector keyword, and let the backend handle location mapping.\n"
    "5. START PREFIX:\n"
    "   - The query MUST start with 'site:linkedin.com/in/' exactly. Never omit or alter this prefix.\n\n"
    "Output rules:\n"
    "6. NEVER USE THE EXACT LONG TARGET PROFILE TEXT AS THE SEARCH QUERY.\n"
    "   - The target profile is a paragraph. You must convert it into 1-2 exact job titles plus an industry keyword.\n"
    "   - Example target profile: 'Corporate HR & Diversity Leaders at companies with $10M-$1B+ revenue in Chicago'\n"
    "     Converted query: (\"Chief Human Resources Officer\" OR \"Head of Diversity and Inclusion\") AND (\"talent acquisition\" OR \"early career\" OR \"diversity recruiting\")\n AND (\"Chicago\""
    "   - Keep the query under 60 characters after the site:linkedin.com/in/ prefix.\n"
    "The VERY FIRST line of your entire response must be the search query line, starting exactly with "
    "'XRAY_QUERY: ' followed by your optimized search query string, and nothing before it.\n"
    "After that first line, provide the email drafts as the rest of your response.\n\n"
    "REACH Pathways ICP CONTEXT (for improved accuracy):\n"
    f"Exact titles: {', '.join(ICP_EXACT_TITLES)}\n"
    f"Pain points: {ICP_PAIN_POINTS}\n"
    f"Value proposition: {ICP_VALUE_PROP}\n"
)

prompt_template = ChatPromptTemplate.from_messages([
    ("system", SYSTEM_PROMPT),
    ("human", "Generate the custom B2B outreach copy and operational prospecting list based on these inputs.")
])


def generate_outreach(inputs: dict) -> dict:
    """
    Run the LLM and parse XRAY_QUERY + email text.
    Returns {"xray_query": str, "email_text": str, "raw_text": str}
    """
    llm = _build_llm()
    chain = prompt_template | llm
    response = chain.invoke(inputs)
    increment_usage("openrouter", 1)
    raw_text = response.content

    query_match = re.search(
        r'^\s*XRAY_QUERY\s*[:*#\s]+(.*)$', raw_text, re.IGNORECASE | re.MULTILINE
    )

    if query_match:
        xray_query = query_match.group(1).strip().replace("**", "").replace("`", "")
        email_text = raw_text[query_match.end():].strip()
        email_text = email_text.lstrip("#*-\\ \n")
    else:
        xray_query = ""
        email_text = raw_text.strip()

    return {
        "xray_query": xray_query,
        "email_text": email_text,
        "raw_text": raw_text,
    }


def fetch_live_leads(query_string: str, start_offset: int = 0):
    """
    Run a SerpAPI Google search for the given X-Ray query.
    Returns (leads, error_string). error_string is None on success.
    """
    if not query_string:
        return [], "Missing search query."

    if not SERPAPI_API_KEY:
        return [], "SERPAPI_API_KEY is not set in .env."

    # Guarantee the search is scoped to individual LinkedIn profiles.
    if "site:linkedin.com/in" not in query_string.lower():
        query_string = f"site:linkedin.com/in/ {query_string}"

    url = "https://serpapi.com/search"
    params = {
        "engine": "google",
        "q": query_string,
        "api_key": SERPAPI_API_KEY,
        "num": 10,
        "start": start_offset,
    }

    try:
        with requests.Session() as session:
            response = session.get(url, params=params, timeout=15)

        if response.status_code != 200:
            return [], f"SerpAPI error {response.status_code}: {response.text[:200]}"

        if response.status_code == 200:
            increment_usage("serpapi", 1)

        data = response.json()
        if "error" in data:
            return [], f"SerpAPI error: {data['error']}"

        results = data.get("organic_results", [])
        if not results:
            return [], "SerpAPI returned no organic results for this query. Try simplifying the target profile or location."

        leads = []
        for item in results:
            raw_title = item.get("title", "")
            clean_headline = raw_title.replace("- LinkedIn", "").replace("| LinkedIn", "").strip()
            standardized = clean_headline.replace("—", "-").replace("–", "-").replace("|", "-")

            if " - " in standardized:
                parts = standardized.split(" - ", 1)
                name = parts[0].strip()
                role = parts[1].strip()
            elif "-" in standardized:
                parts = standardized.split("-", 1)
                name = parts[0].strip()
                role = parts[1].strip()
            else:
                name = clean_headline
                role = "Click link to view role details"

            leads.append({
                "name": name,
                "role": role,
                "linkedin_url": item.get("link", "N/A"),
                "snippet": item.get("snippet", "N/A"),
            })

        return leads, None

    except requests.exceptions.Timeout:
        return [], "SerpAPI timed out after 15 seconds."
    except Exception as e:
        return [], f"Unexpected error: {e}"