"""Инспектирует ответ /poe1/api/builds/1/character — какие поля, есть ли PoB-code."""
import json, sys

with open("char_api.json", "r", encoding="utf-8-sig") as f:
    data = json.load(f)

print("=== top-level keys ===")
for k in sorted(data.keys()):
    v = data[k]
    t = type(v).__name__
    if isinstance(v, (list, dict)):
        print(f"  {k}: {t}[{len(v)}]")
    else:
        s = str(v)
        print(f"  {k}: {t} = {s[:80]}")

# ищем PoB-code / importcode / pastebin-подобные поля
print("\n=== pob/code-ish keys (recursive) ===")
def walk(o, path=""):
    if isinstance(o, dict):
        for k, v in o.items():
            kl = str(k).lower()
            if any(x in kl for x in ("code", "pob", "pastebin", "import", "buildlink")):
                print(f"  {path}/{k} = {str(v)[:120]}")
            walk(v, f"{path}/{k}")
    elif isinstance(o, list):
        for i, v in enumerate(o[:3]):
            walk(v, f"{path}[{i}]")
walk(data)
