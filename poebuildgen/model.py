"""Внутренняя build-model (pydantic) поверх PoB-XML.

Назначение: дать генератору типобезопасную структуру для чтения/мутации билда
вместо работы с сырыми строками, не теряя при этом верность PoB.

Принцип losslessness: типизируем только то, что генератор реально мутирует —
Build (мета), Tree/Spec (узлы, мастери, класс), Skills/SkillSet/Skill/Gem.
Всё прочее (Import, Party, Notes, Calcs, TreeView, Items, Config, незнакомые
теги) сохраняется ВЕРБАТИМ как сырой XML и переставляется обратно в исходном
порядке. Все атрибуты хранятся как сырые строки (dict[str,str]); типизированный
доступ — через свойства, чтобы не терять формат PoB ("true"/"false"/"nil" и т.п.).

Контракт (см. tests): from_xml(xml).to_xml() даёт XML, который PoB пересчитывает
идентично исходному (статы совпадают). Байтовая идентичность — забота pobcode,
не модели.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from typing import ClassVar

from pydantic import BaseModel, ConfigDict, Field

_MASTERY_RE = re.compile(r"\{(\d+),(\d+)\}")

# верхнеуровневые теги, которые модель типизирует (остальное — lossless-passthrough)
_TYPED_TOP = {"Build", "Tree", "Skills"}


def _b2s(v: bool) -> str:
    return "true" if v else "false"


class _El(BaseModel):
    """Базовый узел: имя тега известно подклассу, атрибуты — сырые строки."""

    model_config = ConfigDict(extra="forbid")
    TAG: ClassVar[str] = ""

    attrib: dict[str, str] = Field(default_factory=dict)
    # Дочерние узлы, которые модель не типизирует (напр. Spec/URL/Sockets/Overrides),
    # сохраняются вербатим и переэмитятся как есть — без потери статов.
    raw_children: list[str] = Field(default_factory=list)

    # --- generic attribute helpers ---
    def get(self, key: str, default: str | None = None) -> str | None:
        return self.attrib.get(key, default)

    def set(self, key: str, value: str | None) -> None:
        if value is None:
            self.attrib.pop(key, None)
        else:
            self.attrib[key] = value

    def _int(self, key: str, default: int = 0) -> int:
        raw = self.attrib.get(key)
        try:
            return int(raw) if raw is not None else default
        except ValueError:
            return default

    def _bool(self, key: str, default: bool = False) -> bool:
        raw = self.attrib.get(key)
        if raw is None:
            return default
        return raw == "true"

    def _to_element(self) -> ET.Element:
        el = ET.Element(self.TAG, {k: str(v) for k, v in self.attrib.items()})
        for child in self._children():
            el.append(child._to_element())
        for raw in self.raw_children:
            el.append(ET.fromstring(raw))
        return el

    def _children(self) -> list["_El"]:
        return []


class Gem(_El):
    TAG: ClassVar[str] = "Gem"

    @property
    def name(self) -> str:
        return self.attrib.get("nameSpec", "")

    @name.setter
    def name(self, v: str) -> None:
        self.attrib["nameSpec"] = v

    @property
    def skill_id(self) -> str | None:
        return self.attrib.get("skillId")

    @property
    def gem_id(self) -> str | None:
        return self.attrib.get("gemId")

    @property
    def level(self) -> int:
        return self._int("level", 1)

    @level.setter
    def level(self, v: int) -> None:
        self.attrib["level"] = str(v)

    @property
    def quality(self) -> int:
        return self._int("quality", 0)

    @quality.setter
    def quality(self, v: int) -> None:
        self.attrib["quality"] = str(v)

    @property
    def enabled(self) -> bool:
        return self._bool("enabled", True)

    @enabled.setter
    def enabled(self, v: bool) -> None:
        self.attrib["enabled"] = _b2s(v)


class SkillGroup(_El):
    TAG: ClassVar[str] = "Skill"

    gems: list[Gem] = Field(default_factory=list)

    def _children(self) -> list[_El]:
        return list(self.gems)

    @property
    def slot(self) -> str | None:
        return self.attrib.get("slot")

    @property
    def enabled(self) -> bool:
        return self._bool("enabled", True)

    @enabled.setter
    def enabled(self, v: bool) -> None:
        self.attrib["enabled"] = _b2s(v)

    @property
    def is_main(self) -> bool:
        return self._bool("mainActiveSkill", False) or self.attrib.get("mainActiveSkill") == "1"


class SkillSet(_El):
    TAG: ClassVar[str] = "SkillSet"

    groups: list[SkillGroup] = Field(default_factory=list)

    def _children(self) -> list[_El]:
        return list(self.groups)

    @property
    def id(self) -> int:
        return self._int("id", 1)


class Skills(_El):
    TAG: ClassVar[str] = "Skills"

    sets: list[SkillSet] = Field(default_factory=list)

    def _children(self) -> list[_El]:
        return list(self.sets)

    @property
    def active_set_id(self) -> int:
        return self._int("activeSkillSet", 1)

    def active_set(self) -> SkillSet | None:
        wanted = self.active_set_id
        for s in self.sets:
            if s.id == wanted:
                return s
        return self.sets[0] if self.sets else None

    def all_gems(self) -> list[Gem]:
        return [g for s in self.sets for grp in s.groups for g in grp.gems]


class Spec(_El):
    TAG: ClassVar[str] = "Spec"

    @property
    def class_id(self) -> int:
        return self._int("classId", 0)

    @property
    def ascend_class_id(self) -> int:
        return self._int("ascendClassId", 0)

    @property
    def tree_version(self) -> str | None:
        return self.attrib.get("treeVersion")

    @property
    def nodes(self) -> list[int]:
        raw = self.attrib.get("nodes", "")
        out = []
        for part in raw.split(","):
            part = part.strip()
            if part:
                try:
                    out.append(int(part))
                except ValueError:
                    pass
        return out

    @nodes.setter
    def nodes(self, ids) -> None:
        self.attrib["nodes"] = ",".join(str(int(i)) for i in ids)

    @property
    def mastery_effects(self) -> dict[int, int]:
        raw = self.attrib.get("masteryEffects", "")
        out: dict[int, int] = {}
        for node, effect in _MASTERY_RE.findall(raw):
            out[int(node)] = int(effect)
        return out

    @mastery_effects.setter
    def mastery_effects(self, mapping: dict[int, int]) -> None:
        self.attrib["masteryEffects"] = ",".join(
            f"{{{int(n)},{int(e)}}}" for n, e in mapping.items()
        )


class Tree(_El):
    TAG: ClassVar[str] = "Tree"

    specs: list[Spec] = Field(default_factory=list)

    def _children(self) -> list[_El]:
        return list(self.specs)

    @property
    def active_spec_idx(self) -> int:
        return self._int("activeSpec", 1)

    def active_spec(self) -> Spec | None:
        idx = self.active_spec_idx
        if 1 <= idx <= len(self.specs):
            return self.specs[idx - 1]
        return self.specs[0] if self.specs else None


class BuildMeta(_El):
    TAG: ClassVar[str] = "Build"

    @property
    def level(self) -> int:
        return self._int("level", 1)

    @level.setter
    def level(self, v: int) -> None:
        self.attrib["level"] = str(v)

    @property
    def class_name(self) -> str:
        return self.attrib.get("className", "")

    @property
    def ascend_class_name(self) -> str:
        return self.attrib.get("ascendClassName", "")

    @ascend_class_name.setter
    def ascend_class_name(self, v: str) -> None:
        self.attrib["ascendClassName"] = v

    @property
    def main_socket_group(self) -> int:
        return self._int("mainSocketGroup", 1)


class PobBuild(BaseModel):
    """Корень: типизированные Build/Tree/Skills + lossless passthrough остального."""

    model_config = ConfigDict(extra="forbid")

    build: BuildMeta
    tree: Tree
    skills: Skills
    # layout — раскладка верхнеуровневых детей в исходном порядке: каждый элемент это
    # либо "Build"/"Tree"/"Skills" (типизированная секция), либо "" (плейсхолдер сырого
    # ребёнка). passthrough — сырые XML сырых детей в том же порядке. Такая схема корректна
    # при любых дублях тегов (в отличие от dict[tag] с #N-ключами).
    passthrough: list[str] = Field(default_factory=list)
    layout: list[str] = Field(default_factory=list)

    # --- разбор ---
    @classmethod
    def from_xml(cls, xml: str | bytes) -> "PobBuild":
        text = xml.decode("utf-8") if isinstance(xml, (bytes, bytearray)) else xml
        root = ET.fromstring(text)
        if root.tag != "PathOfBuilding":
            raise ValueError(f"ожидался <PathOfBuilding>, получено <{root.tag}>")

        build = tree = skills = None
        passthrough: list[str] = []
        layout: list[str] = []
        seen: set[str] = set()
        for child in root:
            # типизируем только ПЕРВОЕ вхождение Build/Tree/Skills; всё остальное — сырое
            if child.tag in _TYPED_TOP and child.tag not in seen:
                seen.add(child.tag)
                layout.append(child.tag)
                if child.tag == "Build":
                    build = BuildMeta(attrib=dict(child.attrib), raw_children=_raw_others(child))
                elif child.tag == "Tree":
                    tree = _parse_tree(child)
                else:
                    skills = _parse_skills(child)
            else:
                layout.append("")
                passthrough.append(ET.tostring(child, encoding="unicode"))

        if build is None or tree is None or skills is None:
            missing = [n for n, v in (("Build", build), ("Tree", tree), ("Skills", skills)) if v is None]
            raise ValueError(f"в билде отсутствуют обязательные секции: {missing}")

        return cls(build=build, tree=tree, skills=skills, passthrough=passthrough, layout=layout)

    @classmethod
    def from_code(cls, code: str) -> "PobBuild":
        from poebuildgen import pobcode

        return cls.from_xml(pobcode.decode(code))

    # --- сборка ---
    def to_xml(self) -> str:
        root = ET.Element("PathOfBuilding")
        raw_idx = 0
        for slot in self.layout:
            if slot == "Build":
                root.append(self.build._to_element())
            elif slot == "Tree":
                root.append(self.tree._to_element())
            elif slot == "Skills":
                root.append(self.skills._to_element())
            elif raw_idx < len(self.passthrough):
                root.append(ET.fromstring(self.passthrough[raw_idx]))
                raw_idx += 1
        return ET.tostring(root, encoding="unicode")

    def to_code(self) -> str:
        from poebuildgen import pobcode

        return pobcode.encode(self.to_xml().encode("utf-8"))


def _raw_others(el: ET.Element, *known: str) -> list[str]:
    """Сериализованные дочерние элементы, чьи теги модель не типизирует."""
    return [ET.tostring(c, encoding="unicode") for c in el if c.tag not in known]


def _parse_skills(el: ET.Element) -> Skills:
    skills = Skills(attrib=dict(el.attrib), raw_children=_raw_others(el, "SkillSet"))
    for sset_el in el.findall("SkillSet"):
        sset = SkillSet(attrib=dict(sset_el.attrib), raw_children=_raw_others(sset_el, "Skill"))
        for sk_el in sset_el.findall("Skill"):
            grp = SkillGroup(attrib=dict(sk_el.attrib), raw_children=_raw_others(sk_el, "Gem"))
            for gem_el in sk_el.findall("Gem"):
                grp.gems.append(Gem(attrib=dict(gem_el.attrib), raw_children=_raw_others(gem_el)))
            sset.groups.append(grp)
        skills.sets.append(sset)
    return skills


def _parse_tree(el: ET.Element) -> Tree:
    tree = Tree(attrib=dict(el.attrib), raw_children=_raw_others(el, "Spec"))
    for spec_el in el.findall("Spec"):
        tree.specs.append(Spec(attrib=dict(spec_el.attrib), raw_children=_raw_others(spec_el)))
    return tree
