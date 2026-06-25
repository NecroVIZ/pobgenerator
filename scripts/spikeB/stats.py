"""Stat-базис спайка B и парсер строк модов.

Один и тот же парсер используется (а) для инъекции пробных модов при замере маржиналов
и (б) для разбора реальных аффиксов пула PoB в вектор stat-вкладов. Диапазоны (min-max)
сворачиваем в max-roll (верхняя граница тира) — это «идеальный ролл», грубая cost-модель
спайка (D: марковский крафт не моделируем).
"""

from __future__ import annotations

import re

# Stat-ключи. OFFENSE идут в цель прокси, DEFENSE — в ограничения.
OFFENSE = [
    "inc_spell_dmg", "inc_elem_dmg", "inc_light_dmg", "inc_cold_dmg", "inc_fire_dmg",
    "inc_phys_dmg", "inc_chaos_dmg", "added_light_spell", "added_cold_spell",
    "added_fire_spell", "added_chaos_spell", "cast_speed", "crit_chance",
    "crit_multi", "dot_multi", "plus_spell_gem", "plus_all_gem", "pen_elem",
    # атакующие (для билдов-атак / конверсии, напр. 1b Frost Blades)
    "added_light_attack", "added_cold_attack", "added_fire_attack",
    "added_phys_attack", "added_chaos_attack", "attack_speed",
]
RES = ["fire_res", "cold_res", "light_res", "chaos_res"]
ATTR = ["str", "dex", "int"]
DEFENSE = ["life", "inc_life", "es", "inc_es", "mana", *RES, *ATTR]

ALL = OFFENSE + DEFENSE

# Пробы для маржиналов: (stat_key, строка-мод для инъекции, величина).
# Строки должны быть понятны парсеру модов PoB.
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
    # атрибуты как ПОТЕНЦИАЛЬНЫЙ источник урона (build 10: "inc Damage per X Int").
    # Если маржинал ~0 — солвер их и не возьмёт; если >0 — учтёт честно.
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

# Подмножество stat-ключей, идущих в ЦЕЛЬ (offense + атрибуты как возможный урон).
OBJECTIVE = OFFENSE + ATTR

_TAG = re.compile(r"^\{[^}]*\}\s*")


def _last(m: re.Match, *groups) -> float:
    """Из (min-max) берём max-roll (последнюю числовую группу)."""
    vals = [float(m.group(g)) for g in groups if m.group(g) is not None]
    return vals[-1] if vals else 0.0


# Правила: (compiled regex, lambda m -> dict[stat, value]). Порядок важен —
# более специфичные раньше общих. Диапазоны -> max-roll.
_R = lambda p: re.compile(p, re.IGNORECASE)
_NUM = r"\(?(\d+(?:\.\d+)?)(?:-(\d+(?:\.\d+)?))?\)?"

_RULES: list[tuple[re.Pattern, callable]] = [
    # added flat to spells: "Adds (a-b) to (c-d) X Damage to Spells"
    (_R(r"Adds " + _NUM + r" to " + _NUM + r" (Lightning|Cold|Fire|Chaos) Damage to Spells"),
     lambda m: {f"added_{m.group(5).lower()}_spell": (_last_pair(m))}),
    # added flat to attacks: "Adds (a-b) to (c-d) X Damage to Attacks"
    (_R(r"Adds " + _NUM + r" to " + _NUM + r" (Lightning|Cold|Fire|Physical|Chaos) Damage to Attacks"),
     lambda m: {f"added_{m.group(5).lower()}_attack": (_last_pair(m))}),
    # added flat без квалификатора (кольца/амулеты): трактуем как к атакам (наиболее частый кейс гира)
    (_R(r"Adds " + _NUM + r" to " + _NUM + r" (Lightning|Cold|Fire|Physical|Chaos) Damage$"),
     lambda m: {f"added_{m.group(5).lower()}_attack": (_last_pair(m))}),
    # attack speed
    (_R(_NUM + r"% increased Attack Speed"), lambda m: {"attack_speed": _last(m, 1, 2)}),
    # gem levels
    (_R(r"\+(\d+) to Level of all Spell Skill Gems"), lambda m: {"plus_spell_gem": float(m.group(1))}),
    (_R(r"\+(\d+) to Level of all Skill Gems"), lambda m: {"plus_all_gem": float(m.group(1))}),
    (_R(r"\+(\d+) to Level of all (Lightning|Cold|Fire|Chaos|Physical) Spell Skill Gems"),
     lambda m: {"plus_spell_gem": float(m.group(1))}),
    # crit
    (_R(r"\+" + _NUM + r"% to Critical Strike Multiplier for Spells"), lambda m: {"crit_multi": _last(m, 1, 2)}),
    (_R(r"\+" + _NUM + r"% to Global Critical Strike Multiplier"), lambda m: {"crit_multi": _last(m, 1, 2)}),
    (_R(r"\+" + _NUM + r"% to Critical Strike Multiplier"), lambda m: {"crit_multi": _last(m, 1, 2)}),
    (_R(_NUM + r"% increased Critical Strike Chance for Spells"), lambda m: {"crit_chance": _last(m, 1, 2)}),
    (_R(_NUM + r"% increased Global Critical Strike Chance"), lambda m: {"crit_chance": _last(m, 1, 2)}),
    (_R(_NUM + r"% increased Critical Strike Chance"), lambda m: {"crit_chance": _last(m, 1, 2)}),
    # cast speed
    (_R(_NUM + r"% increased Cast Speed"), lambda m: {"cast_speed": _last(m, 1, 2)}),
    # dot multi
    (_R(r"\+" + _NUM + r"% to Damage over Time Multiplier"), lambda m: {"dot_multi": _last(m, 1, 2)}),
    # penetration
    (_R(r"Damage Penetrates " + _NUM + r"% Elemental Resistances"), lambda m: {"pen_elem": _last(m, 1, 2)}),
    (_R(r"Damage Penetrates " + _NUM + r"% (Lightning|Cold|Fire) Resistance"), lambda m: {"pen_elem": _last(m, 1, 2)}),
    # increased damage (specific then generic)
    (_R(_NUM + r"% increased Spell Damage"), lambda m: {"inc_spell_dmg": _last(m, 1, 2)}),
    (_R(_NUM + r"% increased Elemental Damage with Attack Skills"), lambda m: {}),  # attack-only: игнор для спеллов
    (_R(_NUM + r"% increased Elemental Damage"), lambda m: {"inc_elem_dmg": _last(m, 1, 2)}),
    (_R(_NUM + r"% increased Lightning Damage"), lambda m: {"inc_light_dmg": _last(m, 1, 2)}),
    (_R(_NUM + r"% increased Cold Damage"), lambda m: {"inc_cold_dmg": _last(m, 1, 2)}),
    (_R(_NUM + r"% increased Fire Damage"), lambda m: {"inc_fire_dmg": _last(m, 1, 2)}),
    (_R(_NUM + r"% increased Physical Damage"), lambda m: {"inc_phys_dmg": _last(m, 1, 2)}),
    (_R(_NUM + r"% increased Chaos Damage"), lambda m: {"inc_chaos_dmg": _last(m, 1, 2)}),
    # resists (combined first)
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
    # attributes
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
    # life / es / mana
    (_R(_NUM + r"% increased maximum Life"), lambda m: {"inc_life": _last(m, 1, 2)}),
    (_R(r"\+" + _NUM + r" to maximum Life"), lambda m: {"life": _last(m, 1, 2)}),
    (_R(_NUM + r"% increased maximum Energy Shield"), lambda m: {"inc_es": _last(m, 1, 2)}),
    (_R(r"\+" + _NUM + r" to maximum Energy Shield"), lambda m: {"es": _last(m, 1, 2)}),
    (_R(r"\+" + _NUM + r" to maximum Mana"), lambda m: {"mana": _last(m, 1, 2)}),
]


def _last_pair(m: re.Match) -> float:
    """'Adds (a-b) to (c-d) ...': средний флэт по max-роллу = (maxA + maxB)/2."""
    a = float(m.group(2) if m.group(2) is not None else m.group(1))
    b = float(m.group(4) if m.group(4) is not None else m.group(3))
    return (a + b) / 2.0


def parse_mod_line(line: str) -> dict[str, float]:
    """Строка мода -> вектор stat-вкладов (max-roll). Неузнанное -> {}."""
    s = _TAG.sub("", line).strip()
    for rx, fn in _RULES:
        m = rx.match(s)
        if m:
            return {k: v for k, v in fn(m).items() if v}
    return {}


def parse_item_stats(explicit_lines: list[str]) -> dict[str, float]:
    """Суммарный stat-вектор набора эксплицитов."""
    out: dict[str, float] = {}
    for ln in explicit_lines:
        for k, v in parse_mod_line(ln).items():
            out[k] = out.get(k, 0.0) + v
    return out
