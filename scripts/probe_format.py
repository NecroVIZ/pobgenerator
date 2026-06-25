"""Определяет бинарный формат /search по magic bytes и Accept-header negotiations."""
import requests

URL = "https://poe.ninja/poe1/api/builds/1/search?overview=mirage&type=exp"

for accept in ["application/json", "application/x-protobuf", "application/octet-stream", "*/*"]:
    r = requests.get(URL, headers={"User-Agent": "poebuildgen/0.1", "Accept": accept}, timeout=20)
    b = r.content
    magic = b[:8].hex()
    print(f"Accept={accept!r}: status={r.status_code} len={len(b)} magic={magic} "
          f"ct={r.headers.get('Content-Type')}")
    # is it JSON?
    try:
        r.json()
        print("  -> parses as JSON!")
    except Exception:
        # maybe it's NDJSON/JSONL
        txt = b.decode("utf-8", errors="replace")
        first_line = txt.split("\n", 1)[0][:100]
        print(f"  first line: {first_line!r}")
