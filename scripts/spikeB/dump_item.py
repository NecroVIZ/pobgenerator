"""Печать сырого текста одного редкого предмета билда с номерами строк —
чтобы зафиксировать формат (где Implicits/эксплициты) для срезки и инжекта модов."""

import sys
import xml.etree.ElementTree as ET
from pathlib import Path

from poebuildgen import pobcode

path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("builds/10.txt")
slot_want = sys.argv[2] if len(sys.argv) > 2 else "Ring 1"

xml = pobcode.decode(path.read_text().strip()).decode("utf-8")
root = ET.fromstring(xml)
items = root.find("Items")
by = {it.get("id"): it for it in items.findall("Item")}
iset = [c for c in items if c.tag == "ItemSet"][0]
for s in iset.findall("Slot"):
    if s.get("name") == slot_want and s.get("itemId", "0") != "0":
        it = by.get(s.get("itemId"))
        print(f"# slot={slot_want} itemId={s.get('itemId')}")
        for i, ln in enumerate((it.text or "").splitlines()):
            print(f"{i:>3}| {ln}")
        break
