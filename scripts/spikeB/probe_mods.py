"""Разведка пулов модов в данных PoB: какие группы (data.itemMods.*), сколько,
и как устроена одна запись (type/affix/group/level/строки/веса по тегам).

Запускаем один экземпляр PobHeadless (standalone-процесс), печатаем сводку и образцы.
"""

from poebuildgen.headless import PobHeadless, _lua_to_py

PROBE = r"""
function()
  local r = { groups = {}, sample = {} }
  for k, v in pairs(data.itemMods) do
    local n = 0
    for _ in pairs(v) do n = n + 1 end
    r.groups[k] = n
  end
  -- образцы из основной таблицы аффиксов предметов
  local src = data.itemMods.Item
  if src then
    local cnt = 0
    for key, m in pairs(src) do
      cnt = cnt + 1
      if cnt <= 6 then
        local lines = {}
        for i = 1, 6 do if m[i] then lines[#lines+1] = m[i] end end
        local wk = {}
        if m.weightKey then for i = 1, math.min(5, #m.weightKey) do
          wk[#wk+1] = (m.weightKey[i] or "?") .. "=" .. tostring(m.weightVal and m.weightVal[i])
        end end
        table.insert(r.sample, {
          key = tostring(key), affix = m.affix, type = m.type, group = m.group,
          level = m.level, lines = lines, weights = wk,
        })
      end
    end
  end
  return r
end
"""


def main():
    pob = PobHeadless()
    pob.new_build()
    out = _lua_to_py(pob.eval(PROBE)())
    print("== data.itemMods groups (name -> count) ==")
    groups = out.get("groups", {})
    for k in sorted(groups, key=lambda x: -groups[x]):
        print(f"  {k:<24} {groups[k]}")
    print("\n== sample entries from data.itemMods.Item ==")
    for s in out.get("sample", []):
        print(f"\n  affix={s.get('affix')!r} type={s.get('type')} group={s.get('group')} level={s.get('level')}")
        for ln in s.get("lines", []):
            print(f"     | {ln}")
        if s.get("weights"):
            print(f"     weights: {s['weights']}")


if __name__ == "__main__":
    main()
