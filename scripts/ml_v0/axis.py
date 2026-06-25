"""Axis-descriptor layers A (+ optional C) for tree target-set ML-v0."""

from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

from poebuildgen.headless import PobHeadless
from scripts.spikeC.tree_build import load_tree_graph, main_tree_notables, split_main_ascend

_REPO = Path(__file__).resolve().parents[2]

# Top gems by corpus frequency (filled by prepare); fallback minimal set
DEFAULT_GEM_VOCAB = [
    "Elemental Hit", "Penance Brand", "Flicker Strike", "Arc", "Spark",
    "Righteous Fire", "Summon Raging Spirit", "Animate Guardian",
    "Grace", "Determination", "Hatred", "Wrath", "Anger", "Precision",
    "Vitality", "Clarity", "Defiance Banner", "Malevolence", "Haste",
    "Empower Support", "Enlighten Support", "Enhance Support",
    "Increased Critical Strikes Support", "Multistrike Support",
    "Trinity Support", "Awakened", "Lifetap Support",
]

CLASS_VOCAB = [
    "Marauder", "Ranger", "Witch", "Duelist", "Templar", "Shadow", "Scion",
]
ASCEND_VOCAB = [
    "Juggernaut", "Berserker", "Chieftain", "Slayer", "Gladiator", "Champion",
    "Raider", "Deadeye", "Pathfinder", "Assassin", "Saboteur", "Trickster",
    "Necromancer", "Elementalist", "Occultist", "Inquisitor", "Hierophant",
    "Guardian", "Ascendant",
]


def _main_skill(meta: dict) -> str:
    gems = meta.get("allGemNames") or []
    if not gems:
        for grp in meta.get("skillGroups") or []:
            gs = grp.get("gems") or []
            if gs:
                return str(gs[0])
        return ""
    # skip auras/support-only groups: pick first non-support-looking gem
    for g in gems:
        g = str(g)
        if "Support" not in g and g not in ("Steelskin", "Frostblink", "Whirling Blades",
                                              "Cast when Damage Taken Support"):
            return g
    return str(gems[0])


def _gem_flags(gems: list[str], vocab: list[str]) -> dict[str, int]:
    gset = {str(g) for g in gems}
    return {f"gem_{v}": int(v in gset) for v in vocab}


def _one_hot(value: str, vocab: list[str], prefix: str) -> dict[str, int]:
    v = (value or "").strip()
    return {f"{prefix}_{x}": int(x == v) for x in vocab}


def build_features_from_meta(meta: dict, *, gem_vocab: list[str] | None = None) -> dict[str, Any]:
    """Layer A: skeleton from ninja meta.json."""
    gems = meta.get("allGemNames") or []
    gem_vocab = gem_vocab or DEFAULT_GEM_VOCAB
    feats: dict[str, Any] = {
        "level": float(meta.get("level") or 100) / 100.0,
        "passive_count": float(meta.get("passiveCount") or 0) / 130.0,
        "main_skill": _main_skill(meta),
    }
    feats.update(_one_hot(meta.get("class") or "", CLASS_VOCAB, "class"))
    feats.update(_one_hot(meta.get("ascendancy") or "", ASCEND_VOCAB, "asc"))
    feats.update(_gem_flags(gems, gem_vocab))
    
    # Safe keystone extraction
    ks = set()
    for k in (meta.get("keyStones") or []):
        if isinstance(k, dict):
            name = k.get("name")
            if name:
                ks.add(str(name))
        else:
            ks.add(str(k))

    for name in ("Iron Reflexes", "Mind Over Matter", "Ghost Reaver", "Blood Magic",
                 "Chaos Inoculation", "Resolute Technique", "Avatar of Fire",
                 "Eldritch Battery", "Ancestral Bond", "Pain Attunement"):
        feats[f"ks_{name}"] = int(name in ks)

    # Minimal Layer B features
    main_skill = feats["main_skill"]
    has_crit_gem = any("crit" in g.lower() for g in gems)
    feats["is_crit"] = int(has_crit_gem and "Resolute Technique" not in ks)

    dot_keywords = {"dot", "decay", "ignite", "poison", "bleed", "affliction", "ailment", "cruelty", "void manipulation", "over time"}
    has_dot_gem = any(any(kw in g.lower() for kw in dot_keywords) for g in gems)
    dot_skills = {"righteous fire", "blight", "essence drain", "contagion", "toxic rain", "caustic arrow", "soulrend", "scourge arrow", "poisonous concoction"}
    main_skill_lower = main_skill.lower()
    feats["is_dot"] = int(has_dot_gem or any(ds in main_skill_lower for ds in dot_skills))

    minion_keywords = {"minion", "summon", "raise", "guardian", "golem", "spectre", "zombie", "animate", "phastasm", "holy relic", "absolution"}
    has_minion_gem = any(any(kw in g.lower() for kw in minion_keywords) for g in gems)
    feats["is_minion"] = int(has_minion_gem or meta.get("ascendancy") == "Necromancer")

    conv_keywords = {"conversion", "convert", "cold to fire", "physical to lightning", "trinity"}
    has_conv_gem = any(any(kw in g.lower() for kw in conv_keywords) for g in gems)
    feats["is_conversion"] = int(has_conv_gem or "Avatar of Fire" in ks)

    es_keystones = {"Chaos Inoculation", "Ghost Reaver", "Eldritch Battery", "Pain Attunement"}
    has_es_ks = any(k in ks for k in es_keystones)
    feats["is_es_based"] = int(has_es_ks or meta.get("ascendancy") in ("Occultist", "Trickster"))

    feats["is_life_based"] = int("Chaos Inoculation" not in ks)

    return feats


def notable_labels_from_xml_fast(xml: str, graph) -> set[str]:
    """Parse Spec nodes from XML; filter to main-tree notables via cached graph."""
    root = ET.fromstring(xml)
    tree = root.find("Tree")
    if tree is None:
        return set()
    specs = tree.findall("Spec")
    if not specs:
        return set()
    active = int(tree.get("activeSpec") or 1)
    spec = specs[min(active - 1, len(specs) - 1)]
    raw = spec.get("nodes") or ""
    node_ids = {n.strip() for n in raw.split(",") if n.strip()}
    return {n for n in node_ids if n in graph.nodes
            and graph.nodes[n].type in ("Notable", "Keystone")
            and not graph.nodes[n].ascendancy}


_ref_graph = None


def get_reference_graph():
    """Single PoB load: passive tree node metadata (types, ascendancy)."""
    global _ref_graph
    if _ref_graph is None:
        template = _REPO / "builds" / "10.txt"
        xml = load_xml(template)
        pob = PobHeadless()
        pob.load_build_xml(xml)
        _ref_graph = load_tree_graph(pob)
    return _ref_graph


def notable_labels_from_xml(xml: str) -> tuple[set[str], set[str]]:
    """Main-tree notable/keystone node IDs from full build XML."""
    graph = get_reference_graph()
    labels = notable_labels_from_xml_fast(xml, graph)
    # allocated set for API compat
    root = ET.fromstring(xml)
    spec = root.find("Tree").findall("Spec")[0]  # type: ignore[union-attr]
    raw = spec.get("nodes") or ""
    allocated = {n.strip() for n in raw.split(",") if n.strip()}
    return labels, allocated


def load_xml(path: Path) -> str:
    from poebuildgen import pobcode
    raw = path.read_text(encoding="utf-8").strip()
    if raw.startswith("<"):
        return raw
    return pobcode.decode(raw).decode("utf-8")


def gems_from_pob_xml(xml: str) -> list[str]:
    root = ET.fromstring(xml)
    out: list[str] = []
    for gem in root.iter("Gem"):
        name = gem.get("nameSpec") or gem.get("skillId") or ""
        if name:
            out.append(name)
    return out


def build_features_from_xml(xml: str, graph, *, gem_vocab: list[str] | None = None) -> dict[str, Any]:
    keystones = []
    for nid in graph.allocated:
        if nid in graph.nodes and graph.nodes[nid].type == "Keystone":
            keystones.append(graph.nodes[nid].dn)
    meta = {
        "class": graph.cur_class,
        "ascendancy": graph.cur_ascend,
        "allGemNames": gems_from_pob_xml(xml),
        "level": 100,
        "passiveCount": len(graph.allocated),
        "keyStones": keystones,
    }
    return build_features_from_meta(meta, gem_vocab=gem_vocab)


def build_gem_vocab(corpus_dir: Path, top_n: int = 80) -> list[str]:
    from collections import Counter
    c: Counter[str] = Counter()
    for m in corpus_dir.glob("*.meta.json"):
        try:
            meta = json.loads(m.read_text(encoding="utf-8"))
        except Exception:
            continue
        for g in meta.get("allGemNames") or []:
            c[str(g)] += 1
    return [g for g, _ in c.most_common(top_n)]


def feature_vector(feats: dict[str, Any], columns: list[str]) -> list[float]:
    return [float(feats.get(c, 0)) for c in columns]
