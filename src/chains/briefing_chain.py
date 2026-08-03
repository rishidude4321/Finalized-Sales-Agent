"""
Structured extraction chain – pure JSON output, no external context.
Used for follow‑ups (supplementary) and passive updates.
"""

import json
import re
from typing import List
from pydantic import BaseModel, Field, ValidationError
from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import chain as runnable_chain
from src.services.model_selector import get_best_model
from src.utils.config import OPENROUTER_API_KEY, OPENROUTER_BASE_URL

class FollowUpItem(BaseModel):
    action: str
    reasoning: str
    contact: str

class UpdateItem(BaseModel):
    name: str
    email: str
    new_title: str
    evidence: str

class BriefingInsights(BaseModel):
    follow_ups: List[FollowUpItem] = Field(default_factory=list)
    potential_updates: List[UpdateItem] = Field(default_factory=list)

EXTRACTION_SYSTEM_PROMPT = """You are an assistant that extracts structured information from a sales professional's data.

You are given:
- Recent email conversations (including unread emails).
- Full bodies of unread emails.

Your response must be a valid JSON object with exactly these keys:

{{
  "follow_ups": [
    {{
      "action": "string",
      "reasoning": "string",
      "contact": "string"
    }}
  ],
  "potential_updates": [
    {{
      "name": "string",
      "email": "string",
      "new_title": "string",
      "evidence": "string"
    }}
  ]
}}

Rules:
- For follow_ups: only include items that require deeper reasoning, e.g., long unresponsive threads, approaching deadlines, or subtle asks. Do NOT include obvious questions or meeting requests – those are already handled.
- For potential_updates: scan emails for phrases like "promoted to", "new role", "title change", "excited to share that I've joined". Extract the person's display name, email, the exact new title, and a short evidence snippet.
- Use empty lists [] if nothing applies.
- Return ONLY the JSON object, no markdown, no code fences.
"""

EXTRACTION_PROMPT = ChatPromptTemplate.from_messages([
    ("system", EXTRACTION_SYSTEM_PROMPT),
    ("human", "Conversations:\n{conversations}\n\nUnread emails:\n{emails}")
])

def parse_json_from_response(text: str) -> BriefingInsights:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?", "", text)
        text = re.sub(r"```$", "", text)
    try:
        data = json.loads(text)
        return BriefingInsights(**data)
    except (json.JSONDecodeError, ValidationError) as e:
        print(f"⚠️ LLM JSON parse failed: {e}")
        return BriefingInsights()

def build_insights_chain():
    model_id = get_best_model()
    llm = ChatOpenAI(
        model=model_id,
        openai_api_key=OPENROUTER_API_KEY,
        openai_api_base=OPENROUTER_BASE_URL,
        temperature=0,
        max_tokens=1000,
    )
    raw_chain = EXTRACTION_PROMPT | llm

    @runnable_chain
    def parse_chain(inputs):
        response = raw_chain.invoke(inputs)
        return parse_json_from_response(response.content)

    return parse_chain