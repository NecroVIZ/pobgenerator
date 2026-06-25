"""Проверка пригодности билда как скелета спайка B: headless-DPS == вшитый GUI-DPS,
плюс перечень экипированных слотов/предметов (кандидаты на ре-оптимизацию шмота)."""

import sys
import xml.etree.ElementTree as ET
from pathlib import Path

from poebuildgen import pobcode
from poebuildgen.evaluator import evaluate


def embedded(root, name):
    for ps in root.iter("PlayerStat"):
        if ps.get("stat") == name:
            try:
                return float(ps.get("value"))
            except (TypeError, ValueError):
                return None
    return None


def main():
    path = Path(sys.argv[1])
    xml = pobcode.decode(path.read_text(encoding="utf-8").strip()).decode("utf-8")
    root = ET.fromstring(xml)

    keys = ["TotalDPS", "FullDPS", "CombinedDPS", "Life", "EnergyShield", "TotalEHP"]
    emb = {k: embedded(root, k) for k in keys}
    res = evaluate(xml, keys, name=f"inspect-{path.stem}")
    hl = res["stats"]

    print(f"=== {path.name} (PoB {res.get('version')}) ===")
    for k in keys:
        e, h = emb.get(k), hl.get(k)
        if e is None and h is None:
            continue
        rel = abs((e or 0) - (h or 0)) / max(abs(e or 0), abs(h or 0), 1e-9)
        flag = "OK" if rel < 0.01 else f"DIFF {rel*100:.2f}%"
        print(f"  {k:<12} embedded={e} headless={h} [{flag}]")

    items = root.find("Items")
    by_id = {it.get("id"): it for it in items.findall("Item")}
    print("  -- equipped slots --")
    for slot in items.findall("Slot"):
        iid = slot.get("itemId")
        it = by_id.get(iid)
        first = (it.text or "").strip().splitlines() if it is not None else []
        name = " / ".join(first[:2]) if first else "?"
        print(f"    {slot.get('name'):<16} -> {name}")


if __name__ == "__main__":
    main()
