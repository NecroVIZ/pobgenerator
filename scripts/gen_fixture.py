"""Сгенерировать эталонный экспорт-фикстур (PoB-XML с вшитыми PlayerStat)."""

from __future__ import annotations

from pathlib import Path

from poebuildgen.headless import PobHeadless

INPUT_XML = """<?xml version="1.0" encoding="UTF-8"?>
<PathOfBuilding>
<Build level="90" targetVersion="3_0" className="Witch" ascendClassName="None" mainSocketGroup="1" viewMode="CALCS"/>
<Skills activeSkillSet="1">
 <SkillSet id="1">
  <Skill enabled="true" mainActiveSkill="1">
   <Gem nameSpec="Fireball" level="20" quality="20" enabled="true"/>
  </Skill>
 </SkillSet>
</Skills>
<Tree activeSpec="1"><Spec treeVersion="3_28" classId="3" ascendClassId="0"/></Tree>
<Items/>
<Config/>
</PathOfBuilding>
"""

OUT = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "witch_fireball.pob.xml"


def main() -> None:
    pob = PobHeadless()
    pob.load_build_xml(INPUT_XML, "fireball")
    xml = pob.export_xml()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(xml, encoding="utf-8")
    print("wrote", OUT, len(xml), "chars; TotalDPS=", pob.stat("TotalDPS"))


if __name__ == "__main__":
    main()
