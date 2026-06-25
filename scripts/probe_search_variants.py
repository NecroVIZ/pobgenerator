"""Проба /search с POST + JSON — возможно protobuf это только default, а JSON принимается."""
import requests

BASE = "https://poe.ninja/poe1/api/builds/1/search"

variants = [
    ("GET json-accept", {"method": "GET",
        "url": BASE + "?overview=mirage&type=exp",
        "headers": {"Accept": "application/json"}}),
    ("POST empty", {"method": "POST", "url": BASE + "?overview=mirage&type=exp",
        "headers": {"Accept": "application/json"}, "json": {}}),
    ("POST body", {"method": "POST", "url": BASE,
        "headers": {"Accept": "application/json", "Content-Type": "application/json"},
        "json": {"overview": "mirage", "type": "exp"}}),
]

for label, spec in variants:
    try:
        r = requests.request(spec["method"], spec["url"],
                             headers={**{"User-Agent": "poebuildgen/0.1"}, **spec.get("headers", {})},
                             json=spec.get("json"), timeout=15)
        ct = r.headers.get("Content-Type", "")
        print(f"{label}: {r.status_code} len={len(r.content)} ct={ct}")
        if "json" in ct.lower():
            try:
                d = r.json()
                if isinstance(d, dict):
                    print(f"  JSON keys: {list(d.keys())[:8]}")
                    for k in ("rows", "characters", "data", "items"):
                        if k in d and isinstance(d[k], list):
                            print(f"  {k}[0] (if dict): {list(d[k][0].keys())[:8] if d[k] and isinstance(d[k][0], dict) else d[k][:1]}")
                            break
            except Exception as e:
                print(f"  json parse err: {e}")
    except Exception as e:
        print(f"{label}: ERR {e}")
