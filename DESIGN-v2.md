# Генератор билдов PoE1 — Консолидированная спецификация v2.0

> Статус: **консолидированная спецификация v2.0**. Эта версия собрана как **линейная, чистая
> спека** (одно решение — в одном месте) из эволюции v0.2→v1.2, что закрывает замечание E1
> внешнего ревью («лог верификации вместо спеки»). Все верификационные находки (E1–E32) и
> решения (D1–D36) применены в основной текст; их хронология вынесена в Appendix B.
> Также применены вердикты внешнего ревью (REVIEW-of-DESIGN-v2.md, пункты A1–F3).
> Назначение: прямой сравнение с `DESIGN.md` (v0.1) и `REVIEW-of-DESIGN-v2.md`.
> Принцип: **честность приоритетнее амбиций**. Явно фиксирую, что реально в MVP, а что — труд.

> **Решение ревью A1/D1 (принято): архитектура остаётся correctness-first** (CP-SAT + fixpoint
> как ядро), НЕ разворачивается на seed-and-mutate. Обоснование совместимости с discovery-целью
> — в §1.4 и §13. **Решение ревью B1 (принято): введены уровни доверия к фактам** (Appendix A);
> patch-3.28-specific сверен с первоисточником и исправлены ошибки (Graft→Runegraft, PoB
> v2.65.0, Thread of Hope issue #9779 — см. Appendix A).

---

## 1. Цель и принцип решения

### 1.1 Два продукта
- **Продукт A — «Валидный билд-генератор»**: полные, механически корректные, валидируемые
  PoB-сборки под заданный архетип. Достижимо и измеримо.
- **Продукт B — «Необычные + сильные связки»**: исследовательская надстройка над A — максимизация
  `unusualness` (§8) при жёстких gate'ах силы. **Не предсказываем популярность** — ищем странные,
  но мощные комбинации в текущих данных.

### 1.2 Принцип поиска (correctness-first — решено, см. ревью A1; **обновлено D37**)
Скелет → реализатор → PoB. **PoB — единственный оракул числовой истины** (DPS/EHP/легальность);
все прокси и линейные оценки — только для seed/feasibility, не для финального maximize (D37).

Реализатор — **гибрид** (эмпирически подтверждён спайками Phase 0):
- **Снаряжение:** CP-SAT как **feasibility-seeder** (легальный шмот под бюджет/констрейнты), затем
  **PoB-in-loop** hill-climb/multi-start как оптимизатор (не CP-SAT-maximize-DPS).
- **Дерево:** **target-set** (ML-prior или PoB-greedy/hill-climb) + **Dijkstra-stitching** (D14);
  sd-tag target-set — отвергнут как primary (overlap ~5% в спайке).
- **Cold-start / joint:** tree↔gear **fixpoint** обязателен; ML-prior over trees/notables — основная
  траектория для cold-start (спайк E: 9% без prior).
- **Joint constraint loop** между слоями (дерево ↔ шмот ↔ резист/атриб/резервация), не линейный pipeline.

> **D37 (2026-06-25, Phase 0 spikes):** §1.2 **развивает**, не отменяет correctness-first (A1/D1).
> CP-SAT остаётся в скелете спеки как feasibility-solver; **primary-солвер оптимизации = PoB**.
> ML — learned prior над target-set/деревом (не замена PoB). Gate: `max(heuristic_baseline, ML)`;
> при проигрыше ML на минимальном эксперименте — заморозка ML-ветки (см. ML-DIALOGUE.md).

### 1.3 Разрешение ревью A1: почему correctness-first совместим с discovery-целью
Ревью утверждает, что throughput 10–60 билдов/час/ядро «душит движок открытия». Это справедливое
напряжение, но разворот на seed-and-mutate **отклонён** по причинам:
1. **Seed-and-mutate наследует ошибки семян.** Реальные билды содержат PoB-config-abuse
   (накрученные uptime, free buffs). Мутируя их, система размножает эти ошибки, а не «находит
   новое». Correctness-first гарантирует, что каждый кандидат построен из честных компонентов.
2. **Discovery живёт в unusualness-метрике (§8), а не в ширине перебора.** Даже 10 билдов/час,
   если они проходят честные gate'и и максимизируют unusualness, ценнее 1000 мутаций мета-билда.
3. **Phase 1 = узкая точка (2 билда, D3), а не массовый перебор.** Throughput-напряжение актуально
   с Phase 2 (энумерация), где и применяется batch + checkpoint-resume + multiprocess (D32).
4. **CP-SAT решает «craftable + budget + constraints» — задача, которой seed-and-mutate не
   решает вовсе** (мутация не гарантирует, что предмет существует в игре). Для цели «готовые
   валидные билды» это критично.
Честно: если Phase 2 покажет, что throughput критически мал для exploration, **seed-and-mutate
остаётся доступным как дополнительный слой** для breadth-search, а CP-SAT — как «ремонтник»
мутаций. Решение «correction-first как primary» пересматривается по данным Phase 2, не сейчас.

### 1.4 Решение ревью A2: «новая мета» — явный downgrade цели
В v0.1 цель была «поиск новой меты». **Осознанно понижено** до «необычное+сильное в текущих
данных» (§8.4), потому что «мета от будущих лиг/предметов» **невозможна** до их появления в
данных PoB. Это не побочный эффект, а явное решение с обоснованием: вероятностный прогноз
популярности зависел бы от внешних данных (ninja) и был бы недостоверен; формальная метрика
unusualness в пространстве механик — честнее и self-contained. Альтернатива «сохранить
новую-мету как амбицию» отклонена как недостижимая на текущих данных.

---

## 2. Конвейер (pipeline)

```
[1] Энумерация скелетов (skill × ascendancy × механическая точка в пространстве осей)
        |
        v
[2] Skeleton-gate (ДЁШЕВО, БЕЗ CP-SAT/PoB): pre-filter структурно-безнадёжных —
    доступность источника freeze-immunity (A11), accuracy-source для атак, curse-слотов,
    мин. резервация ≤100%, правила зависимости (§4.2). Провал → отбраковка скелета.
        v
[3] Реализатор (joint, iterative fixpoint D1):
    дерево (target-set + shortest-path, D14) + шмот (cost-constrained CP-SAT, D15) + гемы.
    Круговые зависимости — fixpoint как pre-filter (D1: inconclusive→PoB, не invalid).
    infeasible → back to tree-prune (D14); timeout → relax (D15).
        v
[4] Gate-валидатор — только СТРУКТУРНЫЙ (БЕЗ числовых формул PoB): легальность гемов/линков
    (К6), item-constraints (К7), curse-limit, budget К5, ailment-source-наличие (A11),
    MVP-инвариант (D21). ЧИСЛОВЫЕ gate'ы → PoB [5] (D22: устраняет второй расчётный движок).
        v
[5] Оценка в PoB (DPS/EHP-вектор/макс-хит + числовые gate'ы: точный резист-кап, резервация,
    accuracy-vs-enemy, freeze-source реально активен D27).
        v
[6] Локальная оптимизация (хилл-клаймб: своп узла/мода/гема, в рамках бюджета). Принятие —
    по Tchebycheff-скор T (D2, не чистый Pareto).
        v
[7] Ранжирование: reference-point T (D2) + unusualness (§8, с discovery D6) + доступность.
    Degenerate-guards (D34): diversification, anti-meta-mimicry, sanity-bounds.
```

---

## 3. Модель данных

```
Build {
  class, ascendancy, bloodline_ascendancy?, level,
  skills: [ SkillGroup {
      slot,                          # привязка к слоту-предмета (К1: PoB считает по слоту)
      main_gem { base_id, level, quality, quality_type },   # level — производное (К4, slot-bound D11)
      supports [ {base_id, level, quality, quality_type} ],
      is_active: bool,               # MVP-инвариант D21: ровно одна группа с is_active=true
      trigger?                       # MVP: всегда ∅ (П2); multi-skill — поздняя фаза
  } ],
  active_socket_group: SlotId,       # К1: явный указатель "main" socket group
  active_skill: GemRef,              # К1: явный указатель активного гема внутри группы
  tree: { allocated[], masteries[], jewel_sockets: { socket_id -> Jewel } },  # D2: mapping
                               # cluster_jewels / timeless jewels — ИСКЛЮЧЕНЫ из MVP (К3, П3)
  gear: { slot -> Item },
  jewels: [ Jewel ],                # ordinary jewels only в MVP
  flasks: [ Flask ],                # charge-balance модель (D18), не uptime-float
  bandits,
  pantheon: {                        # D19: структурированный объект с upgrade-state
    major: (God, upgrade_tier 0..3), # major gods: 3 upgrade-тира (Divine Vessel + map-boss)
    minor: (God, upgrade_tier 0..1)  # minor gods: 1 upgrade-тир; tier определяет силу эффекта
  },
  mobility_skill: SkillGroup,        # Flame Dash и т.п. (non-DPS, не входит в active-set)
  config: CombatConfig,              # обязательный
  budget_profile: BudgetProfile      # экономика
}

Item {                               # v0.7 D9: домен = реальные правила игры
  base, item_level, rarity,
  item_source: "rare-craftable" | "unique" | "synthesised" | "fractured-base",  # D9: определяет пулы/взаимоисключения
  influences: { shaper, elder, crusader, hunter, redeemer, warlord },  # D9: cap=2 (Σbool≤2), взаимоискл. со synth/eldritch
  eldritch: { eater implicits[], exarch implicits[] },     # D9: ТОЛЬКО при influences=∅ и base∈{helm,gloves,boots,body}(+Eternal-Struggle)
  synthesised_implicits: [],          # D9: взаимоискл. с influence (synth=отдельный base-тип)
  fractured_mod: Mod | null,          # D9: совместим с influence (с 3.24); MVP: ≤1 fractured-мод
  catalyst: CatalystType | null,
  explicit_affixes: [ Affix{ mod_id, tier, values[], group } ],
  corrupted, implicit_mods: [], enchant: Enchant | null,
  links: int,                         # Д1: число линков (производное от base+corrupt); констрейнт len(skill_group)≤links (К6)
  quality                              # для gem-items
}
# КОНСТРЕЙНТЫ ПРЕДМЕТА (домен CP-SAT, иначе генерируются невозможные предметы):
#   (1) Σ influences.bool ≤ 2                          # influence-cap (E6)
#   (2) eldritch ≠ ∅  ⟺  influences == ∅ ∧ base ∈ eldritch_slots   # eldritch/non-influenced (E5)
#   (3) synthesised_implicits ≠ ∅  ⟹  influences == ∅ # synth взаимоискл. с influence (E7)
#   (4) item_source == "synthesised" ⟹ synthesised_implicits ≠ ∅
#   (5) fractured_mod: ≤1 в MVP; совместим с influence (E7, с 3.24)

CombatConfig {
  enemy_profile: "white" | "rare" | "pinnacle" | "uber",   # A10: tiered, `rare`=обязательный gate
  enemy_level,                                            # 83 для white/rare (map-tier-84)
  active_buffs: [Buff],               # ТОЛЬКО самообеспечиваемые
  flasks_uptime: derived,              # D18: ПРОИЗВОДНОЕ от charge-balance Flask[], не input
  buff_uptime_models: {Buff -> UptimeModel},  # D29: decaying (Elusive)/conditional (Adrenaline/Rage) — производное, не "always full"
  shock_assumed: (source: SkillId, effect: float) | null,  # только если есть источник
  charges_assumed: { ... },            # assume capped ТОЛЬКО если есть generation-source
  onslaught, fortify, ...              # только если билд их даёт
  damage_uptime_model: {               # D28: PoB-DPS = потолок; реальный = × uptime (E24)
    archetype_coefficient: float,      # mines/totems/traps ~0.7-0.9; self-cast ~0.4-0.6; melee ~0.3-0.5; DoT ~0.5-0.7
    # честно: эвристика ±0.15; effective_DPS = PoB_DPS × coefficient; ранжируется effective, не сырой PoB
    # F3 (review): калибровать по данным/эталонам, не задавать руками
  }
}

# === ЭКОНОМИКА (К5) — первоклассная подсистема ===
BudgetProfile {
  league: LeagueId,                   # tempcore / hardcore / standard
  day_of_league: int,                 # старт vs mid vs late — цена меняется в разы
  total_budget: Currency,             # в chaos-equivalent
  max_tier_per_slot: { slot -> Tier } # мягкий потолок тира (backstop)
}

CostModel {
  unique_prices: { unique_id -> Price },      # из ninja (волатильны! E7)
  base_prices:   { base_id -> Price },
  gem_prices:    { (gem_id, level, quality) -> Price },
  # D5: crafting-method — дифференциация моделей стоимости:
  #   независимые (alt/chaos-spam/essence/fossil): expected_cost = attempts × per_attempt, attempts=1/success_prob (геометрическая)
  #   последовательные (metacraft-block+remove, harvest-reforge, fossil-резонаторы): МАРКОВСКАЯ ЦЕПЬ / DP по состояниям аффикс-сета
  craft_cost: (base, target_mods) -> { method, expected_cost, success_prob }[]
  # D30: ликвидность ≠ цена. "доступно" — это наличие, не только стоимость.
  liquidity: { item_id -> { listings_count, volume_7d, freshness_days, ssf_feasible } },
  # item где listings≤2 или freshness>7d → flag illiquid (в BuildOutput, не отбраковка)
}

Flask {                              # D18: charge-balance, не uptime-float
  base, rarity, flask_mods[],
  instilling: InstillingEnchant | ∅,   # auto-trigger (use-when-full / when-hit / left-click...)
  enkindled: bool, enkindling_effect | ∅,  # effect↑, charge-gain↓↓
  # uptime — ПРОИЗВОДНОЕ от charge-balance (не input):
  #   f(max_charges, charges_per_use, gen_rate, reduced_charges_used, duration_mods, instilling, mageblood|traitor)
  # gen_rate-источники: kills-rate (CombatConfig), "charges gained"-моды, "charges when hit",
  #                    Tides-of-Time, Alchemist's-Boon, asc-узлы (Pathfinder).
  # Билд без источника gen-rate НЕ МОЖЕТ заявить uptime=1.0 (устраняет PoB-warrior на уровне модели).
}

# D31 — ПРОДУКТОВЫЙ СЛОЙ: что получает пользователь (с метаданными доверия)
BuildOutput {
  build: Build,
  pob_code: str,                       # для импорта (Base64URL(Deflate(XML)), D23)
  pob_stats: { dps, ehp_vector, max_hit_by_type, ... },  # сырое, как PoB насчитал
  effective_dps: float,                # D28: PoB_DPS × damage_uptime (реалистичный, не потолок)
  pob_trust_flags: {mechanic -> high|medium|low},  # D7: low → "требует игрового подтверждения"
  liquidity_flags: {item -> liquid|illiquid|unknown},  # D30
  uptime_assumptions: {damage_uptime_coeff, buff_uptimes},  # D28/D29 — допущения явно
  caveats: [str],                      # человекочитаемые пометки ("requires-trade-patience" и т.п.)
}
# Принцип D31: пользователь НЕ воспринимает число как факт без контекста.
```

**Принцип CombatConfig:** всё, что билд сам себе не обеспечивает — **выключено**, если нет источника.
Это устраняет «PoB-warrior DPS». **D18/D29:** uptime (flasks, Elusive, Adrenaline) — производное
от модели источника, не ручное поле.

---

## 4. Генерация скелетов (движок новизны)

### 4.1 Пространство осей
В дополнение к осям v0.1:

| Ось | Значения | Почему |
|-----|----------|--------|
| Скейл-якорь | стандартный / attribute-stacking / conversion-based / trigger-based / minion | attribute-stacking и conversion — отдельные архетипы |
| Защитный профиль | armour / evasion+suppression / block / max-res / MoM / CI / ward / ─ | определяет ветки дерева и типы нужных модов |
| Статы-драйверы | плоский урон / % урон / speed / crit-multi / DoT-mult | предсказание ценных модов |

### 4.2 Совместимость = жёсткие правила, не теги
Перед энумерацией — **таблица правил**:
- **Несовместимости:** DoT-скилл (RF/ED/Blight) + крит-ось → отброс; миньон + caster-дерево → отброс;
  trap/mine + cast-speed-дерево → слабо; без источника шока + конфиг «shock 50%» → невозможен.
- **D3 (review Д3) — правила ЗАВИСИМОСТИ:** trigger-флаг (CoC/CwC/CwDT) активен → **требуется**
  активный attack/spell-источник; charge-движитель + Discharge → требует Discharge; totem + skill
  без totem-tag → бесполезно. Skeleton без зависимости → отбраковка.
- **C2 (review):** исключение «крит применим к DoT» (Perfect Agony) ограничено классом
  **ailment-DoT** (ignite/poison/bleed), НЕ skill-DoT (RF/ED). Базовый хит всё равно критует.

Эти правила — **данные, а не эвристики**, ведутся явно в отдельном файле.

### 4.3 LLM-слой (D17: роль переформулирована честно)
LLM = **priority-эвристика для known-strong-combos** + **source гипотез** для discovery-term (D6),
НЕ «расширитель за пределы известного». LLM обучена на данных комьюнити → её гипотезы совпадают
с тем, что жадная энумерация нашла бы сама. Настоящее расширение живёт в discovery-term (D6,
формальная мера), не в LLM-выводе. Вывод — JSON-контракт (A8), парсится через whitelist, валидируется PoB.

### 4.4 Каталог механических флагов M
Пополняемый вручную + из данных PoB каталог необычных механик. Каждый флаг = механика,
«переворачивающая обычное правило», кормит unusualness (§8). **Уровни доверия (review B1)**
проставлены по Appendix A. Сгруппированы по семействам:

**А. Конверсия «стат → эффект» (атрибут-стекинг):**
- STR-stacking: Iron Will, The Baron, Geofri's Sanctuary, Meginord's Vise, Iron Commander.
- INT-stacking: Void Battery, Crown of the Inward Eye, CI-int-stackers.
- All-stat: Astramentis.
- ⚠️ **INT→ES-rebalance 3.28 (verified-by-primary, Appendix A):** 1% inc max ES per **10** INT
  (раньше per 5) = **NERF вдвое**. int-stacking ослаблен, не усилен. ~~бонус int-stack×INT-rebalance~~ — аннулирован.

**Б. «Ресурс = пул»:** Archmage, MoM, EB, CI, Corrupted Soul, Ghost Reaver, Blood Magic,
Petrified Blood+low-life. ES-recharge: Aegis Aurora, Wicked Ward, Zealous Oath.
**Ward** (verified-by-primary: Expedition 3.15, итерирован 3.25, восстановление 4с) — валидный defensive-канал.

**В. Защита «переворачивающая правила»:** Transcendence, Melding of the Flesh, Glancing Blows,
Wind Dancer, block-recovery-on-block (Aegis Aurora, The Surrender).

**Г. Trigger-двигатели:** CoC, CwC, CwDT, Cast on Melee Kill, Spellslinger, Automation, Unleash,
Intensify, Spell Echo. Оружейные: Mjölner, Cospri's Malice, Asenath's. Influence-триггер.
Discharge + charge-двигатели (Voll's, Romira's).

**Д. Состояние:** Frenzy/Power/Endurance-stacking, Rage, warcries/exerted-attacks, Banners/impale, Brands.

**Е. DoT-специфичные (включая исключения C2):**
- Perfect Agony — crit-multi на **ailment-DoT** (ignite/poison/bleed), НЕ skill-DoT; хит критует.
- Crimson Dance, Plague Bearer, Toxic Rain, The Golden Rule.
- Elemental Equilibrium, Elemental Overload, Avatar of Fire.

**Ж. Миньон-наследование:** Spectres, Animate Guardian (вложенный билд — С3, вне MVP),
Animate Weapon, Necromantic Aegis, The Scourge, Arakaali's Fang, golemancer, Dominating Blow/Smite.

**З. Скилл-семейства со «странной» доставкой:** Steel + Call of Steel, Kinetic Bolt/Blast,
Flicker Strike, Lightning Strike, Cyclone, Blink/Mirror Arrow, Vaal skills.
**Runegrafts (verified-by-primary, Appendix A):** 10 новых в 3.28 (через Kingsmarch-shipment) —
Runegraft of the Agile/Connection/Consecration/Fury/Imbued/Rallying/Resurgence/Rotblood/Spellbound/Suffering.

**И. Лига-системы:** Crucible weapon trees, Tinctures, Kalandra reflected rings,
Eldritch/Synth/Fractured implicits, Watcher's Eye, Replica uniques, Cluster jewels.
**Imbued gems (verified-by-primary, Appendix A):** 3.28, Djinn coin corrupts skill gem → inherent support-effect.

**К. Обязательные gate'ы (viability, не необычность):**
- Accuracy (атаки), leech/regen/recharge (сустейн), curse-limit, totem/trap/mine/brand caps,
- Mobility skills, stun-avoidance (soft gate A11),
- ailment-immunity: **freeze = HARD gate (mandatory)**; **chill = soft gate** (review C1:
  понижен с hard — большинство билдов без chill-immunity играбельны); shock/poison/bleed/ignite — НЕ gate.
- cast/attack-speed caps.

---

## 5. Реализатор — joint constraint solver

### 5.1 Снаряжение — CP-SAT (D15: разрешимость при реальном масштабе)
CSP: для каждого слота — выбор базы + аффиксов + eldritch/synth/influence-опций.
- **Домены:** реальные пулы модов из PoB-data / RePoE (D4-gate сверки покрытия).
- **Глобальные констрейнты (D22: числовые → PoB, здесь только структурные):**
  - требования атрибутов ≥ требования гемов/шмота;
  - суммарная резервация ≤ 100% (с efficacy/Enlighten/Blood Magic);
  - curse-слотов достаточно;
  - **viability-gate на `rare`-профиле (A10):** DPS/EHP-пороги измеряются на `rare` (effective-DPS D28);
  - **Σ cost(item, mods, craft_method) ≤ total_budget** (К5) — бюджет. **D4 (review): для unusual-
    слотов бюджет МЯГКИЙ (ранжирующий), не жёсткий** (crafting-cost ±100% именно там).
- **К4 (gem level, D11: +level-моды slot-bound):** level = base + quality + Empower + `+level
  socketed` моды + corrupted implicits. +level-моды **slot-scoped** (weapon→только weapon-сокеты).
  Связь в CP-SAT: `gear[slot].+level-mods → действуют только на SkillGroup где slot совпадает`.
- **Решение (D15):** per-slot pre-solve → feasible-domain; cross-slot (резист-сумма, резервация,
  бюджет) через **Lagrangian relaxation / column generation**; timeout → relax → retry; повторный
  timeout → inconclusive → PoB-проверка лучшего (D1-philosophy: не отбраковывать).

### 5.2 Fixpoint поверх CP-SAT (D1: pre-filter, не оракул)
**К2 — круговые зависимости:** `added as`, attribute-stacking, Iron Will — циклы. CP-SAT линейный.
- **Fixpoint = pre-filter, не оракул валидности.** Несход за 5 итераций → **inconclusive → PoB**,
  не «invalid» (критика: иначе отбраковывает целевые циклические схемы).
- **Детекция 2-цикла (D1):** при повторе решений → среднее derived как фикс-точка → стабильно → valid.
- **Bounded-optimization:** CP-SAT минимизирует `cost + λ·change от предыдущего решения` → убирает осцилляцию.
- δ=0.5% по derived-статам; max 5 итераций. Долгосрочно (§15.К2): нативный nonlinear/MILP.

### 5.3 Дерево — D14: декомпозируемая конструкция (не Steiner)
**Заземление (E10):** дерево PoE1 ~2175 узлов, ~135 очков. Steiner NP-hard — **отказ**.
1. **Target-set selection (эвристика, явно названная):** ~8–15 целевых нотаблов по stat-match (PoB-data).
2. **Shortest-path stitching:** Dijkstra (small-node=1 очко); union путей = allocated-set.
3. **Point-budget pruning:** Σ > ~135 → отбрасываем цели с наименьшим stat-value-per-point.
4. **Mastery assignment:** ~21 типов × ~10 эффектов как local search.
5. **Jewel-socket fill:** ordinary jewels (MVP).
- Ascendancy/Bloodline фиксируют стартовую точку. Качество «приемлемое для MVP»; hill-climb (§7) дорабатывает.
- **К3/П3:** Timeless/Cluster jewels — поздняя фаза (отдельные конструкторы), MVP = plain tree + ordinary jewels.

### 5.4 Joint loop
После дерева+шмота — skeleton-gate [2] + structural-gate [4]. Fail → локальная пересборка
затронутого слоя, не полный пересбор. infeasible → tree-prune (D14).

---

## 6. Оценка через Path of Building (headless)

### 6.1 Гибрид XML-контракт + тёплый luajit (D8 perf, D23 tooling, D24 round-trip)
- **Контракт = PoB-XML** (не код напрямую). Код = `Base64URL(Deflate(XML))` (E19) — transport-encoding.
- **Tooling (готовое, D23):** `HeadlessWrapper.lua` (headless; luajit, не lua 5.3);
  `pob_wrapper` (Python, PoB как subprocess — MVP); `pobapi` (Python-парсер кодов).
- **Изоляция (D8):** LuaJIT не потокобезопасен → последовательные прогоны в одном процессе;
  параллелизм через multiprocess. Throughput 100ms–1с/билд (не «десятки мс»). raid-check +
  тонкая проверка active-skill-результата на эталонном микро-билде.
- **Round-trip ±1% (D24, E20):** по **статам**, не по коду (коды не идемпотентны при ре-кодировании).
  `build.xml → наш-формат → build.xml' → PoB → статы` vs `build.xml → PoB → статы`.
- **PoB-defaults whitelist (D24):** поля, которые PoB defaults'ит (`mainSocketGroup` и др.) —
  сериализатор обязан эмитить явно.
- **Property-based round-trip:** fuzzer валидных build.xml → round-trip → assert статов.

### 6.2 Контракт-валидатор (усиленный, D3 + D27 oracle-wins)
1. Каждый ожидаемый эффект (по `mod_id`) в `modDB` с правильной величиной.
2. Active skill — тот, что планировался.
3. Support-гемы — все активны.
4. Masteries выбраны и дают эффект.
5. Расхождения → **invalid**, не «молча 0 DPS».
6. **Phase 1 (D3):** DPS-цифра соответствует детерминированному config (одна цель, фикс. дистанция).
7. **D27 (oracle-wins, E23):** мод, который structural-gate счёл «источником» (иммунитет/curse/charge),
   **реально активен в PoB** (в modDB с эффектом). PoB — единственный оракул; structural-gate =
   оптимизация. Закрывает ложный accept.

### 6.3 Миграционный путь — fork PoB + JSON API
Для production: fork PathOfBuildingCommunity + JSON/HTTP-API. **D23: для MVP `pob_wrapper` ближе**;
fork+JSON — production-оптимизация (когда упрётся в stub-хрупкость), не Phase 0.

### 6.4 Fitness-вектор (D2: reference-point, не чистый Pareto)
- effective DPS (PoB × damage_uptime D28, на `rare`-профиле);
- **макс-хит по каждому типу урона** (физ/огонь/холод/молния/хаос) — покрывает shock/ignite-усиленные хиты;
- EHP (block-вероятностная часть отдельно);
- сустейн (life/mana regen+leech);
- gate-флаги: резисты капнуты, accuracy капнут, **freeze-immunity пройден (hard gate A11), stun-
  avoidance пройден (soft gate A11, с Pantheon tier D19)**.

**Ранжирование (D2):** двухуровневая схема.
1. **Pareto-фильтр** (отсекает мусор). При ~10 осях чистый Pareto вырождается — поэтому только шаг отсева.
2. **Tchebycheff/reference-point:** `T = max_i(w_i · |f_i − f_i*| / r_i)`. **Веса публикуются**
   (DPS=1.0, EHP=0.8, max-hit-worst=0.8, sustain=0.5, доступность=0.6, unusualness=0.9; D20 sensitivity-audit).
   «Не взвешенный скор» ОТМЕНЁНО как нереализуемое (проклятие размерности).
- gate-оси — жёсткие фильтры ДО скаляризации. `pinnacle`-профиль = бонус.

---

## 7. Оптимизация
- Локальный хилл-клаймб: своп узла дерева у границы / замена мода / замена support.
- Принятие — по **T (D2)**: шаг принимается, если улучшает T (T=max по осям — нельзя «убить» EHP
  ради DPS). Бывшее «по Парето» заменено (не ранжирует при ~10 осях).
- Genetic/Bayesian — позже. Кэш под-деревьев/слотов между кандидатами. **Warm-start через `AddHint`** (D32).

---

## 8. Оценка «необычности» (unusualness)

### 8.1 Формальное определение (D6: + discovery-term)
```
unusualness(build) = Σ r(m)                   # сумма активных необычных флагов
                  + Σ c(mi, mj)               # БОНУС за курируемые комбинации (A7)
                  + discovery(build)          # D6: редкость набора флагов в собственной истории
                  − Σ p(mk)                   # штраф за «обычные» флаги
```
- `Σ r(m)` — линейная «отдельно необычное».
- `Σ c(mi,mj)` — curated нелинейности (A7).
- **`discovery(build)` (D6, review D2): self-contained мера новизны** — редкость набора флагов в
  **собственной сгенерированной истории** (BuildHistory, D33). Расширяет рекомбинацию за пределы
  curated-list. **D2 (review): привязан к осмысленному расстоянию** (взаимодействия флагов, не
  наличие случайного); нижний порог «значимости» флага (бесполезный флаг не награждает);
  **cold-start:** пустая история → discovery≈0 → unusualness=curated-only (честно зафиксировано).

### 8.2 Целевая функция (D20: абсолютный порог, не percentile — review D3)
```
максимизировать unusualness(build)
при ограничениях:  PoB-viable(build) = true     (gate + честный CombatConfig)
                   effective_DPS(build) ≥ DPS_threshold   (на `rare`, абсолютный порог — D20/D3)
                   EHP(build) ≥ EHP_threshold   (на `rare`, абсолютный)
                   freeze-immunity = true       (hard gate A11)
```
- **D20/D3 (review): пороги = АБСОЛЮТНЫЙ физический** под контент (выживаемость/урон), НЕ
  percentile-of-known-good (последний по построению против новизны). Калибруются по эталонам A1
  + sensitivity-audit к ±20% весов.
- **D35 (review D3): двухуровневая выдача** — (1) «сильные+необычные» (проходят порог, T+unusualness),
  (2) «high-unusualness-but-borderline» (близко к порогу, помечены «требует валидации» в BuildOutput).

### 8.3 Веса и бонусы
- `r(m)` — эвристика автора (Phase 2), опц. калибровка по референс-базе.
- `c(mi,mj)` — курируемый список пар (A7, с PoB-trust-флагами D7): attribute-stack×Transcendence,
  Archmage×CI, Perfect Agony×ailment-DoT, Melding×chaos-res-stack, Battlemage×Crown-of-Eyes.

### 8.4 Что исключено (честно)
- «Мета от будущих лиг» — **невозможно** до данных PoB (явное решение A2).
- LLM не открывает новых механик (D17) — recomбинирует известное.

---

## 9. Источники данных (D4 RePoE-gate, D22 oracle, D26 invalidation)
- **PoB data-экспорт** — каноничный источник **расчёта** И, при неполном RePoE, правил генерации.
- **RePoE** — первичный источник правил генерации, **но с обязательной сверкой покрытия ДО Phase 1**
  (D4-gate Phase 0: Bloodline/Runegraft/Imbued/3.28-механики). При gaps → fallback на PoB-data.
- **poe.ninja API** — популярность + экономика. URL требует DevTools-верификации (E4).
- **Версионные снапшоты** по хэшу коммита PoB + патчу лиги.
- **D22 (oracle):** PoB — единственное ядро расчёта (соблюдается: pre-gate только структурный,
  числовой → PoB).
- **D26 (invalidation при patch-boundary):** regression-set A1 пересчитывается (дрейф >1% =
  breaking-change); кэш результатов инвалидируется; curated-bonuses A7 + PoB-trust D7 ревьюятся;
  каталог M `?проверить`-пункты ревьюятся. Automate: diff PoB-data → human-review затронутых.

---

## 10. Стек (D32 throughput, D33 BuildHistory)
- Python + **OR-Tools (CP-SAT)**, **`AddHint` warm-start** (D32) для fixpoint/local-opt.
- `networkx` + Dijkstra (D14); `pydantic`.
- PoB: **`pob_wrapper`** (subprocess, MVP) + `HeadlessWrapper.lua`/luajit; `pobapi`. fork+JSON — production.
- SQLite: кэш + результаты + **`BuildHistory` store (D33)** — persistent, для discovery (D6) и
  feedback-loop; JSONL-экспорт для аудита. Инвалидируется по D26.
- LLM через API, whitelist (D17: priority-эвристика).
- FastAPI + UI — поздняя фаза (выводит BuildOutput D31 с trust-флагами).
- **Throughput (D32):** 1 ядро ≈ 10–60 билдов/час full-pipeline. Multi-archetype через multiprocess.
  Phase 2 — batch + checkpoint-resume.

---

## 11. Фазы (MVP → рост)
- **Phase 0 — Round-trip PoB + D4 RePoE-gate.** Round-trip ±1% по статам (D24). Включает gate
  сверки покрытия RePoE для 3.27/3.28 (§9); пока не пройден — Phase 1 не стартует.
- **Phase 0b — Pipeline цен.** Зеркалирование ninja (URL DevTools-верификация E4), кэш с
  версионированием. Параллельно с Phase 0.
- **Phase 1 — D3: ДВА контрастных билда.**
  - **1a (лёгкий):** Templar/Inquisitor, spell+hit+lightning+crit, Determination+Grace+suppression.
    Скилл — **Arc-семейство** (Spark заменён D3: config-abuse-зависим).
  - **1b (жёсткий):** атрибут-stacking (циклический скейл → напрягает fixpoint) ИЛИ конверсия
    (напрягает gate резистов). Доказывает, что архитектура держит тяжёлый случай.
  - **Критерий успеха:** оба собираются, на 1b fixpoint сошёлся (D1), gate'и прошли, round-trip ±1%.
- **Phase 2 — Энумерация скелетов + unusualness (D6 discovery).** Каталог M наполняется.
  poe.ninja — опц. (калибровка `r(m)`). Batch + checkpoint-resume (D32).
- **Phase 3 — Локальная оптимизация + reference-point T (D2).**
- **Phase 4 — Расширение осей** (DoT, атаки, minion, конверсия, attribute-stack — по одной).
  Trigger/multi-skill (П2) — отдельная под-фаза.
- **Phase 5 — Cluster/timeless jewels, eldritch, synth, fractured; UI.**
- **Phase 6 — fork PoB + JSON API.**

---

## 12. Риски (residual-risk-register, E32)
| Риск | Статус | Mitigation |
|------|--------|-----------|
| PoB-слепые зоны на unusual-механиках | нерешаемо | D7-флаги доверия; калибровка по эталонам; честные caveats |
| «Мета от будущих лиг» | невозможно до данных | §8.4 (явное решение A2); D26 |
| Uptime/crafting-cost/liquidity оценки | ±15–50% | честные пометки; sensitivity-audit (D20) |
| LLM не открывает нового | структурно | D17 честная роль; D6 discovery |
| Curated-bonuses ограничены куратором | частично | D6 discovery; пополняемый каталог M |
| Полное покрытие всех осей | многолетний труд | §14; Phase-by-axis (§11) |
| Масштаб Phase 2+ | часы-дни/ядро | D32 multiprocess + batch + checkpoint |
| Steiner на ~2175 узлах | (устранено) | D14 декомпозиция |
| CP-SAT комбинаторный взрыв | (mitigated) | D15 per-slot pre-solve + Lagrangian |
| Throughput «тысячи/час» | (устранено) | D32 реалистичные 10–60/час |

---

## 13. Принятые решения
- **Версия: патч 3.28 «Mirage» (verified-by-primary, Appendix A).** PoB — **v2.65.0** (latest, не
  v2.63.0 — устарел). Imbued/Bloodline — verified-by-primary. Runegrafts (не «Graft skills» 3.27).
  INT→ES: 1% per 10 INT (nerf, не усиление).
- **A1/D1 (review): correctness-first остаётся.** Обоснование совместимости с discovery — §1.3.
- **A2 (review): «новая мета» → «необычное+сильное» — явное решение** (§1.4).
- **Скоуп билда: MVP = один main skill** (П2: multi-skill/trigger — поздняя; MVP-инвариант D21).
- **Экономика: полная, сразу, crafting-methods** (D5: независимые=геометрическая, метакрафт=DP).
  **D4 (review): бюджет мягкий для unusual-слотов.**
- **Phase 1 = два билда** (1a Inquisitor/Arc + 1b жёсткий).
- **Eldritch implicits: ВКЛЮЧЕНЫ в MVP** (4 слота; только non-influenced, D9).
- **enemy_profile (A10): tiered** — `rare`=hard gate (D20: абсолютный порог D3-review), `pinnacle`=bonus, `uber`=post-MVP.
- **ailment-gates (A11, review C1): freeze=hard; chill=soft; stun=soft (Brine King); shock/poison/bleed/ignite=НЕ gate.**
- **C3 (review): Brine King** — «cannot be Frozen if you've been Frozen recently» (условный иммунитет)
  + «50% reduced Effect of Chill», НЕ «50% avoidance».
- Реализатор = CP-SAT (fixpoint D1 pre-filter, **feasibility-seeder D37**) + декомпозиция дерева (D14)
  + **PoB-in-loop optimize** + **ML-prior** (tree target-set, cold-start).
- **D37 (2026-06-25):** PoB-first ядро; CP-SAT не maximize-DPS; ML = prior, PoB = likelihood/oracle.
- Joint constraint loop; CombatConfig самообеспечиваемый; PoB-data для расчёта + (fallback) правил.
- unusualness = curated + discovery (D6); reference-point ранжирование (D2).
- LLM в контуре, whitelist, priority-эвристика (D17).
- Headless PoB via `pob_wrapper` (MVP) → fork+JSON (production).

---

## 14. Честная оценка реализма
| Компонент | Реалистично в MVP? | Комментарий |
|-----------|-------------------|-------------|
| PoB round-trip + контракт-валидатор | **Да**, недели-две | Phase 0, самый рискованный |
| CP-SAT с fixpoint D1 (ordinary gear) | **Да** | D1: inconclusive→PoB; D15: per-slot+Lagrangian |
| Дерево (D14 декомпозиция) | **Да** | не Steiner; fallback на жадный |
| CombatConfig + gate'ы | **Да** | D22: структурные pre-gate, числовые → PoB |
| Pipeline цен ninja | **Да**, внешняя зависимость | URL DevTools-верификация (E4) |
| Crafting-симулятор метакрафта (D5 DP) | месяц-два | внутри Phase 1; до DP ±100% |
| **Два** билда + бюджет (Phase 1, D3) | **4–5+ мес** одного разработчика | 1b валидирует архитектуру |
| Энумерация + unusualness (D6 discovery) | **Частично** | калибровка; cold-start → curated-only |
| Полное покрытие осей | **Многолетний труд** | каждая ось = мини-проект |
| Cluster + timeless jewels | **Месяцы** | timeless — hardest |
| «Новая мета» от будущих лиг | **Невозможно** | явное решение A2 |

---

## 15. Критические требования (без чего генератор молча неверен)

**15.К1 — Active skill как первоклассная сущность:** явные `active_socket_group` + `active_skill`.
**15.К2 — Fixpoint = pre-filter (D1):** inconclusive→PoB; долгосрочно MILP.
**15.К3 — Timeless/cluster jewels вне MVP.**
**15.К4 — Уровень гема = производное (D11 slot-bound):** +level-моды slot-scoped.
**15.К5 — Бюджет = constraint (D4 review: мягкий для unusual).**
**15.К6 — Socket/link-легальность (Д1):** `len(skill_group) ≤ gear[slot].links`.
**15.К7 — Домен предмета = правила игры (D9/D11).**
**15.П2 — Multi-skill/trigger поздняя фаза (D21 MVP-инвариант).**
**15.П4 — Защита по входящим типам хита.**
**15.С3 — Animate Guardian/Spectres вне MVP.**

**15.В — Validation-фреймворк (D25):**
- **В1 Regression/golden-master:** эталоны A1 → фиксация DPS/EHP → CI на каждом изменении.
- **В2 Oracle-property:** инварианты автоматические (Σрезистов≤cap+ε; budget; MVP-инвариант D21;
  item-constraints К7; freeze-source активен D27). Нарушение = баг.
- **В3 Differential:** fixpoint-derived vs PoB-actual — дрейф >δ логируется.
- **В4 Mutation-testing:** намеренно сломать билд → система ловит.
- **В5 Property-based round-trip** (D24).

**15.Дегенераты (D34):** diversification (anti-near-duplicate по flag_set_hash); anti-meta-mimicry
(штраф за частые в BuildHistory); uptime-gaming bound (D28 sanity); sanity-gate (≥1 «очевидно-сильного» в выдаче).

---

## 16. Чек-лист «ответь до кода» — все закрыты (A1–A11)
| Q | Статус | Решение |
|---|--------|---------|
| Q1 версия PoB | ✅ A1 | 3.28 Mirage / PoB **v2.65.0** (verified-by-primary) |
| Q2 reset-стратегия | ✅ A2 | полный rebuild modDB + raid-check (D8) |
| Q3 SkillGroup | ✅ §3 | active_socket_group + active_skill + MVP-инвариант D21 |
| Q4 eldritch в MVP | ✅ | ВКЛЮЧЕНЫ (4 слота, non-influenced D9) |
| Q5 CostModel | ✅ A4 (D5) | crafting-methods; независимые=геом., метакрафт=DP |
| Q6 fixpoint | ✅ A5 (D1) | inconclusive→PoB, 2-cycle detect, bounded-opt |
| Q7 ninja endpoints | ⚠️ A3 (E4) | URL требует DevTools-верификации |
| Q8 plain-tree MVP | ✅ §5.2 (D14) | декомпозиция, без cluster/timeless |
| Q9 веса r(m) | ✅ A6 | эвристика автора Phase 2 |
| Q10 бонусы c(mi,mj) | ✅ A7 (D6+D7) | curated + discovery + PoB-trust |
| Q11 enemy_profile | ✅ A10 (D20/D3) | tiered; `rare`=абсолютный порог |
| Q12 ailment-gates | ✅ A11 (C1) | freeze=hard; chill=soft; stun=soft |
| Q13 LLM-формат | ✅ A8 | строгий JSON + whitelist (D17 role) |

---

## Appendix A — Уровни доверия к фактам (review B1)

| Факт | Уровень | Источник / верификация |
|------|---------|------------------------|
| Патч 3.28 «Mirage» (23.06.2026) | **verified-by-primary** | офиц. patch notes |
| PoB тег | **verified-by-primary** | GitHub API latest = **v2.65.0** (не v2.63.0) |
| Imbued gems | **verified-by-primary** | PoB release notes PR #9793 + patch notes (Runegraft of the Imbued, Djinn coin) |
| Bloodline Ascendancy | **verified-by-primary** | PoB release notes PR #9797 |
| INT→ES nerf 5→10 | **verified-by-primary** | patch notes дословно |
| Thread of Hope import bug | **verified-by-primary** | [PoB issue #9779](https://github.com/PathOfBuildingCommunity/PathOfBuilding/issues/9779) (review B2: ссылка исправлена) |
| Runegrafts (вместо «Graft skills») | **verified-by-primary** | patch notes: 10 Runegrafts; старые Grafts УДАЛЕНЫ в 3.28 |
| eldritch-имплициты в 3.28 | community-source | review F2: сверить активность Eater/Exarch-системы |
| +level-тиры на body (ilvl 1/50/76) | community-source | review F1: сверить точные ilvl-пороги по poedb |
| Influence-cap=2 | **verified-by-primary** | maxroll double-influence |
| Eldritch = non-influenced only | **verified-by-primary** | maxroll/poewiki |
| Synth↔influence взаимоискл. | **verified-by-primary** | poewiki |
| Pantheon upgrades (3 major/1 minor) | **verified-by-primary** | poewiki |
| Flask charge-mechanics | **verified-by-primary** | poewiki/maxroll |
| Damage-uptime коэфф. (traps 0.7-0.9 и т.д.) | unverified-эвристика | review F3: вывести из данных/эталонов |
| ninja URL-паттерн | unverified | E4: DevTools-верификация до Phase 0b |
| RePoE-покрытие 3.28 | unverified | D4-gate: сверка в Phase 0 |

**Принцип (review B1):** ✅ ставится только после сверки с PoB-data / живой игрой / первоисточником,
не с форум-тредами. Patch-specific факты сверенос primary где возможно; оставшиеся помечены
community-source/unverified и требуют сверки в Phase 0/0b.

---

## Appendix B — История верификации и решений (changelog)

> Хронология проходов (для аудита; **итоговые решения — в основном тексте**, не здесь).

- **v0.5 (§18):** errata + первичная верификация. E1 (3.28 реален), E2 (INT-nerf, не усиление),
  E3 (RePoE-покрытие под вопросом), E4 (ninja URL не верифицирован). К-fixpoint, К-Pareto,
  К-unusualness, К-Phase1, К-craft, К-LLM-oracle — критика с альтернативами.
- **v0.6 (§19):** D1 (fixpoint=pre-filter), D2 (reference-point), D3 (Phase 1 = 2 билда, Spark→Arc),
  D4 (RePoE-gate), D5 (крафт DP), D6 (discovery), D7 (PoB-trust), D8 (perf/isolation).
- **v0.7 (§20):** D9 (домен предмета: influence-cap, eldritch/non-infl, synth), D10 (Ward),
  D11 (+level slot-bound), Д1 (links К6), Д2 (jewel mapping), Д3 (правила зависимости).
- **v0.8 (§21):** D14 (дерево декомпозиция, не Steiner), D15 (CP-SAT разрешимость), D16 (skeleton-gate),
  D17 (LLM role).
- **v0.9 (§22):** D18 (flask charge-balance), D19 (Pantheon upgrade-state), D20 (пороги percentile→
  абсолютный D3-review), D21 (MVP-инвариант), D22 (gate структурные/числовые — критика №2).
- **v1.0 (§23):** D23 (PoB-формат/tooling), D24 (round-trip по статам), D25 (validation §15.В),
  D26 (invalidation), D27 (oracle-wins).
- **v1.1 (§24):** D28 (damage-uptime), D29 (buff-uptime-realism), D30 (liquidity), D31 (BuildOutput).
- **v1.2 (§25):** D32 (масштаб/warm-start), D33 (BuildHistory/feedback), D34 (degenerate-guards),
  D35 (D6↔D20 tension), E32 (residual-risk-register).
- **v2.0 (этот документ):** консолидация (review E1) + применение вердиктов ревью A1–F3.
  A1/D1: correctness-first обоснован (§1.3). A2: «мета» — явное решение (§1.4). B1: уровни доверия
  (Appendix A) + исправления Graft→Runegraft, PoB v2.65.0, Thread of Hope #9779. C1: chill→soft.
  C2: Perfect Agony = ailment-DoT. C3: Brine King эффект уточнён. D2: discovery = осмысленное
  расстояние + cold-start. D3: порог абсолютный, не percentile. D4: бюджет мягкий для unusual.
  F1/F2/F3: помечены community-source/unverified для сверки в Phase 0.
- **D37 (2026-06-25, post-Phase-0 spikes):** PoB-first гибридное ядро (§1.2): CP-SAT=feasibility-seeder,
  PoB-in-loop=optimizer, ML-prior=tree/cold-start, gate=max(heuristic,ML). Эмпирика: CP-SAT-proxy 40%,
  sd-target-set 5%, greedy 23–34%, cold-joint 9%.

---

> **Итог v2.0:** это **чистая консолидированная спека** (закрывает review E1), с применёнными
> решениями D1–D37 и вердиктами ревью A1–F3. Документ отражает максимально работоспособную версию,
> достижимую на уровне дизайна. Дальнейшие проблемы — уровня реализации/эксперимента Phase 0/1.
