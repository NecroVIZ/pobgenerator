"""Пул аффиксов из данных PoB для конкретной базы предмета.

Берём data.itemMods.Item, фильтруем по: type in {Prefix,Suffix}, level<=ilvl,
spawn-вес>0 для тегов базы (eligibility как в PoB). На один group оставляем
максимальный доступный тир (≤ilvl) — это «идеальный ролл» (грубая cost-модель спайка).
Строки модов парсим в stat-вектор (stats.parse_mod_line); моды без отслеживаемых
статов отбрасываем (для цели/ограничений они шум).
"""

from __future__ import annotations

from dataclasses import dataclass

from scripts.spikeB.stats import parse_item_stats

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
    type: str            # "Prefix" / "Suffix"
    group: str
    level: int
    lines: list[str]
    stats: dict[str, float]


def _max_roll_lines(lines: list[str]) -> list[str]:
    """В строках мода диапазоны (a-b) заменяем на b (max-roll) — для записи в предмет."""
    import re
    def repl(m):
        return m.group(2)
    out = []
    for ln in lines:
        out.append(re.sub(r"\((\d+(?:\.\d+)?)-(\d+(?:\.\d+)?)\)", repl, ln))
    return out


class ModPool:
    def __init__(self, engine):
        self._fn = engine.pob.eval(_POOL_LUA)
        self._cache: dict[tuple[str, int], list[Affix]] = {}

    def for_base(self, base: str, ilvl: int) -> list[Affix]:
        from poebuildgen.headless import _lua_to_py
        key = (base, ilvl)
        if key in self._cache:
            return self._cache[key]
        rows = _lua_to_py(self._fn(base.encode("utf-8"), ilvl))
        rows = rows if isinstance(rows, list) else []
        best: dict[str, Affix] = {}  # group -> лучший (max level) тир с отслеживаемыми статами
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


if __name__ == "__main__":
    import sys

    from scripts.spikeB.engine import Engine
    from scripts.spikeB.harness import Build

    b = Build.load(sys.argv[1] if len(sys.argv) > 1 else "builds/10.txt")
    eng = Engine()
    pool = ModPool(eng)
    for slot in b.rare_core_slots():
        it = b.item_for_slot(slot)
        p = pool.for_base(it.base, it.item_level)
        npre = sum(1 for a in p if a.type == "Prefix")
        nsuf = sum(1 for a in p if a.type == "Suffix")
        print(f"{slot:<12} base={it.base!r} ilvl={it.item_level}: {len(p)} affix-groups ({npre}p/{nsuf}s)")
