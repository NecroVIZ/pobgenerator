"""Диагностика загрузки реального билда: дерево/предметы/скилл."""

from __future__ import annotations

from pathlib import Path

from poebuildgen import pobcode
from poebuildgen.headless import PobHeadless

CODE_FILE = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "user_build.txt"


def main() -> None:
    xml = pobcode.decode(CODE_FILE.read_text(encoding="utf-8").strip()).decode("utf-8")
    pob = PobHeadless()
    pob.load_build_xml(xml, "diag")
    lua = pob.lua

    def ev(expr: str):
        return lua.eval(expr)

    print("promptMsg:", ev("mainObject and mainObject.promptMsg or 'none'"))
    print("class:", ev("build.spec and build.spec.curClassName or '?'"),
          "/", ev("build.spec and build.spec.curAscendClassName or '?'"))
    print("tree version:", ev("build.spec and build.spec.treeVersion or '?'"))
    print("alloc nodes:", lua.eval(
        "(function() local n=0 if build.spec and build.spec.allocNodes then "
        "for _ in pairs(build.spec.allocNodes) do n=n+1 end end return n end)()"))
    print("active spec count:", lua.eval(
        "(function() local n=0 if build.treeTab and build.treeTab.specList then "
        "for _ in pairs(build.treeTab.specList) do n=n+1 end end return n end)()"))
    print("items count:", lua.eval(
        "(function() local n=0 if build.itemsTab and build.itemsTab.items then "
        "for _ in pairs(build.itemsTab.items) do n=n+1 end end return n end)()"))
    print("equipped slots:", lua.eval(
        "(function() local n=0 if build.itemsTab and build.itemsTab.slots then "
        "for _,s in pairs(build.itemsTab.slots) do if s.selItemId and s.selItemId~=0 then n=n+1 end end end return n end)()"))
    print("socket groups:", lua.eval(
        "(function() local n=0 if build.skillsTab and build.skillsTab.socketGroupList then "
        "for _ in pairs(build.skillsTab.socketGroupList) do n=n+1 end end return n end)()"))
    print("main skill:", lua.eval(
        "(build.calcsTab and build.calcsTab.mainEnv and build.calcsTab.mainEnv.player "
        "and build.calcsTab.mainEnv.player.mainSkill and build.calcsTab.mainEnv.player.mainSkill.activeEffect "
        "and build.calcsTab.mainEnv.player.mainSkill.activeEffect.grantedEffect "
        "and build.calcsTab.mainEnv.player.mainSkill.activeEffect.grantedEffect.name) or '?'"))
    print("TotalDPS:", pob.stat("TotalDPS"), "Life:", pob.stat("Life"))


if __name__ == "__main__":
    main()
