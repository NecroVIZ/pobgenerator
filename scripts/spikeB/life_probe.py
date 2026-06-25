"""PoB-информированная оценка жизни для констрейнтов CP-SAT."""

from __future__ import annotations


def measure_life_coefs(eng, build, carrier_slot: str, stripped_overrides: dict) -> tuple[float, float]:
    """ΔLife на +1 flat life и на +1% increased life (конечные разности в PoB)."""
    item = build.item_for_slot(carrier_slot)
    base_life = float(eng.stats(build.render(stripped_overrides), ["Life"]).get("Life") or 0)

    ov_flat = dict(stripped_overrides)
    ov_flat[carrier_slot] = item.with_explicits(["+100 to maximum Life"])
    life_flat = float(eng.stats(build.render(ov_flat), ["Life"]).get("Life") or 0)

    ov_inc = dict(stripped_overrides)
    ov_inc[carrier_slot] = item.with_explicits(["10% increased maximum Life"])
    life_inc = float(eng.stats(build.render(ov_inc), ["Life"]).get("Life") or 0)

    flat_coef = max(0.01, (life_flat - base_life) / 100.0) if life_flat > base_life else 1.0
    inc_coef = max(0.0, (life_inc - base_life) / 10.0) if life_inc > base_life else 0.0
    return flat_coef, inc_coef
