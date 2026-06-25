"""Базовые резисты/атрибуты/жизнь на срезанном билде + требования (Req*) —
для ограничений CP-SAT (резист-кап, attribute-floor, не уронить выживаемость)."""

import sys

from scripts.spikeB.engine import Engine
from scripts.spikeB.harness import Build

KEYS = ["FireResist", "ColdResist", "LightningResist", "ChaosResist",
        "Str", "Dex", "Int", "ReqStr", "ReqDex", "ReqInt",
        "Life", "EnergyShield", "TotalEHP", "ManaUnreserved",
        "FireResistOverCap", "ColdResistOverCap", "LightningResistOverCap"]

b = Build.load(sys.argv[1] if len(sys.argv) > 1 else "builds/10.txt")
rares = b.rare_core_slots()
eng = Engine()
ref = eng.eval_stats(b.xml, KEYS)
stripped = {s: b.item_for_slot(s).stripped() for s in rares}
base = eng.eval_stats(b.render(stripped), KEYS)
print(f"{'stat':<22}{'reference':>14}{'stripped':>14}")
for k in KEYS:
    print(f"{k:<22}{str(ref.get(k)):>14}{str(base.get(k)):>14}")
