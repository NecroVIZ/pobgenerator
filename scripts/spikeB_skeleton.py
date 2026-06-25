"""Спайк B, шаг 0: фиксированный скелет 1a (Inquisitor/Arc) считается headless и
масштабируется от шмота. Дерево здесь НЕ предмет проверки — берём минимальное.

Проверяем: (1) Arc-скилл разрешается, TotalDPS > 0; (2) добавление +lightning к урону
через фейковый предмет двигает DPS (значит оптимизация шмота осмысленна).
"""

from poebuildgen.evaluator import evaluate

BASE = """<?xml version="1.0"?>
<PathOfBuilding>
<Build level="90" className="Templar" ascendClassName="Inquisitor" mainSocketGroup="1"/>
<Skills activeSkillSet="1"><SkillSet id="1">
 <Skill enabled="true" mainActiveSkill="1" slot="Weapon 1">
  <Gem nameSpec="Arc" level="20" quality="20" enabled="true"/>
  <Gem nameSpec="Added Lightning Damage" level="20" quality="0" enabled="true"/>
  <Gem nameSpec="Spell Echo" level="20" quality="0" enabled="true"/>
  <Gem nameSpec="Controlled Destruction" level="20" quality="0" enabled="true"/>
 </Skill>
</SkillSet></Skills>
<Tree activeSpec="1"><Spec treeVersion="3_28" classId="5" ascendClassId="1" nodes=""/></Tree>
<Items{items_attr}>{items}</Items>
<Config>
 <Input name="enemyIsBoss" boolean="false"/>
</Config>
</PathOfBuilding>"""

RING_TEMPLATE = (
    '<Item id="1">Rarity: RARE\nTest Ring\nTwo-Stone Ring\n'
    "Implicits: 0\n"
    "Adds {lo} to {hi} Lightning Damage to Spells\n"
    "</Item>"
)


def build_xml(added=None):
    if added is None:
        return BASE.format(items_attr="", items="")
    lo, hi = added
    item = RING_TEMPLATE.format(lo=lo, hi=hi)
    items = f'\n {item}\n <Slot name="Ring 1" itemId="1"/>\n'
    return BASE.format(items_attr=' activeItemSet="1"', items=items)


def main():
    keys = ["TotalDPS", "ManaUnreserved", "LightningResist"]
    r0 = evaluate(build_xml(), keys, name="spikeB-base")
    print("base   :", r0["stats"], "| ver", r0.get("version"))

    r1 = evaluate(build_xml((50, 90)), keys, name="spikeB-ring")
    print("+ring  :", r1["stats"])

    d0 = r0["stats"].get("TotalDPS") or 0
    d1 = r1["stats"].get("TotalDPS") or 0
    print(f"DPS base={d0:.0f} ring={d1:.0f} delta={d1 - d0:.0f} ({'OK' if d1 > d0 else 'NO SCALING'})")


if __name__ == "__main__":
    main()
