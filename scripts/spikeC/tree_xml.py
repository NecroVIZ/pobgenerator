"""Правка дерева в PoB-XML для батч-оценок (спайк D14 marginals)."""

from __future__ import annotations

import copy
import re
import xml.etree.ElementTree as ET

_MASTERY_RE = re.compile(r"\{(\d+),(\d+)\}")


def parse_mastery_effects(spec: ET.Element) -> dict[int, int]:
    raw = spec.get("masteryEffects") or ""
    return {int(n): int(e) for n, e in _MASTERY_RE.findall(raw)}


def render_tree_nodes(xml: str, node_ids: set[str | int],
                      mastery_effects: dict[int, int] | None = None) -> str:
    """Вернуть XML с заменённым списком nodes активного Spec."""
    root = copy.deepcopy(ET.fromstring(xml))
    tree = root.find("Tree")
    if tree is None:
        return xml
    specs = tree.findall("Spec")
    if not specs:
        return xml
    active = int(tree.get("activeSpec") or 1)
    spec = specs[min(active - 1, len(specs) - 1)]
    ids = sorted(int(n) for n in node_ids)
    spec.set("nodes", ",".join(str(i) for i in ids))
    if mastery_effects is not None:
        spec.set("masteryEffects", ",".join(f"{{{int(n)},{int(e)}}}" for n, e in mastery_effects.items()))
    return ET.tostring(root, encoding="unicode")
