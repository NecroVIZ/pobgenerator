"""Проверка: загрузить билд со скиллом (Witch + Fireball) и получить ненулевой DPS."""

from __future__ import annotations

from poebuildgen.headless import PobHeadless

XML = """<?xml version="1.0" encoding="UTF-8"?>
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

KEYS = ["Life", "Mana", "AverageDamage", "TotalDPS", "Speed", "FireResist"]


def main() -> None:
    pob = PobHeadless()
    pob.load_build_xml(XML, "fireball-test")
    print("--- Witch + Fireball ---")
    for k, v in pob.stats(KEYS).items():
        print(f"  {k:16} = {v}")

    xml_out = pob.export_xml()
    print("export length:", len(xml_out))
    print("contains PlayerStat:", "PlayerStat" in xml_out)
    print("contains Fireball:", "Fireball" in xml_out)


if __name__ == "__main__":
    main()
