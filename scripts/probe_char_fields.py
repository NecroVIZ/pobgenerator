"""Проверяем реальные поля skills + items + jewels в /character ответе."""
import json
import requests
from poebuildgen import pobcode  # noqa: F401  (проверка что модуль доступен)

URL = "https://poe.ninja/poe1/api/builds/1/character"
r = requests.get(URL, params={"account": "heygyus-0416", "name": "ResurrectSanest",
                              "overview": "mirage", "type": "exp"},
                 headers={"User-Agent": "poebuildgen-corpus/0.1"}, timeout=25)
d = r.json()

print("=== skills[0..2] (full structure) ===")
for i, s in enumerate((d.get("skills") or [])[:3]):
    print(f"--- skills[{i}] type={type(s).__name__} ---")
    print(json.dumps(s, ensure_ascii=False, indent=2)[:600])

print("\n=== items[0] (structure) ===")
items = d.get("items") or []
if items:
    print(json.dumps(items[0], ensure_ascii=False, indent=2)[:600])

print("\n=== jewels[0] (structure) ===")
jewels = d.get("jewels") or []
if jewels:
    print(json.dumps(jewels[0], ensure_ascii=False, indent=2)[:500])
