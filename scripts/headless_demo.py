"""Демо: поднять headless PoB, создать пустой билд и прочитать статы."""

from __future__ import annotations

from poebuildgen.headless import PobHeadless

KEYS = ["Life", "Mana", "EnergyShield", "TotalEHP", "TotalDPS",
        "FireResist", "ColdResist", "LightningResist", "ChaosResist"]


def main() -> None:
    pob = PobHeadless()
    print("PoB version:", pob.pob_version())
    pob.new_build()
    print("--- empty default build mainOutput ---")
    for k, v in pob.stats(KEYS).items():
        print(f"  {k:16} = {v}")


if __name__ == "__main__":
    main()
