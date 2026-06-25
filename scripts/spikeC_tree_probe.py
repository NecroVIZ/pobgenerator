"""Зонд дерева PoB: какие данные/функции доступны для построения дерева.

Цель спайка D14 — понять, что PoB-data даёт для:
  - графа дерева (узлы + связи),
  - статов notables (для target-set),
  - стартовой точки класса,
  - мастерей,
чтобы спроектировать Dijkstra-stitching.
"""
from __future__ import annotations
from poebuildgen.headless import PobHeadless, _lua_to_py

PROBE = r"""
function()
  local out = {}
  if not build or not build.spec or not build.spec.tree then out.err="no spec.tree"; return out end

  -- nodeCalculator: PoB-овская встроенная оценка силы узлов.
  -- Узнать сигнатуру: продампить entry1 (функция) — что она ждёт?
  if build.calcsTab and build.calcsTab.nodeCalculator then
    local nc = build.calcsTab.nodeCalculator
    out.nodeCalc_entry1_type = type(nc[1])
    out.nodeCalc_entry2_type = type(nc[2])
    -- entry2 — таблица; что в ней?
    if type(nc[2]) == "table" then
      out.nodeCalc2_keys = {}
      for k, _ in pairs(nc[2]) do out.nodeCalc2_keys[#out.nodeCalc2_keys+1] = tostring(k) end
    end
    -- попробуем вызвать nc[1] (билд загружен) разными способами
    if type(nc[1]) == "function" then
      -- nodeCalculator[1] требует подготовленный env (внутренний PoB-флоу).
      -- Найдём sample- notable для последующих маржинальных тестов.
      local sample_id = nil
      for nid, nd in pairs(build.spec.tree.nodes) do
        if type(nd)=="table" and nd.type=="Notable" and nd.sd and #nd.sd>0 then sample_id = nid; break end
      end
      out.sample_notable_id = sample_id
      out.nc1_unusable = "требует внутренний PoB-calcs-env, нельзя вызвать напрямую"
    end

    -- nodeCalculator[2] — таблица; что в ней (может быть кэш сил узлов)?
    if type(nc[2]) == "table" then
      out.nc2_count = 0
      out.nc2_sample = {}
      local numeric_vals = {}
      local total_num = 0
      local sum = 0
      local mn, mx
      for k, v in pairs(nc[2]) do
        out.nc2_count = out.nc2_count + 1
        if out.nc2_count <= 5 then
          out.nc2_sample[tostring(k)] = type(v) == "table" and "<table>" or tostring(v)
        end
        -- статистика по числовым значениям
        if type(v) == "number" then
          total_num = total_num + 1
          sum = sum + v
          if not mn or v < mn then mn = v end
          if not mx or v > mx then mx = v end
        end
        -- если значения — таблицы, дампнем первую целиком
        if out.nc2_count == 1 and type(v) == "table" then
          local row = {}
          for rk, rv in pairs(v) do
            row[tostring(rk)] = type(rv)=="table" and "<table>" or tostring(rv)
          end
          out.nc2_first_full = row
        end
      end
      if total_num > 0 then
        out.nc2_numeric = { count=total_num, mean=sum/total_num, min=mn, max=mx }
      end
      -- Найти «силу» sample- notable (13922) в кэше
      local sv = nc[2][13922] or nc[2]["13922"]
      out.nc2_sample_notable_val = sv and (type(sv)=="table" and "<table>" or tostring(sv)) or "absent"
    end
  end

  --_allocated в реальном билде: dump notables + keystones
  local nodes = build.spec.tree.nodes
  local alloc = build.spec.allocNodes or {}
  out.allocated_total = 0
  out.allocated_notables = {}
  out.allocated_keystones = {}
  out.allocated_sockets = 0
  out.allocated_normal = 0
  for id, _ in pairs(alloc) do
    out.allocated_total = out.allocated_total + 1
    local nd = nodes[id]
    if type(nd) == "table" then
      if nd.type == "Notable" then
        out.allocated_notables[#out.allocated_notables+1] = { id=id, dn=nd.dn, sd=nd.sd }
      elseif nd.type == "Keystone" then
        out.allocated_keystones[#out.allocated_keystones+1] = { id=id, dn=nd.dn, sd=nd.sd }
      elseif nd.type == "Socket" then
        out.allocated_sockets = out.allocated_sockets + 1
      elseif nd.type == "Normal" then
        out.allocated_normal = out.allocated_normal + 1
      end
    end
  end

  -- класс/аскенданси загруженного билда
  out.curClass = build.spec.curClassName
  out.curAscend = build.spec.curAscendClassName
  out.points_budget = build.spec.tree.points  -- суммарный бюджет очков в дереве
  out.points_used = out.allocated_total
  return out
end
"""


def main() -> None:
    import sys
    p = PobHeadless()
    # если передан путь к билду — загрузить его (эталон), иначе пустой
    if len(sys.argv) > 1:
        from poebuildgen import pobcode
        code = open(sys.argv[1]).read().strip()
        xml = pobcode.decode(code).decode("utf-8")
        p.load_build_xml(xml)
        print(f"[loaded {sys.argv[1]}]", file=sys.stderr)
    else:
        p.new_build()
    fn = p.eval(PROBE)
    res = _lua_to_py(fn())
    import json
    print(json.dumps(res, indent=2, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
