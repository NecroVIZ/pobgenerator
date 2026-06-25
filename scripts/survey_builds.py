"""Обзор папки builds/: класс, аскенд, главный скилл, уровень, ключевые статы (offline-разбор)."""

import xml.etree.ElementTree as ET
from pathlib import Path

from poebuildgen import pobcode

BUILDS = Path("builds")


def main_skill(root) -> str:
    skills = root.find("Skills")
    if skills is None:
        return "?"
    active_set = skills.get("activeSkillSet", "1")
    sset = None
    for s in skills.findall("SkillSet"):
        if s.get("id") == active_set:
            sset = s
            break
    sset = sset or (skills.find("SkillSet") if skills.find("SkillSet") is not None else skills)
    main_grp = root.find("Build").get("mainSocketGroup", "1")
    groups = sset.findall("Skill")
    try:
        grp = groups[int(main_grp) - 1]
    except (ValueError, IndexError):
        grp = groups[0] if groups else None
    if grp is None:
        return "?"
    gems = grp.findall("Gem")
    # главный активный — обычно не support; берём первый не-support по имени
    for g in gems:
        nm = g.get("nameSpec", "")
        if nm and "Support" not in (g.get("skillId") or ""):
            return nm
    return gems[0].get("nameSpec", "?") if gems else "?"


def stat(root, name):
    for ps in root.iter("PlayerStat"):
        if ps.get("stat") == name:
            try:
                return float(ps.get("value"))
            except (TypeError, ValueError):
                return None
    return None


def main():
    for f in sorted(BUILDS.glob("*.txt"), key=lambda p: int(p.stem) if p.stem.isdigit() else 999):
        try:
            xml = pobcode.decode(f.read_text(encoding="utf-8").strip()).decode("utf-8")
            root = ET.fromstring(xml)
            b = root.find("Build")
            cls = b.get("className")
            asc = b.get("ascendClassName")
            lvl = b.get("level")
            skill = main_skill(root)
            dps = stat(root, "TotalDPS") or stat(root, "CombinedDPS") or stat(root, "FullDPS")
            life = stat(root, "Life")
            es = stat(root, "EnergyShield")
            dps_s = f"{dps:,.0f}" if dps else "?"
            print(f"{f.name:>7} | {cls:<8} {asc:<12} L{lvl:<3} | {skill:<22} | DPS {dps_s:>15} | Life {life or 0:.0f} ES {es or 0:.0f}")
        except Exception as exc:  # noqa: BLE001
            print(f"{f.name:>7} | ОШИБКА: {type(exc).__name__}: {exc}")


if __name__ == "__main__":
    main()
