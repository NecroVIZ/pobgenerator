"""Headless-воркер PoB. Два режима:

1) Одноразовый (один процесс = одна оценка) — для изоляции состояния (DESIGN-v2 §6):
     python -m poebuildgen.worker <in.json> <out.json>
   Результат пишем в файл, т.к. stdout засоряется логами PoB.

2) Персистентный (тёплый воркер для пула, throughput ×N по ядрам):
     python -m poebuildgen.worker --serve <ready_marker>
   PoB грузится ОДИН раз; затем цикл: читаем из stdin путь к запросу <p>.json,
   пишем ответ <p>.out и маркер <p>.done. Синхронизация по файлам, а не по stdout
   (его засоряет PoB). Тёплый инстанс безопасен: один PobHeadless на процесс
   выдерживает сотни load_build_xml (проверено спайками A/B, gold-match стабилен).

Запрос: {"xml": <str|null>, "stats": [..], "want_export"/"want_validate"/"want_audit": bool, "name": str}
Ответ:  {"ok": bool, "version": str, "stats": {..}, ["export"|"validation"|"audit"], ["error"]}
"""

from __future__ import annotations

import json
import os
import sys


def _coerce(v):
    if isinstance(v, (int, float)) or v is None:
        return v
    try:
        return float(v)
    except (TypeError, ValueError):
        return str(v)


def _run_one(pob, req: dict) -> dict:
    if req.get("xml"):
        pob.load_build_xml(req["xml"], req.get("name", "worker"), fingerprint=req.get("fingerprint"))
    else:
        pob.new_build()
    stats = {k: _coerce(v) for k, v in pob.stats(req.get("stats", [])).items()}
    result = {"ok": True, "version": pob.pob_version(), "stats": stats}
    if req.get("want_export"):
        result["export"] = pob.export_xml()
    if req.get("want_validate"):
        result["validation"] = pob.validate()
    if req.get("want_audit"):
        result["audit"] = pob.audit_data()
    if req.get("want_tree_graph"):
        from poebuildgen.realizer.tree import load_tree_graph
        graph = load_tree_graph(pob)
        result["tree_graph"] = {
            "nodes": {nid: {"type": n.type, "dn": n.dn, "linked": n.linked, "sd": n.sd, "ascendancy": n.ascendancy}
                      for nid, n in graph.nodes.items()},
            "class_start": graph.class_start,
            "cur_class": graph.cur_class,
            "cur_ascend": graph.cur_ascend,
            "class_id": graph.class_id,
            "allocated": list(graph.allocated),
            "points_total": graph.points_total,
            "points_ascend": graph.points_ascend,
        }
    if req.get("want_mod_pools"):
        from poebuildgen.realizer.gear import _POOL_LUA
        from poebuildgen.headless import _lua_to_py
        fn = pob.eval(_POOL_LUA)
        pools = []
        for item in req["want_mod_pools"]:
            base = item["base"]
            ilvl = item["ilvl"]
            rows = _lua_to_py(fn(base.encode("utf-8"), ilvl))
            pools.append({"base": base, "ilvl": ilvl, "rows": rows if isinstance(rows, list) else []})
        result["mod_pools"] = pools
    return result


def _serve(ready_marker: str) -> None:
    """Персистентный режим: PoB один раз, дальше задачи из stdin."""
    from poebuildgen.headless import PobHeadless

    pob = PobHeadless()
    pob.new_build()
    # сигнал готовности — отдельным файлом (stdout засорён логами PoB)
    with open(ready_marker, "w", encoding="utf-8") as f:
        f.write("ready")

    for line in sys.stdin:
        path = line.strip()
        if not path or path == "QUIT":
            break
        try:
            with open(path, encoding="utf-8") as f:
                req = json.load(f)
            result = _run_one(pob, req)
        except Exception as exc:  # noqa: BLE001 — диагностика в ответ, воркер живёт дальше
            result = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        tmp = path + ".out.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(result, f)
        os.replace(tmp, path + ".out")  # атомарно: .out появляется уже целым
        with open(path + ".done", "w", encoding="utf-8") as f:
            f.write("1")


def main() -> None:
    if len(sys.argv) >= 3 and sys.argv[1] == "--serve":
        _serve(sys.argv[2])
        return

    in_path, out_path = sys.argv[1], sys.argv[2]
    with open(in_path, encoding="utf-8") as f:
        req = json.load(f)
    try:
        from poebuildgen.headless import PobHeadless

        pob = PobHeadless()
        result = _run_one(pob, req)
    except Exception as exc:  # noqa: BLE001
        result = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(result, f)
    sys.exit(0 if result.get("ok") else 1)


if __name__ == "__main__":
    main()
