"""PoB-информированные маржиналы: ΔDPS на единицу каждого offense-стата вокруг
текущей точки (срезанный baseline или уже выбранный шмот — для fixpoint).

Коэффициенты прокси берём из самого PoB (конечные разности), а не из переписанных
формул PoB — это и есть смысл «прокси, который PoB ценит».
"""

from __future__ import annotations

from scripts.spikeB.stats import PROBES


def measure(engine, build, opt_slots: list[str], overrides: dict[str, str],
            prefer: str = "CombinedDPS") -> tuple[float, dict[str, float]]:
    """overrides — текущий шмот оптимизируемых слотов (slot->текст). Возвращает
    (base_dps, {stat: dDPS/unit}). Пробу добавляем поверх текущего шмота на слот-носитель.
    Все пробы считаются ОДНИМ батчем через пул.
    """
    carrier = opt_slots[0]
    carrier_item = build.item_for_slot(carrier)
    carrier_explicits = _current_explicits(overrides, carrier, carrier_item)

    xmls = [build.render(overrides)]
    for _key, line, _amt in PROBES:
        ov = dict(overrides)
        ov[carrier] = carrier_item.with_explicits(carrier_explicits + [line])
        xmls.append(build.render(ov))

    dvals = engine.dps_batch(xmls)
    base = dvals[0]
    marg: dict[str, float] = {}
    for (key, _line, amt), d in zip(PROBES, dvals[1:]):
        marg[key] = (d - base) / amt
    return base, marg


def _current_explicits(overrides, slot, item) -> list[str]:
    text = overrides.get(slot)
    if not text:
        return []
    head = item.header_through_implicits
    lines = text.splitlines()
    return [l.strip() for l in lines[len(head):] if l.strip()]


if __name__ == "__main__":
    import sys

    from scripts.spikeB.engine import Engine
    from scripts.spikeB.harness import Build

    b = Build.load(sys.argv[1] if len(sys.argv) > 1 else "builds/10.txt")
    rares = b.rare_core_slots()
    eng = Engine()
    stripped = {s: b.item_for_slot(s).stripped() for s in rares}
    base, marg = measure(eng, b, rares, stripped)
    print(f"base (stripped) DPS = {base:,.0f}")
    print("marginal dDPS per unit (sorted):")
    for k in sorted(marg, key=lambda x: -marg[x]):
        if abs(marg[k]) > 1:
            print(f"  {k:<18} {marg[k]:>14,.0f}  ({marg[k]/base*100:+.3f}%/unit)")
    print(f"evals={eng.evals}")
