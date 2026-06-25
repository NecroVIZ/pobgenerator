"""Попытка generic-декода /search protobuf. Protobuf wire format допускает чтение без .proto:
length-delimited fields могут содержать UTF-8 строки. Вытащим все printable-строки и поищем
account/name-подобные паттерны.

Если работает — получаем список имён напрямую, без playwright."""
import re
import requests

URL = "https://poe.ninja/poe1/api/builds/1/search?overview=mirage&type=exp"
r = requests.get(URL, headers={"User-Agent": "poebuildgen-corpus/0.1"}, timeout=20)
data = r.content
print(f"len={len(data)} ct={r.headers.get('Content-Type')}")

# generic protobuf string extraction: находим все length-delimited UTF-8 строки >=3 символов
# (protobuf field: tag + length + bytes; length-prefixed bytes часто валидный UTF-8)
strings = re.findall(rb'[\x20-\x7e]{3,}', data)
decoded = [s.decode("ascii", errors="ignore") for s in strings]

# фильтруем «словарные» ключи (class, ascendancy и т.п.) — они повторяются
from collections import Counter
c = Counter(decoded)
print(f"\ntotal unique strings: {len(c)}")
print("--- top-20 most frequent (likely dictionary keys / repeated values) ---")
for s, n in c.most_common(20):
    print(f"  {n:5d}  {s[:60]!r}")

# candidate account/name-подобные: строки, похожие на имена аккаунтов (alnum, _, -) и char-names
print("\n--- candidate character/account-like strings (long, mixed-case) ---")
cand = []
for s in c:
    # account pattern: lowercase-with-digits-or-hyphen; char-name: CamelCase/Alnum
    if 4 <= len(s) <= 24 and re.fullmatch(r'[A-Za-z][A-Za-z0-9_\-]+', s):
        # отбрасываем явно-словарные
        if s.lower() not in ("class", "ascendancy", "secondascendancy", "weaponmode",
                              "mirage", "exp", "depthsolo", "streamer", "level", "character"):
            cand.append(s)
print(f"candidates: {len(cand)}")
for s in cand[:40]:
    print(f"  {s!r}")
