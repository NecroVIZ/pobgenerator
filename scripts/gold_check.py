"""Золотой тест Spike A: реальный билд-код -> headless-пересчёт vs вшитые GUI PlayerStat."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

from poebuildgen import pobcode
from poebuildgen.evaluator import evaluate

CODE_FILE = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "user_build.txt"


def main() -> None:
    code = CODE_FILE.read_text(encoding="utf-8").strip()
    xml_bytes = pobcode.decode(code)
    xml = xml_bytes.decode("utf-8")
    print(f"decoded XML: {len(xml)} chars")

    root = ET.fromstring(xml)
    build_el = root.find("Build")
    if build_el is not None:
        a = build_el.attrib
        print("class:", a.get("className"), "/", a.get("ascendClassName"),
              "| level:", a.get("level"), "| mainSocketGroup:", a.get("mainSocketGroup"))

    embedded = {}
    for ps in root.iter("PlayerStat"):
        try:
            embedded[ps.attrib["stat"]] = float(ps.attrib["value"])
        except (KeyError, ValueError):
            pass
    print(f"embedded PlayerStat: {len(embedded)}")

    res = evaluate(xml, list(embedded.keys()), name="gold")
    print("PoB version:", res["version"])
    recomputed = res["stats"]

    print(f"\n{'stat':28} {'GUI (embedded)':>18} {'headless':>18} {'rel%':>9}  ok")
    print("-" * 86)
    within, total = 0, 0
    worst = []
    for k in sorted(embedded.keys()):
        gui = embedded[k]
        hl = recomputed.get(k)
        if hl is None:
            print(f"{k:28} {gui:>18.4g} {'<none>':>18} {'-':>9}  --")
            continue
        total += 1
        rel = abs(gui - hl) / max(abs(gui), abs(hl), 1e-9)
        ok = rel <= 0.01
        within += ok
        worst.append((rel, k, gui, hl))
        print(f"{k:28} {gui:>18.4g} {hl:>18.4g} {rel*100:>8.3f}%  {'OK' if ok else 'XX'}")

    print("-" * 86)
    print(f"within +/-1%: {within}/{total}")
    worst.sort(reverse=True)
    print("\nworst mismatches:")
    for rel, k, gui, hl in worst[:8]:
        if rel > 0.01:
            print(f"  {k:28} GUI={gui:.6g}  headless={hl:.6g}  rel={rel*100:.3f}%")


if __name__ == "__main__":
    main()
