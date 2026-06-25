"""Харнесс спайка B: загрузка билда, модель предмета, срез эксплицитов, сборка XML.

Скелет билда фиксируется (дерево/гемы/конфиг/уникалки). Оптимизируем только эксплициты
выбранных РЕДКИХ слотов: базу и ВСЕ имплициты сохраняем (они часть базы/крафта),
эксплициты срезаем и затем заполняем выбором CP-SAT.

PoB не валидирует легальность аффиксов импортируемого текста — применяет как есть.
Поэтому caps/группы/eligibility обязан гарантировать солвер, а не PoB.
"""

from __future__ import annotations

import copy
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

from poebuildgen import pobcode

CORE = ["Weapon 1", "Weapon 2", "Helmet", "Body Armour", "Gloves",
        "Boots", "Belt", "Amulet", "Ring 1", "Ring 2"]

_AFFIX_TAG = re.compile(r"^\{(crafted|fractured|mutated|scourge|crucible|veiled)[^}]*\}")


@dataclass
class Item:
    raw: str                      # исходный текст <Item>
    item_id: str
    lines: list[str]              # все строки текста
    rarity: str
    name: str
    base: str                     # имя базы (для тегов/eligibility)
    implicit_count: int
    impl_idx: int                 # индекс строки "Implicits: N"
    explicit_start: int           # индекс первой эксплицит-строки
    explicits: list[str]          # эксплицит-строки (с тегами)
    item_level: int = 1
    prefix_cap: int = 3
    suffix_cap: int = 3

    @property
    def header_through_implicits(self) -> list[str]:
        return self.lines[: self.explicit_start]

    def with_explicits(self, new_explicits: list[str]) -> str:
        """Собрать текст <Item>: шапка+имплициты + переданные эксплициты."""
        body = self.header_through_implicits + list(new_explicits)
        # сохраняем завершающий перевод строки, как у PoB
        return "\n".join(body) + "\n"

    def stripped(self) -> str:
        return self.with_explicits([])


def parse_item(item_el: ET.Element) -> Item:
    raw = item_el.text or ""
    lines = raw.splitlines()
    # нормализуем: убираем ведущие табы/пробелы у служебных строк, но СОХРАНЯЕМ
    # содержимое модов как есть (PoB толерантен к ведущим табам в шапке)
    def clean(s: str) -> str:
        return s.strip()

    rarity = name = base = ""
    impl_idx = -1
    implicit_count = 0
    # найдём rarity, name, base и "Implicits: N"
    content_idx = [i for i, l in enumerate(lines) if clean(l)]
    # rarity
    for i in content_idx:
        if clean(lines[i]).startswith("Rarity:"):
            rarity = clean(lines[i]).split(":", 1)[1].strip()
            name = clean(lines[i + 1]) if i + 1 < len(lines) else ""
            base = clean(lines[i + 2]) if i + 2 < len(lines) else ""
            break
    for i, l in enumerate(lines):
        m = re.match(r"Implicits:\s*(\d+)", clean(l))
        if m:
            impl_idx = i
            implicit_count = int(m.group(1))
            break
    explicit_start = impl_idx + 1 + implicit_count if impl_idx >= 0 else len(lines)
    explicits = [clean(l) for l in lines[explicit_start:] if clean(l)]

    ilvl = 1
    for l in lines:
        m = re.match(r"Item Level:\s*(\d+)", clean(l))
        if m:
            ilvl = int(m.group(1))
            break

    pcap, scap = 3, 3
    for i in range(impl_idx + 1, explicit_start):
        t = clean(lines[i])
        m = re.match(r"([+-]\d+)\s+Prefix Modifiers? allowed", t)
        if m:
            pcap += int(m.group(1))
        m = re.match(r"([+-]\d+)\s+Suffix Modifiers? allowed", t)
        if m:
            scap += int(m.group(1))

    return Item(raw=raw, item_id=item_el.get("id"), lines=lines, rarity=rarity,
                name=name, base=base, implicit_count=implicit_count, impl_idx=impl_idx,
                explicit_start=explicit_start, explicits=explicits, item_level=ilvl,
                prefix_cap=max(0, pcap), suffix_cap=max(0, scap))


@dataclass
class Build:
    path: Path
    xml: str
    root: ET.Element
    items_el: ET.Element
    itemset_el: ET.Element
    by_id: dict[str, ET.Element]
    slot_to_id: dict[str, str] = field(default_factory=dict)

    @classmethod
    def load(cls, path: str | Path) -> "Build":
        path = Path(path)
        xml = pobcode.decode(path.read_text().strip()).decode("utf-8")
        root = ET.fromstring(xml)
        items_el = root.find("Items")
        by_id = {it.get("id"): it for it in items_el.findall("Item")}
        itemset = [c for c in items_el if c.tag == "ItemSet"][0]
        slot_to_id = {}
        for s in itemset.findall("Slot"):
            if s.get("itemId", "0") != "0":
                slot_to_id[s.get("name")] = s.get("itemId")
        return cls(path=path, xml=xml, root=root, items_el=items_el,
                   itemset_el=itemset, by_id=by_id, slot_to_id=slot_to_id)

    @classmethod
    def from_xml(cls, xml: str, path: str | Path = "inline") -> "Build":
        """Build из уже собранного XML (дерево+шмот менялись снаружи)."""
        path = Path(path)
        root = ET.fromstring(xml)
        items_el = root.find("Items")
        by_id = {it.get("id"): it for it in items_el.findall("Item")}
        itemset = [c for c in items_el if c.tag == "ItemSet"][0]
        slot_to_id = {}
        for s in itemset.findall("Slot"):
            if s.get("itemId", "0") != "0":
                slot_to_id[s.get("name")] = s.get("itemId")
        return cls(path=path, xml=xml, root=root, items_el=items_el,
                   itemset_el=itemset, by_id=by_id, slot_to_id=slot_to_id)

    def rare_core_slots(self) -> list[str]:
        out = []
        for slot in CORE:
            iid = self.slot_to_id.get(slot)
            if iid and (self.by_id[iid].text or "").find("Rarity: RARE") >= 0:
                out.append(slot)
        return out

    def item_for_slot(self, slot: str) -> Item:
        return parse_item(self.by_id[self.slot_to_id[slot]])

    def render(self, overrides: dict[str, str] | None = None) -> str:
        """XML билда с заменой текста указанных предметов (slot -> текст <Item>)."""
        root = copy.deepcopy(self.root)
        items_el = root.find("Items")
        by_id = {it.get("id"): it for it in items_el.findall("Item")}
        for slot, text in (overrides or {}).items():
            iid = self.slot_to_id[slot]
            by_id[iid].text = text
        return ET.tostring(root, encoding="unicode")


if __name__ == "__main__":
    import sys

    from scripts.spikeB.engine import Engine

    b = Build.load(sys.argv[1] if len(sys.argv) > 1 else "builds/10.txt")
    rares = b.rare_core_slots()
    print("rare core slots:", rares)
    for slot in rares:
        it = b.item_for_slot(slot)
        print(f"  {slot:<12} base={it.base!r} impl={it.implicit_count} "
              f"caps={it.prefix_cap}p/{it.suffix_cap}s explicits={len(it.explicits)}")

    eng = Engine()
    ref = eng.dps(b.xml)
    stripped = {slot: b.item_for_slot(slot).stripped() for slot in rares}
    base = eng.dps(b.render(stripped))
    print(f"\nreference (real gear) DPS = {ref:,.0f}")
    print(f"baseline (rares stripped) DPS = {base:,.0f}  ({base/ref*100:.1f}% of ref)")
    print(f"evals={eng.evals}")
