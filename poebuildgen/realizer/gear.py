from __future__ import annotations

import copy
import re
import random
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from ortools.sat.python import cp_model
from poebuildgen.headless import PobHeadless, _lua_to_py
from poebuildgen.pool import WorkerPool, _dps_from

DPS_KEYS = ("CombinedDPS", "TotalDPS", "FullDPS")
RES_KEYS = {
    "fire_res": "FireResist",
    "cold_res": "ColdResist",
    "light_res": "LightningResist",
    "chaos_res": "ChaosResist"
}
LIFE_SLOTS = frozenset({
    "Helmet", "Body Armour", "Gloves", "Boots", "Belt",
    "Amulet", "Ring 1", "Ring 2"
})
CORE = [
    "Weapon 1", "Weapon 2", "Helmet", "Body Armour", "Gloves",
    "Boots", "Belt", "Amulet", "Ring 1", "Ring 2"
]

# --- Stats parsing (from scripts.spikeB.stats) ---

OFFENSE = [
    "inc_spell_dmg", "inc_elem_dmg", "inc_light_dmg", "inc_cold_dmg", "inc_fire_dmg",
    "inc_phys_dmg", "inc_chaos_dmg", "added_light_spell", "added_cold_spell",
    "added_fire_spell", "added_chaos_spell", "cast_speed", "crit_chance",
    "crit_multi", "dot_multi", "plus_spell_gem", "plus_all_gem", "pen_elem",
    "added_light_attack", "added_cold_attack", "added_fire_attack",
    "added_phys_attack", "added_chaos_attack", "attack_speed",
]
RES = ["fire_res", "cold_res", "light_res", "chaos_res"]
ATTR = ["str", "dex", "int"]
DEFENSE = ["life", "inc_life", "es", "inc_es", "mana", *RES, *ATTR]
OBJECTIVE = OFFENSE + ATTR

# Probes for marginals
PROBES: list[tuple[str, str, float]] = [
    ("inc_spell_dmg",     "100% increased Spell Damage", 100),
    ("inc_elem_dmg",      "100% increased Elemental Damage", 100),
    ("inc_light_dmg",     "100% increased Lightning Damage", 100),
    ("inc_cold_dmg",      "100% increased Cold Damage", 100),
    ("inc_fire_dmg",      "100% increased Fire Damage", 100),
    ("inc_phys_dmg",      "100% increased Physical Damage", 100),
    ("inc_chaos_dmg",     "100% increased Chaos Damage", 100),
    ("added_light_spell", "Adds 100 to 100 Lightning Damage to Spells", 100),
    ("added_cold_spell",  "Adds 100 to 100 Cold Damage to Spells", 100),
    ("added_fire_spell",  "Adds 100 to 100 Fire Damage to Spells", 100),
    ("added_chaos_spell", "Adds 100 to 100 Chaos Damage to Spells", 100),
    ("cast_speed",        "50% increased Cast Speed", 50),
    ("crit_chance",       "100% increased Critical Strike Chance for Spells", 100),
    ("crit_multi",        "+100% to Critical Strike Multiplier for Spells", 100),
    ("dot_multi",         "+50% to Damage over Time Multiplier", 50),
    ("plus_spell_gem",    "+1 to Level of all Spell Skill Gems", 1),
    ("plus_all_gem",      "+1 to Level of all Skill Gems", 1),
    ("pen_elem",          "Damage Penetrates 10% Elemental Resistances", 10),
    ("int",               "+100 to Intelligence", 100),
    ("str",               "+100 to Strength", 100),
    ("dex",               "+100 to Dexterity", 100),
    ("added_light_attack", "Adds 100 to 100 Lightning Damage to Attacks", 100),
    ("added_cold_attack",  "Adds 100 to 100 Cold Damage to Attacks", 100),
    ("added_fire_attack",  "Adds 100 to 100 Fire Damage to Attacks", 100),
    ("added_phys_attack",  "Adds 100 to 100 Physical Damage to Attacks", 100),
    ("added_chaos_attack", "Adds 100 to 100 Chaos Damage to Attacks", 100),
    ("attack_speed",       "50% increased Attack Speed", 50),
]

_TAG = re.compile(r"^\{[^}]*\}\s*")

def _last(m: re.Match, *groups) -> float:
    vals = [float(m.group(g)) for g in groups if m.group(g) is not None]
    return vals[-1] if vals else 0.0

def _last_pair(m: re.Match) -> float:
    a = float(m.group(2) if m.group(2) is not None else m.group(1))
    b = float(m.group(4) if m.group(4) is not None else m.group(3))
    return (a + b) / 2.0

_R = lambda p: re.compile(p, re.IGNORECASE)
_NUM = r"\(?(\d+(?:\.\d+)?)(?:-(\d+(?:\.\d+)?))?\)?"

_RULES: list[tuple[re.Pattern, Callable]] = [
    (_R(r"Adds " + _NUM + r" to " + _NUM + r" (Lightning|Cold|Fire|Chaos) Damage to Spells"),
     lambda m: {f"added_{m.group(5).lower()}_spell": (_last_pair(m))}),
    (_R(r"Adds " + _NUM + r" to " + _NUM + r" (Lightning|Cold|Fire|Physical|Chaos) Damage to Attacks"),
     lambda m: {f"added_{m.group(5).lower()}_attack": (_last_pair(m))}),
    (_R(r"Adds " + _NUM + r" to " + _NUM + r" (Lightning|Cold|Fire|Physical|Chaos) Damage$"),
     lambda m: {f"added_{m.group(5).lower()}_attack": (_last_pair(m))}),
    (_R(_NUM + r"% increased Attack Speed"), lambda m: {"attack_speed": _last(m, 1, 2)}),
    (_R(r"\+(\d+) to Level of all Spell Skill Gems"), lambda m: {"plus_spell_gem": float(m.group(1))}),
    (_R(r"\+(\d+) to Level of all Skill Gems"), lambda m: {"plus_all_gem": float(m.group(1))}),
    (_R(r"\+(\d+) to Level of all (Lightning|Cold|Fire|Chaos|Physical) Spell Skill Gems"),
     lambda m: {"plus_spell_gem": float(m.group(1))}),
    (_R(r"\+" + _NUM + r"% to Critical Strike Multiplier for Spells"), lambda m: {"crit_multi": _last(m, 1, 2)}),
    (_R(r"\+" + _NUM + r"% to Global Critical Strike Multiplier"), lambda m: {"crit_multi": _last(m, 1, 2)}),
    (_R(r"\+" + _NUM + r"% to Critical Strike Multiplier"), lambda m: {"crit_multi": _last(m, 1, 2)}),
    (_R(_NUM + r"% increased Critical Strike Chance for Spells"), lambda m: {"crit_chance": _last(m, 1, 2)}),
    (_R(_NUM + r"% increased Global Critical Strike Chance"), lambda m: {"crit_chance": _last(m, 1, 2)}),
    (_R(_NUM + r"% increased Critical Strike Chance"), lambda m: {"crit_chance": _last(m, 1, 2)}),
    (_R(_NUM + r"% increased Cast Speed"), lambda m: {"cast_speed": _last(m, 1, 2)}),
    (_R(r"\+" + _NUM + r"% to Damage over Time Multiplier"), lambda m: {"dot_multi": _last(m, 1, 2)}),
    (_R(r"Damage Penetrates " + _NUM + r"% Elemental Resistances"), lambda m: {"pen_elem": _last(m, 1, 2)}),
    (_R(r"Damage Penetrates " + _NUM + r"% (Lightning|Cold|Fire) Resistance"), lambda m: {"pen_elem": _last(m, 1, 2)}),
    (_R(_NUM + r"% increased Spell Damage"), lambda m: {"inc_spell_dmg": _last(m, 1, 2)}),
    (_R(_NUM + r"% increased Elemental Damage with Attack Skills"), lambda m: {}),
    (_R(_NUM + r"% increased Elemental Damage"), lambda m: {"inc_elem_dmg": _last(m, 1, 2)}),
    (_R(_NUM + r"% increased Lightning Damage"), lambda m: {"inc_light_dmg": _last(m, 1, 2)}),
    (_R(_NUM + r"% increased Cold Damage"), lambda m: {"inc_cold_dmg": _last(m, 1, 2)}),
    (_R(_NUM + r"% increased Fire Damage"), lambda m: {"inc_fire_dmg": _last(m, 1, 2)}),
    (_R(_NUM + r"% increased Physical Damage"), lambda m: {"inc_phys_dmg": _last(m, 1, 2)}),
    (_R(_NUM + r"% increased Chaos Damage"), lambda m: {"inc_chaos_dmg": _last(m, 1, 2)}),
    (_R(r"\+" + _NUM + r"% to all Elemental Resistances"),
     lambda m: {"fire_res": _last(m, 1, 2), "cold_res": _last(m, 1, 2), "light_res": _last(m, 1, 2)}),
    (_R(r"\+" + _NUM + r"% to Fire and Cold Resistances"),
     lambda m: {"fire_res": _last(m, 1, 2), "cold_res": _last(m, 1, 2)}),
    (_R(r"\+" + _NUM + r"% to Fire and Lightning Resistances"),
     lambda m: {"fire_res": _last(m, 1, 2), "light_res": _last(m, 1, 2)}),
    (_R(r"\+" + _NUM + r"% to Cold and Lightning Resistances"),
     lambda m: {"cold_res": _last(m, 1, 2), "light_res": _last(m, 1, 2)}),
    (_R(r"\+" + _NUM + r"% to Fire Resistance"), lambda m: {"fire_res": _last(m, 1, 2)}),
    (_R(r"\+" + _NUM + r"% to Cold Resistance"), lambda m: {"cold_res": _last(m, 1, 2)}),
    (_R(r"\+" + _NUM + r"% to Lightning Resistance"), lambda m: {"light_res": _last(m, 1, 2)}),
    (_R(r"\+" + _NUM + r"% to Chaos Resistance"), lambda m: {"chaos_res": _last(m, 1, 2)}),
    (_R(r"\+" + _NUM + r" to all Attributes"),
     lambda m: {"str": _last(m, 1, 2), "dex": _last(m, 1, 2), "int": _last(m, 1, 2)}),
    (_R(r"\+" + _NUM + r" to Strength and Dexterity"),
     lambda m: {"str": _last(m, 1, 2), "dex": _last(m, 1, 2)}),
    (_R(r"\+" + _NUM + r" to Strength and Intelligence"),
     lambda m: {"str": _last(m, 1, 2), "int": _last(m, 1, 2)}),
    (_R(r"\+" + _NUM + r" to Dexterity and Intelligence"),
     lambda m: {"dex": _last(m, 1, 2), "int": _last(m, 1, 2)}),
    (_R(r"\+" + _NUM + r" to Strength"), lambda m: {"str": _last(m, 1, 2)}),
    (_R(r"\+" + _NUM + r" to Dexterity"), lambda m: {"dex": _last(m, 1, 2)}),
    (_R(r"\+" + _NUM + r" to Intelligence"), lambda m: {"int": _last(m, 1, 2)}),
    (_R(_NUM + r"% increased maximum Life"), lambda m: {"inc_life": _last(m, 1, 2)}),
    (_R(r"\+" + _NUM + r" to maximum Life"), lambda m: {"life": _last(m, 1, 2)}),
    (_R(_NUM + r"% increased maximum Energy Shield"), lambda m: {"inc_es": _last(m, 1, 2)}),
    (_R(r"\+" + _NUM + r" to maximum Energy Shield"), lambda m: {"es": _last(m, 1, 2)}),
    (_R(r"\+" + _NUM + r" to maximum Mana"), lambda m: {"mana": _last(m, 1, 2)}),
]

def parse_mod_line(line: str) -> dict[str, float]:
    s = _TAG.sub("", line).strip()
    for rx, fn in _RULES:
        m = rx.match(s)
        if m:
            return {k: v for k, v in fn(m).items() if v}
    return {}

def parse_item_stats(explicit_lines: list[str]) -> dict[str, float]:
    out: dict[str, float] = {}
    for ln in explicit_lines:
        for k, v in parse_mod_line(ln).items():
            out[k] = out.get(k, 0.0) + v
    return out


# --- Item and Build models (ported from harness.py) ---

@dataclass
class Item:
    raw: str
    item_id: str
    lines: list[str]
    rarity: str
    name: str
    base: str
    implicit_count: int
    impl_idx: int
    explicit_start: int
    explicits: list[str]
    item_level: int = 1
    prefix_cap: int = 3
    suffix_cap: int = 3

    @property
    def header_through_implicits(self) -> list[str]:
        return self.lines[: self.explicit_start]

    def with_explicits(self, new_explicits: list[str]) -> str:
        body = self.header_through_implicits + list(new_explicits)
        return "\n".join(body) + "\n"

    def stripped(self) -> str:
        return self.with_explicits([])


def parse_item(item_el: ET.Element) -> Item:
    raw = item_el.text or ""
    lines = raw.splitlines()
    def clean(s: str) -> str:
        return s.strip()

    rarity = name = base = ""
    impl_idx = -1
    implicit_count = 0
    content_idx = [i for i, l in enumerate(lines) if clean(l)]
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
    xml: str
    root: ET.Element
    items_el: ET.Element
    itemset_el: ET.Element
    by_id: dict[str, ET.Element]
    slot_to_id: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_xml(cls, xml: str) -> "Build":
        root = ET.fromstring(xml)
        items_el = root.find("Items")
        by_id = {it.get("id"): it for it in items_el.findall("Item")}
        itemsets = [c for c in items_el if c.tag == "ItemSet"]
        itemset = itemsets[0] if itemsets else ET.SubElement(items_el, "ItemSet")
        slot_to_id = {}
        for s in itemset.findall("Slot"):
            if s.get("itemId", "0") != "0":
                slot_to_id[s.get("name")] = s.get("itemId")
        return cls(xml=xml, root=root, items_el=items_el,
                   itemset_el=itemset, by_id=by_id, slot_to_id=slot_to_id)

    def rare_core_slots(self) -> list[str]:
        out = []
        for slot in CORE:
            iid = self.slot_to_id.get(slot)
            if iid and iid in self.by_id and (self.by_id[iid].text or "").find("Rarity: RARE") >= 0:
                out.append(slot)
        return out

    def item_for_slot(self, slot: str) -> Item:
        return parse_item(self.by_id[self.slot_to_id[slot]])

    def render(self, overrides: dict[str, str] | None = None) -> str:
        root = copy.deepcopy(self.root)
        items_el = root.find("Items")
        by_id = {it.get("id"): it for it in items_el.findall("Item")}
        for slot, text in (overrides or {}).items():
            iid = self.slot_to_id[slot]
            by_id[iid].text = text
        return ET.tostring(root, encoding="unicode")


# --- ModPool loading ---

_POOL_LUA = r"""
function(baseName, ilvl)
  local base = data.itemBases[baseName]
  local out = {}
  if not base then return out end
  local tagset = {}
  if base.tags then
    for k, v in pairs(base.tags) do
      if v == true then tagset[k] = true else tagset[v] = true end
    end
  end
  for _, m in pairs(data.itemMods.Item) do
    if (m.type == "Prefix" or m.type == "Suffix") and (m.level or 1) <= ilvl then
      local w = nil
      if m.weightKey then
        for i = 1, #m.weightKey do
          local k = m.weightKey[i]
          if tagset[k] or k == "default" then w = m.weightVal[i]; break end
        end
      end
      if w and w > 0 then
        local lines = {}
        for i = 1, 8 do if m[i] then lines[#lines+1] = m[i] end end
        table.insert(out, {
          affix = m.affix or "", type = m.type, group = m.group or "?",
          level = m.level or 1, lines = lines,
        })
      end
    end
  end
  return out
end
"""

@dataclass
class Affix:
    affix: str
    type: str
    group: str
    level: int
    lines: list[str]
    stats: dict[str, float]


def _max_roll_lines(lines: list[str]) -> list[str]:
    def repl(m):
        return m.group(2)
    out = []
    for ln in lines:
        out.append(re.sub(r"\((\d+(?:\.\d+)?)-(\d+(?:\.\d+)?)\)", repl, ln))
    return out


class ModPool:
    def __init__(self, pob: PobHeadless):
        self._fn = pob.eval(_POOL_LUA)
        self._cache: dict[tuple[str, int], list[Affix]] = {}

    def for_base(self, base: str, ilvl: int) -> list[Affix]:
        key = (base, ilvl)
        if key in self._cache:
            return self._cache[key]
        rows = _lua_to_py(self._fn(base.encode("utf-8"), ilvl))
        rows = rows if isinstance(rows, list) else []
        best: dict[str, Affix] = {}
        for r in rows:
            lines = r.get("lines") or []
            if isinstance(lines, dict):
                lines = [lines[k] for k in sorted(lines)]
            stats = parse_item_stats(lines)
            if not stats:
                continue
            grp = r.get("group", "?")
            cur = best.get(grp)
            if cur is None or r.get("level", 1) > cur.level:
                best[grp] = Affix(affix=r.get("affix", ""), type=r.get("type", "Prefix"),
                                  group=grp, level=r.get("level", 1),
                                  lines=_max_roll_lines(lines), stats=stats)
        pool = list(best.values())
        self._cache[key] = pool
        return pool


# --- Solver & Optimizations ---

@dataclass
class SolveConfig:
    res_cap: dict[str, float]
    res_baseline: dict[str, float]
    chaos_floor: float = 0.0
    life_base: float = 3000.0
    life_target: float = 0.0
    life_flat_coef: float = 1.0
    life_inc_coef: float = 0.0


@dataclass
class SolveResult:
    overrides: dict[str, str]
    chosen: dict[str, list]
    proxy_dps_gain: float
    status: str
    res_final: dict[str, float] = field(default_factory=dict)
    life_value: float = 0.0


def _affix_value(affix: Affix, marg: dict[str, float]) -> float:
    return sum(affix.stats.get(k, 0.0) * marg.get(k, 0.0) for k in OBJECTIVE)


def _life_value(affix: Affix, life_base: float, flat_coef: float = 1.0, inc_coef: float = 0.0) -> float:
    flat = affix.stats.get("life", 0.0)
    inc = affix.stats.get("inc_life", 0.0)
    if inc_coef or flat_coef != 1.0:
        return flat * flat_coef + inc * inc_coef
    return flat + inc * life_base / 100.0


def solve(build: Build, opt_slots: list[str], pools: dict[str, list[Affix]], marg: dict[str, float],
          cfg: SolveConfig) -> SolveResult:
    m = cp_model.CpModel()
    cand = []
    for slot in opt_slots:
        for af in pools[slot]:
            x = m.NewBoolVar(f"{slot}:{af.group}:{af.type}")
            cand.append((slot, af, x))

    items = {slot: build.item_for_slot(slot) for slot in opt_slots}

    # slot constraints
    for slot in opt_slots:
        it = items[slot]
        pref = [x for (s, a, x) in cand if s == slot and a.type == "Prefix"]
        suff = [x for (s, a, x) in cand if s == slot and a.type == "Suffix"]
        if pref:
            m.Add(sum(pref) <= it.prefix_cap)
        if suff:
            m.Add(sum(suff) <= it.suffix_cap)
        groups: dict[str, list] = {}
        for (s, a, x) in cand:
            if s == slot:
                groups.setdefault(a.group, []).append(x)
        for g, xs in groups.items():
            if len(xs) > 1:
                m.Add(sum(xs) <= 1)

    SC = 1000

    # resist constraints
    for r in ("fire_res", "cold_res", "light_res"):
        need = cfg.res_cap.get(r, 0) - cfg.res_baseline.get(r, 0)
        if need > 0:
            terms = [int(round(a.stats.get(r, 0) * SC)) * x for (s, a, x) in cand if a.stats.get(r, 0)]
            if terms:
                m.Add(sum(terms) >= int(round(need * SC)))

    need_c = cfg.res_cap.get("chaos_res", 0) - cfg.res_baseline.get("chaos_res", 0)
    if need_c > 0:
        terms = [int(round(a.stats.get("chaos_res", 0) * SC)) * x for (s, a, x) in cand if a.stats.get("chaos_res", 0)]
        if terms:
            m.Add(sum(terms) >= int(round(need_c * SC)))

    # life constraint
    if cfg.life_target > 0:
        terms = [int(round(_life_value(a, cfg.life_base, cfg.life_flat_coef, cfg.life_inc_coef) * SC)) * x
                 for (s, a, x) in cand if _life_value(a, cfg.life_base, cfg.life_flat_coef, cfg.life_inc_coef)]
        if terms:
            m.Add(sum(terms) >= int(round(cfg.life_target * SC)))

    # objective
    obj = [int(round(_affix_value(a, marg))) * x for (s, a, x) in cand]
    m.Maximize(sum(obj))

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = 15
    st = solver.Solve(m)
    status = {cp_model.OPTIMAL: "OPTIMAL", cp_model.FEASIBLE: "FEASIBLE",
              cp_model.INFEASIBLE: "INFEASIBLE"}.get(st, str(st))

    chosen: dict[str, list] = {s: [] for s in opt_slots}
    res_final = dict(cfg.res_baseline)
    life_val = 0.0
    if st in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        for (s, a, x) in cand:
            if solver.Value(x):
                chosen[s].append(a)
                for r in ("fire_res", "cold_res", "light_res", "chaos_res"):
                    res_final[r] = res_final.get(r, 0) + a.stats.get(r, 0)
                life_val += _life_value(a, cfg.life_base, cfg.life_flat_coef, cfg.life_inc_coef)

    overrides = {}
    for slot in opt_slots:
        lines = []
        for a in chosen[slot]:
            lines.extend(a.lines)
        overrides[slot] = items[slot].with_explicits(lines)

    return SolveResult(overrides=overrides, chosen=chosen,
                       proxy_dps_gain=solver.ObjectiveValue() if st in (cp_model.OPTIMAL, cp_model.FEASIBLE) else 0.0,
                       status=status, res_final=res_final, life_value=life_val)


# --- Probing & Swaps ---

def measure_life_coefs(pool: WorkerPool, build: Build, carrier_slot: str, stripped_overrides: dict,
                       fingerprint: dict | None = None) -> tuple[float, float]:
    item = build.item_for_slot(carrier_slot)
    
    base_req = {"xml": build.render(stripped_overrides), "stats": ["Life"]}
    if fingerprint:
        base_req["fingerprint"] = fingerprint
    base_res = pool.map([base_req])[0]
    base_life = float(base_res.get("stats", {}).get("Life") or 0)

    ov_flat = dict(stripped_overrides)
    ov_flat[carrier_slot] = item.with_explicits(["+100 to maximum Life"])
    flat_req = {"xml": build.render(ov_flat), "stats": ["Life"]}
    if fingerprint:
        flat_req["fingerprint"] = fingerprint
    flat_res = pool.map([flat_req])[0]
    life_flat = float(flat_res.get("stats", {}).get("Life") or 0)

    ov_inc = dict(stripped_overrides)
    ov_inc[carrier_slot] = item.with_explicits(["10% increased maximum Life"])
    inc_req = {"xml": build.render(ov_inc), "stats": ["Life"]}
    if fingerprint:
        inc_req["fingerprint"] = fingerprint
    inc_res = pool.map([inc_req])[0]
    life_inc = float(inc_res.get("stats", {}).get("Life") or 0)

    flat_coef = max(0.01, (life_flat - base_life) / 100.0) if life_flat > base_life else 1.0
    inc_coef = max(0.0, (life_inc - base_life) / 10.0) if life_inc > base_life else 0.0
    return flat_coef, inc_coef


def measure_offense_marginals(pool: WorkerPool, build: Build, opt_slots: list[str], overrides: dict[str, str],
                             prefer: str = "CombinedDPS", fingerprint: dict | None = None) -> tuple[float, dict[str, float]]:
    carrier = opt_slots[0]
    carrier_item = build.item_for_slot(carrier)
    
    # get current explicits on carrier
    text = overrides.get(carrier)
    if text:
        head = carrier_item.header_through_implicits
        lines = text.splitlines()
        carrier_explicits = [l.strip() for l in lines[len(head):] if l.strip()]
    else:
        carrier_explicits = []

    xmls = [build.render(overrides)]
    for _key, line, _amt in PROBES:
        ov = dict(overrides)
        ov[carrier] = carrier_item.with_explicits(carrier_explicits + [line])
        xmls.append(build.render(ov))

    reqs = [{"xml": x, "stats": list(DPS_KEYS)} for x in xmls]
    if fingerprint:
        for r in reqs:
            r["fingerprint"] = fingerprint
    res = pool.map(reqs)
    
    base_dps = _dps_from(res[0], prefer, DPS_KEYS)
    marg: dict[str, float] = {}
    for (key, _line, amt), r in zip(PROBES, res[1:]):
        d = _dps_from(r, prefer, DPS_KEYS)
        marg[key] = (d - base_dps) / amt
    return base_dps, marg


# --- Fixpoint and Hillclimbing coordinator ---

def _signature(res: SolveResult, opt_slots: list[str]) -> tuple:
    return tuple(
        tuple(sorted((a.group, a.type) for a in res.chosen.get(s, [])))
        for s in opt_slots
    )

def _avg_marg(m1: dict[str, float], m2: dict[str, float]) -> dict[str, float]:
    keys = set(m1) | set(m2)
    return {k: (m1.get(k, 0.0) + m2.get(k, 0.0)) / 2.0 for k in keys}


@dataclass
class FixpointStep:
    iter: int
    status: str
    dps: float
    signature: tuple
    action: str


@dataclass
class FixpointResult:
    dps: float
    overrides: dict[str, str]
    result: SolveResult
    history: list[FixpointStep] = field(default_factory=list)
    d1_applied: bool = False
    last_marg: dict[str, float] = field(default_factory=dict)


def fixpoint_d1(pool: WorkerPool, build: Build, opt_slots: list[str], pools: dict[str, list[Affix]],
                cfg: SolveConfig, prefer: str, iters: int = 2,
                initial_overrides: dict[str, str] | None = None,
                fingerprint: dict | None = None) -> FixpointResult:
    overrides = initial_overrides or {s: build.item_for_slot(s).stripped() for s in opt_slots}
    best_dps, best_ov, best_res = 0.0, overrides, SolveResult({}, {}, 0.0, "NONE")
    prev_sig: tuple | None = None
    prev_marg: dict[str, float] | None = None
    history: list[FixpointStep] = []
    d1_applied = False
    last_marg: dict[str, float] = {}

    for k in range(iters):
        _, marg = measure_offense_marginals(pool, build, opt_slots, overrides, prefer, fingerprint)
        last_marg = marg

        use_marg = marg
        if prev_sig is not None and prev_marg is not None:
            res_probe = solve(build, opt_slots, pools, marg, cfg)
            if res_probe.status != "INFEASIBLE":
                sig_probe = _signature(res_probe, opt_slots)
                if sig_probe == prev_sig:
                    use_marg = _avg_marg(marg, prev_marg)
                    d1_applied = True

        res = solve(build, opt_slots, pools, use_marg, cfg)
        if res.status == "INFEASIBLE":
            history.append(FixpointStep(k, "INFEASIBLE", 0.0, (), "infeasible"))
            break

        sig = _signature(res, opt_slots)
        
        # dps evaluate
        dps_req = {"xml": build.render(res.overrides), "stats": list(DPS_KEYS)}
        if fingerprint:
            dps_req["fingerprint"] = fingerprint
        dps_res = pool.map([dps_req])[0]
        dps = _dps_from(dps_res, prefer, DPS_KEYS)

        if dps > best_dps:
            best_dps, best_ov, best_res = dps, res.overrides, res

        improved = k == 0 or dps > history[-1].dps * 1.005
        action = "continue" if improved else "d1_inconclusive"
        history.append(FixpointStep(k, res.status, dps, sig, action))

        if not improved and k > 0:
            break

        prev_sig, prev_marg = sig, marg
        overrides = res.overrides

    return FixpointResult(best_dps, best_ov, best_res, history, d1_applied, last_marg)


# --- Multi Start & Hill Climb Refinement ---

@dataclass
class HybridResult:
    dps: float
    overrides: dict[str, str]
    chosen: dict[str, list]
    evals: int
    seeds: list[str]
    seed_dps: dict[str, float]
    stability: float


def _feasible(ch: dict[str, list[Affix]], items: dict[str, Item], opt_slots: list[str], cfg: SolveConfig,
              res_keys=("fire_res", "cold_res", "light_res", "chaos_res")) -> bool:
    res = dict(cfg.res_baseline)
    life = 0.0
    for s in opt_slots:
        it = items[s]
        npre = sum(1 for a in ch[s] if a.type == "Prefix")
        nsuf = sum(1 for a in ch[s] if a.type == "Suffix")
        grps = [a.group for a in ch[s]]
        if npre > it.prefix_cap or nsuf > it.suffix_cap or len(grps) != len(set(grps)):
            return False
        for a in ch[s]:
            for r in res_keys:
                res[r] = res.get(r, 0) + a.stats.get(r, 0)
            life += _life_value(a, cfg.life_base, cfg.life_flat_coef, cfg.life_inc_coef)
    for r in ("fire_res", "cold_res", "light_res"):
        if res.get(r, 0) < cfg.res_cap.get(r, 0) - 1e-6:
            return False
    if res.get("chaos_res", 0) < cfg.res_cap.get("chaos_res", 0) - 1e-6:
        return False
    if life < cfg.life_target - 1e-6:
        return False
    return True


def _overrides_of(ch: dict[str, list[Affix]], items: dict[str, Item], opt_slots: list[str]) -> dict[str, str]:
    return {s: items[s].with_explicits([l for a in ch[s] for l in a.lines]) for s in opt_slots}


def hill_climb(pool: WorkerPool, build: Build, opt_slots: list[str], pools: dict[str, list[Affix]],
               cfg: SolveConfig, seed_chosen: dict[str, list[Affix]], prefer: str, max_evals: int = 600,
               fingerprint: dict | None = None) -> tuple[float, dict[str, str], int, dict[str, list[Affix]]]:
    items = {s: build.item_for_slot(s) for s in opt_slots}
    chosen = {s: list(seed_chosen.get(s, [])) for s in opt_slots}
    evals = 0

    def moves(ch):
        for s in opt_slots:
            grps = {a.group for a in ch[s]}
            npre = sum(1 for a in ch[s] if a.type == "Prefix")
            nsuf = sum(1 for a in ch[s] if a.type == "Suffix")
            it = items[s]
            for af in pools[s]:
                if af.group in grps:
                    continue
                if af.type == "Prefix" and npre < it.prefix_cap:
                    yield (s, None, af)
                elif af.type == "Suffix" and nsuf < it.suffix_cap:
                    yield (s, None, af)
            for old in list(ch[s]):
                for af in pools[s]:
                    if af.group == old.group or af.group in grps:
                        continue
                    if af.type == old.type:
                        yield (s, old, af)

    dps_req = {"xml": build.render(_overrides_of(chosen, items, opt_slots)), "stats": list(DPS_KEYS)}
    if fingerprint:
        dps_req["fingerprint"] = fingerprint
    cur_res = pool.map([dps_req])[0]
    cur = _dps_from(cur_res, prefer, DPS_KEYS)
    evals += 1
    improved = True
    while improved and evals < max_evals:
        improved = False
        trials = []
        for (s, old, af) in moves(chosen):
            trial = {k: list(v) for k, v in chosen.items()}
            if old is not None:
                trial[s].remove(old)
            trial[s].append(af)
            if _feasible(trial, items, opt_slots, cfg):
                trials.append(trial)
            if len(trials) >= max_evals - evals:
                break
        if not trials:
            break
            
        xmls = [build.render(_overrides_of(t, items, opt_slots)) for t in trials]
        reqs = [{"xml": x, "stats": list(DPS_KEYS)} for x in xmls]
        if fingerprint:
            for r in reqs:
                r["fingerprint"] = fingerprint
        res_list = pool.map(reqs)
        evals += len(trials)
        
        best_gain, best_ch, best_dps = 0.0, None, cur
        for t, r in zip(trials, res_list):
            d = _dps_from(r, prefer, DPS_KEYS)
            if d - cur > best_gain:
                best_gain, best_ch, best_dps = d - cur, t, d
        if best_ch and best_gain > cur * 0.002:
            chosen, cur = best_ch, best_dps
            improved = True
            
    return cur, _overrides_of(chosen, items, opt_slots), evals, chosen


def greedy_seed(pools: dict[str, list[Affix]], opt_slots: list[str], marg: dict[str, float],
                cfg: SolveConfig, build: Build) -> dict[str, list[Affix]]:
    items = {s: build.item_for_slot(s) for s in opt_slots}
    chosen: dict[str, list] = {s: [] for s in opt_slots}
    for s in opt_slots:
        it = items[s]
        grps: set[str] = set()
        npre = nsuf = 0
        ranked = sorted(pools[s], key=lambda a: _affix_value(a, marg), reverse=True)
        for af in ranked:
            if af.group in grps:
                continue
            if af.type == "Prefix" and npre < it.prefix_cap:
                trial = {k: list(v) for k, v in chosen.items()}
                trial[s].append(af)
                if _feasible(trial, items, opt_slots, cfg):
                    chosen[s].append(af)
                    grps.add(af.group)
                    npre += 1
            elif af.type == "Suffix" and nsuf < it.suffix_cap:
                trial = {k: list(v) for k, v in chosen.items()}
                trial[s].append(af)
                if _feasible(trial, items, opt_slots, cfg):
                    chosen[s].append(af)
                    grps.add(af.group)
                    nsuf += 1
    return chosen


def random_seed(pools: dict[str, list[Affix]], opt_slots: list[str], cfg: SolveConfig,
                build: Build, rng: random.Random) -> dict[str, list[Affix]]:
    items = {s: build.item_for_slot(s) for s in opt_slots}
    chosen: dict[str, list] = {s: [] for s in opt_slots}
    for s in opt_slots:
        it = items[s]
        grps: set[str] = set()
        candidates = list(pools[s])
        rng.shuffle(candidates)
        npre = nsuf = 0
        for af in candidates:
            if af.group in grps:
                continue
            if af.type == "Prefix" and npre < it.prefix_cap:
                trial = {k: list(v) for k, v in chosen.items()}
                trial[s].append(af)
                if _feasible(trial, items, opt_slots, cfg):
                    chosen[s].append(af)
                    grps.add(af.group)
                    npre += 1
            elif af.type == "Suffix" and nsuf < it.suffix_cap:
                trial = {k: list(v) for k, v in chosen.items()}
                trial[s].append(af)
                if _feasible(trial, items, opt_slots, cfg):
                    chosen[s].append(af)
                    grps.add(af.group)
                    nsuf += 1
    return chosen


def multi_start(pool: WorkerPool, build: Build, opt_slots: list[str], pools: dict[str, list[Affix]],
                cfg: SolveConfig, marg: dict[str, float], cps_chosen: dict[str, list[Affix]],
                prefer: str, max_evals: int = 400, n_random: int = 1,
                fingerprint: dict | None = None) -> HybridResult:
    seeds: dict[str, dict] = {
        "cpsat": {s: list(cps_chosen.get(s, [])) for s in opt_slots},
        "greedy": greedy_seed(pools, opt_slots, marg, cfg, build),
    }
    rng = random.Random(42)
    for i in range(n_random):
        seeds[f"random{i}"] = random_seed(pools, opt_slots, cfg, build, rng)

    seed_dps: dict[str, float] = {}
    best_dps, best_ov, best_ch, total_evals = 0.0, {}, {}, 0
    per_seed = max(80, max_evals // len(seeds))
    for name, ch in seeds.items():
        dps, ov, ev, chosen = hill_climb(pool, build, opt_slots, pools, cfg, ch, prefer, per_seed, fingerprint)
        seed_dps[name] = dps
        total_evals += ev
        if dps > best_dps:
            best_dps, best_ov, best_ch = dps, ov, chosen

    vals = [v for v in seed_dps.values() if v > 0]
    stability = min(vals) / max(vals) if vals and max(vals) > 0 else 0.0
    return HybridResult(best_dps, best_ov, best_ch, total_evals,
                        list(seeds), seed_dps, stability)


# --- Main optimize entry ---

def optimize_gear(
    pool: WorkerPool,
    pob: PobHeadless,
    b_cur: Build,
    b_ref: Build,
    opt_slots: list[str],
    prefer: str,
    gear_ov: dict[str, str],
    *,
    life_frac: float = 0.6,
    fixpoint_iters: int = 2,
    bis_evals: int = 300,
    hybrid: bool = True,
    fingerprint: dict | None = None,
) -> tuple[float, dict[str, str]]:
    # CP-SAT constraint building
    keys = list(RES_KEYS.values()) + ["Life"]
    
    ref_req = {"xml": b_ref.xml, "stats": keys}
    cur_req = {"xml": b_cur.render(gear_ov), "stats": keys}
    if fingerprint:
        ref_req["fingerprint"] = fingerprint
        cur_req["fingerprint"] = fingerprint
        
    stats_res = pool.map([ref_req, cur_req])
    ref = stats_res[0].get("stats", {}) if stats_res[0].get("ok") else {}
    cur = stats_res[1].get("stats", {}) if stats_res[1].get("ok") else {}

    res_cap = {k: float(ref.get(v) or 0) for k, v in RES_KEYS.items()}
    res_base = {k: float(cur.get(v) or 0) for k, v in RES_KEYS.items()}
    ref_life = float(ref.get("Life") or 0)
    cur_life = float(cur.get("Life") or 0)
    
    life_target = ref_life * life_frac
    # scale target life down to rare slots ratio
    n_life = sum(1 for s in opt_slots if s in LIFE_SLOTS)
    life_target = max(0.0, (ref_life - cur_life) * life_frac) * n_life / max(1, len(opt_slots))
    
    flat, inc = measure_life_coefs(pool, b_cur, opt_slots[0], gear_ov, fingerprint)
    
    cfg = SolveConfig(
        res_cap=res_cap, res_baseline=res_base, life_base=cur_life or 3000.0,
        life_target=life_target, life_flat_coef=flat, life_inc_coef=inc,
    )
    
    if pob is not None:
        modpool = ModPool(pob)
        pools = {s: modpool.for_base(b_cur.item_for_slot(s).base, b_cur.item_for_slot(s).item_level)
                 for s in opt_slots}
    else:
        query = [{"base": b_cur.item_for_slot(s).base, "ilvl": b_cur.item_for_slot(s).item_level} for s in opt_slots]
        res = pool.map([{"want_mod_pools": query}])[0]
        if not res.get("ok") or "mod_pools" not in res:
            raise RuntimeError(f"Failed to query mod pools from worker: {res.get('error')}")
        
        pools = {}
        for s, q_res in zip(opt_slots, res["mod_pools"]):
            rows = q_res["rows"]
            best = {}
            for r in rows:
                lines = r.get("lines") or []
                if isinstance(lines, dict):
                    lines = [lines[k] for k in sorted(lines)]
                stats = parse_item_stats(lines)
                if not stats:
                    continue
                grp = r.get("group", "?")
                cur = best.get(grp)
                if cur is None or r.get("level", 1) > cur.level:
                    best[grp] = Affix(affix=r.get("affix", ""), type=r.get("type", "Prefix"),
                                      group=grp, level=r.get("level", 1),
                                      lines=_max_roll_lines(lines), stats=stats)
            pools[s] = list(best.values())
             
    fp = fixpoint_d1(pool, b_cur, opt_slots, pools, cfg, prefer, fixpoint_iters,
                     initial_overrides=gear_ov, fingerprint=fingerprint)
                     
    cps_ov = fp.overrides if fp.dps > 0 else gear_ov
    cps_res = fp.result
    
    if not hybrid:
        dps_fallback = fp.dps
        if dps_fallback <= 0:
            fallback_req = {"xml": b_cur.render(gear_ov), "stats": list(DPS_KEYS)}
            if fingerprint:
                fallback_req["fingerprint"] = fingerprint
            dps_fallback = _dps_from(pool.map([fallback_req])[0], prefer, DPS_KEYS)
        return dps_fallback, cps_ov
        
    hr = multi_start(pool, b_cur, opt_slots, pools, cfg, fp.last_marg,
                     cps_res.chosen if cps_res.chosen else {}, prefer, max_evals=bis_evals, fingerprint=fingerprint)
    return hr.dps, hr.overrides
