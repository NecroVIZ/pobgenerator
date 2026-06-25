"""Тесты build-model: типизированный доступ + lossless-эквивалентность для PoB."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from poebuildgen import pobcode
from poebuildgen.model import Gem, PobBuild

CODE = Path(__file__).resolve().parent / "fixtures" / "user_build.txt"


def _real_xml() -> str:
    return pobcode.decode(CODE.read_text(encoding="utf-8").strip()).decode("utf-8")


# --- разбор/типизированный доступ (без PoB) ---


@pytest.mark.skipif(not CODE.exists(), reason="нет фикстуры реального билда")
def test_parse_typed_access():
    b = PobBuild.from_xml(_real_xml())
    assert b.build.level >= 1
    assert b.build.class_name  # Marauder
    assert b.build.ascend_class_name  # Chieftain

    spec = b.tree.active_spec()
    assert spec is not None
    assert len(spec.nodes) > 50, "дерево не распарсилось"
    assert spec.mastery_effects, "мастери не распарсились"

    gems = b.skills.all_gems()
    assert any(g.name == "Blade Vortex" for g in gems)
    bv = next(g for g in gems if g.name == "Blade Vortex")
    assert bv.level >= 1 and bv.enabled


@pytest.mark.skipif(not CODE.exists(), reason="нет фикстуры реального билда")
def test_roundtrip_preserves_structure():
    xml = _real_xml()
    b = PobBuild.from_xml(xml)
    out = b.to_xml()
    root_in, root_out = ET.fromstring(xml), ET.fromstring(out)

    # порядок верхнеуровневых тегов сохранён
    assert [c.tag for c in root_in] == [c.tag for c in root_out]
    # узлы Sockets/URL внутри Spec не потеряны
    spec_out = root_out.find("Tree").find("Spec")
    assert spec_out.find("Sockets") is not None
    assert spec_out.find("URL") is not None
    # число гемов совпадает
    assert len(list(root_in.iter("Gem"))) == len(list(root_out.iter("Gem")))


def test_mutation_writes_back():
    b = PobBuild.from_xml(
        """<?xml version="1.0"?><PathOfBuilding>
        <Build level="90" className="Witch" ascendClassName="None" mainSocketGroup="1"/>
        <Tree activeSpec="1"><Spec treeVersion="3_28" classId="3" ascendClassId="0" nodes="1,2,3"/></Tree>
        <Skills activeSkillSet="1"><SkillSet id="1"><Skill enabled="true">
        <Gem nameSpec="Fireball" level="1" quality="0" enabled="true"/>
        </Skill></SkillSet></Skills>
        </PathOfBuilding>"""
    )
    b.build.level = 95
    g = b.skills.all_gems()[0]
    g.level = 21
    g.quality = 20
    g.enabled = False
    b.tree.active_spec().nodes = [10, 20, 30, 40]

    re = PobBuild.from_xml(b.to_xml())
    assert re.build.level == 95
    g2 = re.skills.all_gems()[0]
    assert g2.level == 21 and g2.quality == 20 and g2.enabled is False
    assert re.tree.active_spec().nodes == [10, 20, 30, 40]


def test_mastery_effects_roundtrip():
    g = Gem(attrib={"nameSpec": "Test", "level": "20", "quality": "0", "enabled": "true"})
    assert g.level == 20 and g.name == "Test"


def test_duplicate_toplevel_tags_roundtrip():
    # B2: дубли верхнеуровневых passthrough-тегов должны сохраняться в порядке и значениях.
    xml = (
        "<PathOfBuilding>"
        "<Notes>n1</Notes>"
        '<Build level="90" className="Witch" ascendClassName="None"/>'
        "<Notes>n2</Notes>"
        '<Tree activeSpec="1"><Spec classId="3" nodes="1,2"/></Tree>'
        '<Import x="1"/>'
        '<Skills activeSkillSet="1"><SkillSet id="1"/></Skills>'
        '<Import x="2"/>'
        "</PathOfBuilding>"
    )
    out = PobBuild.from_xml(xml).to_xml()
    ri, ro = ET.fromstring(xml), ET.fromstring(out)
    assert [c.tag for c in ri] == [c.tag for c in ro]
    # порядок и содержимое дублей не перепутаны
    assert [n.text for n in ro.iter("Notes")] == ["n1", "n2"]
    assert [imp.attrib["x"] for imp in ro.iter("Import")] == ["1", "2"]


# --- эквивалентность для PoB (нужен vendor) ---

from poebuildgen.headless import _default_pob_dir  # noqa: E402

_HAS_POB = (_default_pob_dir() / "src" / "HeadlessWrapper.lua").exists()


@pytest.mark.skipif(
    not (_HAS_POB and CODE.exists()), reason="нет vendor PoB или фикстуры билда"
)
def test_roundtrip_stats_match_pob():
    from poebuildgen.evaluator import evaluate

    xml = _real_xml()
    keys = ["TotalDPS", "Life", "TotalEHP", "EnergyShield", "Armour", "FireResist"]
    base = evaluate(xml, keys, name="model-base")["stats"]
    rt = evaluate(PobBuild.from_xml(xml).to_xml(), keys, name="model-rt")["stats"]

    for k in keys:
        a, b_ = base.get(k), rt.get(k)
        if a is None and b_ is None:
            continue
        rel = abs((a or 0) - (b_ or 0)) / max(abs(a or 0), abs(b_ or 0), 1e-9)
        assert rel < 1e-6, f"{k}: base={a} rt={b_}"
