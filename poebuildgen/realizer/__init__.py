from __future__ import annotations

import base64
import zlib
from typing import Dict, List, Optional
from pydantic import BaseModel

from poebuildgen.model import PobBuild
from poebuildgen.pool import WorkerPool
from poebuildgen.realizer.joint import JointRealizer


class BuildOutput(BaseModel):
    build: PobBuild
    pob_code: str                                # Base64URL(Deflate(XML))
    pob_stats: Dict[str, float]                  # Сырые статы PoB (DPS, EHP, Life...)
    effective_dps: float                         # PoB_DPS * damage_uptime_coefficient (D28)
    pob_trust_flags: Dict[str, str]              # "high" | "medium" | "low" для механик
    liquidity_flags: Dict[str, str]              # "liquid" | "illiquid" для предметов
    uptime_assumptions: Dict[str, float]         # Flask/Buff uptimes
    caveats: List[str]                           # Текстовые предупреждения


class Realizer:
    def __init__(
        self,
        pool: WorkerPool,
        model_path: Optional[str] = None,
        config_path: Optional[str] = None,
    ) -> None:
        """
        Инициализирует Realizer с пулом воркеров и путями к ML-модели.
        """
        self.pool = pool
        self.model_path = model_path
        self.config_path = config_path
        self.joint_realizer = JointRealizer(pool, model_path, config_path)

    def realize(
        self,
        build: PobBuild,
        *,
        budget: float,
        gear_start: str = "stripped",  # "stripped" | "expert"
        tree_start: str = "ml",        # "ml" | "minimal" | "expert" | "both" (запуск обоих и выбор max по DPS согласно D37)
        joint_iters: int = 2,
        tree_rounds: int = 25,
        life_frac: float = 0.6,
        tree_only: bool = False,
        **kwargs,
    ) -> PobBuild:
        """
        Выполняет совместную оптимизацию дерева и снаряжения.
        Возвращает оптимизированный экземпляр PobBuild.
        """
        return self.joint_realizer.realize(
            build,
            budget=budget,
            gear_start=gear_start,
            tree_start=tree_start,
            joint_iters=joint_iters,
            tree_rounds=tree_rounds,
            life_frac=life_frac,
            tree_only=tree_only,
            **kwargs,
        )
