"""Сколько редких (affix-driven) core-слотов в каждом билде — кандидаты на ре-оптимизацию CP-SAT."""

import xml.etree.ElementTree as ET
from pathlib import Path

from poebuildgen import pobcode

CORE = ["Weapon 1", "Weapon 2", "Helmet", "Body Armour", "Gloves",
        "Boots", "Belt", "Amulet", "Ring 1", "Ring 2"]


def main():
    for f in sorted(Path("builds").glob("*.txt"), key=lambda p: int(p.stem)):
        xml = pobcode.decode(f.read_text().strip()).decode("utf-8")
        root = ET.fromstring(xml)
        items = root.find("Items")
        by = {it.get("id"): it for it in items.findall("Item")}
        iset = [c for c in items if c.tag == "ItemSet"][0]
        rare = uniq = 0
        rares = []
        for s in iset.findall("Slot"):
            if s.get("name") in CORE and s.get("itemId", "0") != "0":
                it = by.get(s.get("itemId"))
                head = (it.text or "").strip().splitlines()
                rar = head[0].replace("Rarity: ", "") if head else "?"
                if rar == "RARE":
                    rare += 1
                    rares.append(s.get("name"))
                elif rar == "UNIQUE":
                    uniq += 1
        b = root.find("Build")
        asc = b.get("ascendClassName")
        print(f"{f.name:>7} {asc:<13} rare={rare} uniq={uniq}  rares={rares}")


if __name__ == "__main__":
    main()
