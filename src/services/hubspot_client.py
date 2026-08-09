"""
HubSpot API client for the Sales Support Agent.
Handles reading deals and contacts for the daily briefing.
Pipeline stages are fetched dynamically and cached for 7 days.
"""

import json
import time
from pathlib import Path
from typing import List, Dict, Optional
import requests
from src.utils.config import HUBSPOT_ACCESS_TOKEN


class HubSpotClient:
    """Wraps HubSpot API calls with a Service Key access token."""

    BASE_URL = "https://api.hubapi.com"
    PIPELINE_CACHE_FILE = "pipeline_cache.json"
    CACHE_EXPIRY_SECONDS = 7 * 86400  # 7 days

    def __init__(self):
        self.token = HUBSPOT_ACCESS_TOKEN
        if not self.token:
            raise RuntimeError("HUBSPOT_ACCESS_TOKEN not set in .env file.")

    def _get_headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
        }

    def _make_request(self, method: str, endpoint: str, **kwargs) -> Optional[Dict]:
        """Internal helper for authenticated requests to HubSpot."""
        url = f"{self.BASE_URL}{endpoint}"
        headers = self._get_headers()
        try:
            response = requests.request(method, url, headers=headers, **kwargs)
            if response.ok:
                return response.json()
            else:
                print(f"HubSpot API error {response.status_code}: {response.text}")
                return None
        except Exception as e:
            print(f"HubSpot API request failed: {e}")
            return None

    def _load_pipeline_cache(self) -> Optional[Dict[str, str]]:
        """Load cached pipeline stages if not expired."""
        cache_path = Path(self.PIPELINE_CACHE_FILE)
        if cache_path.exists():
            try:
                cache = json.loads(cache_path.read_text())
                if time.time() - cache.get("timestamp", 0) < self.CACHE_EXPIRY_SECONDS:
                    return cache.get("stages", {})
            except Exception:
                pass
        return None

    def _save_pipeline_cache(self, stages: Dict[str, str]) -> None:
        """Save pipeline stages to a local cache file."""
        cache_path = Path(self.PIPELINE_CACHE_FILE)
        cache_path.write_text(json.dumps({
            "stages": stages,
            "timestamp": time.time(),
        }))

    def _fetch_pipeline_stages(self) -> Dict[str, str]:
        """
        Fetch all deal pipeline stages from HubSpot and return a mapping of
        stage_id -> stage_label. Results are cached for 7 days.
        """
        # Try cache first
        cached = self._load_pipeline_cache()
        if cached:
            return cached

        # Fetch from API
        endpoint = "/crm/v3/pipelines/deals"
        data = self._make_request("GET", endpoint)
        if not data:
            return {}

        stages = {}
        for pipeline in data.get("results", []):
            for stage in pipeline.get("stages", []):
                stage_id = stage.get("id", "")
                stage_label = stage.get("label", "")
                if stage_id and stage_label:
                    stages[stage_id] = stage_label

        # Save to cache
        if stages:
            self._save_pipeline_cache(stages)
            print(f"✅ Cached {len(stages)} deal stages from HubSpot pipelines.")

        return stages

    def get_deal_stage_name(self, stage_id: str) -> str:
        """Convert a stage ID to a human-readable name using the live pipeline data."""
        stages = self._fetch_pipeline_stages()
        return stages.get(stage_id, stage_id.replace("_", " ").title())

    def get_outstanding_deals(self, limit: int = 20) -> List[Dict]:
        """
        Retrieve deals that are NOT in closed stages, sorted by least recently modified first.
        """
        endpoint = "/crm/v3/objects/deals/search"
        payload = {
            "filterGroups": [
                {
                    "filters": [
                        {
                            "propertyName": "dealstage",
                            "operator": "NOT_IN",
                            "values": ["closedwon", "closedlost"],
                        }
                    ]
                }
            ],
            "sorts": [
                {
                    "propertyName": "hs_lastmodifieddate",
                    "direction": "ASCENDING",
                }
            ],
            "properties": [
                "dealname",
                "dealstage",
                "amount",
                "hs_lastmodifieddate",
            ],
            "limit": limit,
        }
        data = self._make_request("POST", endpoint, json=payload)
        if not data:
            return []

        deals = []
        for deal in data.get("results", []):
            props = deal.get("properties", {})
            deals.append({
                "id": deal.get("id"),
                "name": props.get("dealname", "Unnamed Deal"),
                "stage": props.get("dealstage", ""),
                "amount": props.get("amount", ""),
                "last_modified": props.get("hs_lastmodifieddate", ""),
            })
        return deals

    def get_recent_contacts(self, days: int = 7, limit: int = 20) -> List[Dict]:
        """
        Retrieve contacts modified in the last `days` days.
        """
        import datetime
        since = (
            datetime.datetime.utcnow() - datetime.timedelta(days=days)
        ).strftime("%Y-%m-%d")

        endpoint = "/crm/v3/objects/contacts/search"
        payload = {
            "filterGroups": [
                {
                    "filters": [
                        {
                            "propertyName": "lastmodifieddate",
                            "operator": "GTE",
                            "value": since,
                        }
                    ]
                }
            ],
            "sorts": [
                {
                    "propertyName": "lastmodifieddate",
                    "direction": "DESCENDING",
                }
            ],
            "properties": [
                "firstname",
                "lastname",
                "email",
                "company",
                "jobtitle",
            ],
            "limit": limit,
        }
        data = self._make_request("POST", endpoint, json=payload)
        if not data:
            return []

        contacts = []
        for contact in data.get("results", []):
            props = contact.get("properties", {})
            first = props.get("firstname", "")
            last = props.get("lastname", "")
            updated = contact.get("updatedAt", "")[:10]
            contacts.append({
                "id": contact.get("id"),
                "name": f"{first} {last}".strip(),
                "email": props.get("email", ""),
                "company": props.get("company", ""),
                "jobtitle": props.get("jobtitle", ""),
                "last_modified": updated,
            })
        return contacts

    def get_contacts_by_domain(self, domain: str) -> List[Dict]:
        """Retrieve all contacts whose email ends with the given domain."""
        endpoint = "/crm/v3/objects/contacts/search"
        payload = {
            "filterGroups": [
                {
                    "filters": [
                        {
                            "propertyName": "email",
                            "operator": "CONTAINS_TOKEN",
                            "value": f"@{domain}",
                        }
                    ]
                }
            ],
            "properties": ["firstname", "lastname", "email", "company", "jobtitle"],
            "limit": 50,
        }
        data = self._make_request("POST", endpoint, json=payload)
        if not data:
            return []
        contacts = []
        for c in data.get("results", []):
            p = c.get("properties", {})
            contacts.append({
                "id": c.get("id"),
                "name": f"{p.get('firstname','')} {p.get('lastname','')}".strip(),
                "email": p.get("email", ""),
                "company": p.get("company", ""),
                "jobtitle": p.get("jobtitle", ""),
            })
        return contacts

    def get_company_by_domain(self, domain: str) -> Optional[Dict]:
        """Find a HubSpot company by domain (uses company domain property)."""
        endpoint = "/crm/v3/objects/companies/search"
        payload = {
            "filterGroups": [
                {
                    "filters": [
                        {
                            "propertyName": "domain",
                            "operator": "EQ",
                            "value": domain,
                        }
                    ]
                }
            ],
            "properties": ["name", "domain", "description", "industry", "numberofemployees"],
            "limit": 1,
        }
        data = self._make_request("POST", endpoint, json=payload)
        if data and data.get("results"):
            company = data["results"][0]
            p = company.get("properties", {})
            return {
                "id": company.get("id"),
                "name": p.get("name", ""),
                "domain": p.get("domain", ""),
                "description": p.get("description", ""),
                "industry": p.get("industry", ""),
                "employees": p.get("numberofemployees", ""),
            }
        return None