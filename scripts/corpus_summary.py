"""Сводка по корпусу: распределение по классам, уровни, валидность PoB-xml."""
import json
from collections import Counter
from pathlib import Path

CORPUS = Path(__file__).resolve().parents[1] / "corpus"
metas = sorted(CORPUS.glob("*.meta.json"))
print(f"total builds: {len(metas)}")

classes = Counter()
ascendancies = Counter()
levels = []
empty_pob = 0
empty_skills = 0
for m in metas:
    try:
        d = json.loads(m.read_text(encoding="utf-8"))
    except Exception:
        continue
    classes[d.get("class") or "?"] += 1
    ascendancies[d.get("ascendancy") or "?"] += 1
    if d.get("level"):
        levels.append(d["level"])
    if not d.get("allGemNames"):
        empty_skills += 1
    # проверить pob-xml рядом
    xml_path = m.with_suffix(".pob.xml")
    if xml_path.exists() and xml_path.stat().st_size < 500:
        empty_pob += 1

print(f"\n=== class distribution ===")
for c, n in classes.most_common():
    print(f"  {n:4d}  {c}")
print(f"\n=== ascendancy distribution (top 15) ===")
for a, n in ascendancies.most_common(15):
    print(f"  {n:4d}  {a}")
print(f"\nlevels: min={min(levels)} max={max(levels)} (all lvl-100? {len(set(levels))==1})")
print(f"empty skills: {empty_skills}/{len(metas)}")
print(f"suspiciously small pob-xml (<500b): {empty_pob}")
