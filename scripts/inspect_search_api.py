"""Инспектирует /poe1/api/builds/1/search — что за формат (NDJSON? один объект?)."""
import requests
import gzip

URL = "https://poe.ninja/poe1/api/builds/1/search?overview=mirage&type=exp"
r = requests.get(URL, headers={"User-Agent": "poebuildgen-corpus-recon/0.1"}, timeout=20)
print("status:", r.status_code, "len:", len(r.content), "enc:", r.headers.get("Content-Encoding"))
# ручная декомпрессия (r.content уже разжат requests'ом; но проверим сырец)
print("first 4 bytes of r.text:", repr(r.text[:4]))
print("r.text length:", len(r.text))
print("--- first 500 chars ---")
print(r.text[:500])
print("--- last 200 chars ---")
print(r.text[-200:])
print("--- line count ---")
print(r.text.count("\n"), "newlines")
