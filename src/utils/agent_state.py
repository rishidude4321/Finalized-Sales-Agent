import json
from pathlib import Path
from datetime import datetime, timezone

AGENT_STATUS_FILE = Path("agent_status.json")
USAGE_FILE = Path("logs/usage.json")

def load_json(path, default):
    if path.exists():
        try:
            return json.loads(path.read_text())
        except Exception:
            pass
    return default

def save_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f, indent=2, default=str)

def load_agent_status():
    return load_json(AGENT_STATUS_FILE, {})

def save_agent_status(data):
    save_json(AGENT_STATUS_FILE, data)

def load_usage():
    return load_json(USAGE_FILE, {})

def increment_usage(service, count=1):
    usage = load_usage()
    now = datetime.now(timezone.utc)
    month_key = now.strftime("%Y-%m")

    service_data = usage.setdefault(service, {
        "month": 0,
        "total": 0,
        "month_key": month_key,
    })

    if service_data.get("month_key") != month_key:
        service_data["month"] = 0
        service_data["month_key"] = month_key

    service_data["month"] += count
    service_data["total"] = service_data.get("total", 0) + count

    save_json(USAGE_FILE, usage)