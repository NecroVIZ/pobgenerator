"""Проверяет /dictionary формат и ищет proto-схему."""
import requests

DICT_URL = "https://poe.ninja/poe1/api/builds/dictionary"
# возможно нужен version в пути
for u in [DICT_URL, "https://poe.ninja/poe1/api/builds/1/dictionary"]:
    r = requests.get(u, headers={"User-Agent": "poebuildgen/0.1"}, timeout=15)
    print(f"{u}: status={r.status_code} len={len(r.content)} ct={r.headers.get('Content-Type')}")
    if r.status_code == 200:
        b = r.content
        print(f"  magic: {b[:8].hex()}")
        # try json
        try:
            d = r.json()
            print(f"  JSON keys: {list(d.keys())[:10] if isinstance(d, dict) else type(d)}")
        except Exception:
            print(f"  not JSON; first 120 chars: {b[:120]!r}")

# есть ли proto-схема в чанках? (grep по проекту ниже)
