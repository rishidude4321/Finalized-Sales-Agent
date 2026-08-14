"""
Pluggable company enrichment.
Default provider: Serper.dev (free Google Search API).
Future provider: Clearbit.
Results are cached for 30 days to avoid rate limits.
"""

import json
import time
from pathlib import Path
from typing import Optional, Dict
import requests
from src.utils.config import SERPER_API_KEY
from src.utils.agent_state import increment_usage


class SerperProvider:
    """Enrich company info using Serper.dev search API."""

    BASE_URL = "https://google.serper.dev/search"

    def __init__(self, api_key: str):
        self.api_key = api_key

    def lookup_company(self, domain: str) -> Optional[Dict]:
        """Search for the company domain and return a summary dict."""
        if not self.api_key:
            return None
        headers = {"X-API-KEY": self.api_key, "Content-Type": "application/json"}
        payload = {"q": f"{domain} company description industry"}
        try:
            resp = requests.post(self.BASE_URL, json=payload, headers=headers, timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                # Extract the first organic snippet as a summary
                organic = data.get("organic", [])
                if organic:
                    snippet = organic[0].get("snippet", "")
                    title = organic[0].get("title", "")
                    return {
                        "name": title.split(" – ")[0].strip() if " – " in title else title,
                        "description": snippet,
                        "source": "serper.dev",
                    }
            else:
                print(f"Serper error {resp.status_code}: {resp.text}")
        except Exception as e:
            print(f"Serper request failed: {e}")
        return None


class CompanyEnricher:
    """Wraps enrichment providers and caches results."""

    CACHE_FILE = "company_cache.json"
    CACHE_EXPIRY_DAYS = 30

    def __init__(self):
        self.provider = SerperProvider(SERPER_API_KEY)
        self._cache = self._load_cache()

    def _load_cache(self) -> Dict[str, Dict]:
        path = Path(self.CACHE_FILE)
        if path.exists():
            try:
                return json.loads(path.read_text())
            except:
                pass
        return {}

    def _save_cache(self):
        with open(self.CACHE_FILE, "w") as f:
            json.dump(self._cache, f, indent=2)

    def enrich_company(self, domain: str) -> Optional[Dict]:
        """Return enriched data for a domain, using cache if available."""
        domain = domain.lower().strip()
        # Check cache
        if domain in self._cache:
            entry = self._cache[domain]
            if time.time() - entry.get("timestamp", 0) < self.CACHE_EXPIRY_DAYS * 86400:
                return entry.get("data")

        # Fetch from provider
        data = self.provider.lookup_company(domain)
        if data:
            increment_usage("serper", 1)
            self._cache[domain] = {"timestamp": time.time(), "data": data}
            self._save_cache()
        return data