# WORK-REPORT.md — отчёт о проделанной работе (для следующей нейросети)

> **Назначение:** самодостаточный отчёт о том, что сделано после Phase 0, зачем, с какими
> результатами и какими архитектурными выводами. Читать вместе с `HANDOFF.md` (инфраструктура),
> `SPIKES.md` (цифры спайков), `ROUND2-FINDINGS.md` (критика и незакрытые ставки), `DESIGN-v2.md`
> (канон).
>
> **Дата отчёта:** 2025-06-25. **Статус проекта:** Phase 0 закрыт; decision-gate спайки B/D/E
> пройдены погранично; **Phase 1 ещё не начат** (нет production-реализатора в `poebuildgen/`).

---

## 1. Контекст и цель сессии

Проект: генератор **готовых жизнеспособных билдов** PoE1 с PoB как единственным оракулом.
Стратегия: **correctness-first** (CP-SAT + fixpoint + PoB-in-loop), **не** seed-and-mutate как ядро
(`DESIGN-v2.md` §1.2–1.3).

К моменту начала сессии:
- Phase 0 завершён (headless PoB, gold-match, B1–B7 исправлены).
- Спайк B (CP-SAT по шмоту) был **не сделан** — это decision-gate перед Phase 1.

Задачи сессии (по порядку):
1. Выполнить спайк B на корпусе реальных билдов (`builds/`).
2. Реализовать worker-pool (throughput).
3. Ответить на `ROUND2-FINDINGS.md` (критика «готовности к Phase 1»).
4. Проверить дерево (D14) и joint fixpoint дерево↔шмот.

---

## 2. Что сделано (инфраструктура)

### 2.1 Worker-pool (throughput ×N)

**Проблема:** cold-boot PoB ~2–5 с/оценка; спайк B с hill-climb = 500–1000 оценок → ~12 мин/билд.

**Решение:**
| Файл | Суть |
|---|---|
| `poebuildgen/worker.py` | Режим `--serve`: PoB грузится один раз, задачи по stdin, синхронизация через `.out`/`.done` (stdout PoB засорён) |
| `poebuildgen/pool.py` | `WorkerPool`: N процессов, динамическая раздача, таймаут+респаун |
| `scripts/spikeB/engine.py` | `PoolEngine` (батч-DPS) + `MetaEngine` (один in-process PoB для `data.itemMods` и валидации) |

**Эффект:** полный прогон спайка B с best-in-space **~2 мин** вместо **~12 мин** (6 воркеров).
Корректность сохранена (билд 10: те же 17,628,255 DPS).

**Обоснование:** PoB-in-loop остаётся узким местом по *стоимости кандидата*, но не по *latency
одной сессии*. Пул снимает блокер для масштабирования спайков и Phase 1 прототипов.

### 2.2 Прочие правки

| Изменение | Файл | Зачем |
|---|---|---|
| `caveats()`, `has_unrecognized_mods` | `poebuildgen/validator.py` | `modLine.extra` = warning, не error → тихая потеря DPS в продукте (`ROUND2-FINDINGS` §3) |
| `Build.from_xml()` | `scripts/spikeB/harness.py` | Промежуточный XML при joint-оптимизации |
| D1 fixpoint (2-cycle → avg marginals) | `scripts/spikeB/fixpoint.py` | Политика DESIGN-v2 §5.2, раньше не была реализована |
| Multi-start hybrid + seed stability | `scripts/spikeB/hybrid.py` | Снять риск застревания hill-climb на плохом seed (`ROUND2-FINDINGS` §4) |
| Life-констрейнты PoB-пробами | `scripts/spikeB/life_probe.py`, `solve.py` | Ложный INFEASIBLE на билдах 4/6 |
| `gear_opt.py` | `scripts/spikeB/gear_opt.py` | Переиспользуемая оптимизация шмота для joint |

---

## 3. Спайк B — CP-SAT по шмоту (decision-gate №1)

**Код:** `scripts/spikeB/` (`harness`, `marginals`, `modpool`, `solve`, `run`, …)  
**Запуск:** `python -m scripts.spikeB.run builds/10.txt`

**Метод:** фикс-скелет билда (дерево/гемы/уникалки) → срез эксплицитов редких слотов →
PoB-маржиналы (конечные разности) → CP-SAT (OR-Tools) под craftable-ограничения → fixpoint →
сравнение с **PoB-best-in-space** (hill-climb в том же пуле аффиксов).

### 3.1 Результаты (корпус 6 билдов)

| Билд | Архетип | CP-SAT / best-in-space | вердикт прокси |
|---|---|---|---|
| 10 Penance Brand | спелл/ignite, аддитив | **100%** | PASS |
| 4 Molten Strike | атака, added-ele | **94.8%** | PASS |
| 11 Kinetic Blast | гибрид | **82.9%** | BORDERLINE |
| 8 Frost Blades | конверсия | **72.5%** | BORDERLINE |
| 6 Elemental Hit | атака+крит | **66.5%** | BORDERLINE |
| 2 Flicker | атака-крит | **40.8%** | **FAIL** |

**Градиент:** качество линейного прокси падает по оси **аддитив → мультипликатив/крит**.
Крит — главный убийца прокси (на Flicker PoB-best-in-space нашёл 24.5M vs CP-SAT 10.0M).

**Разрыв «vs реальный шмот»** (напр. 50% на билде 10): глубина крафта вне минимального пула
(fractured, catalysts, …), **не** слабость ядра в рамках одного пула.

### 3.2 Архитектурное решение (принято)

> **Гибридное ядро:** CP-SAT = быстрый генератор **легального seed'а** под ограничения;
> PoB-in-loop (hill-climb / multi-start) = **оптимизатор** для нелинейностей.

Чистый линейный CP-SAT **недостаточен** на крит/конверсия-архетипах. Это согласуется с
`ROUND2-FINDINGS` и усиливает его: гибрид нужен, но **не гарантирует** качество на всех архетипах
(seed stability на билде 4: 30% — greedy/random застревают на baseline).

---

## 4. Спайк D — дерево (D14)

**Код:** `scripts/spikeC/tree_build.py`, `run.py`  
**Запуск:** `python -m scripts.spikeC.run [--oracle|--greedy|--marginal]`

### 4.1 Декомпозиция провала

| режим | overlap эталонных notables | интерпретация |
|---|---|---|
| **full** (sd-теги → target-set + Dijkstra) | 5–23% | FAIL — выбор notables |
| **oracle** (эталонные notables, только Dijkstra) | 96–100% | PASS — построение путей |
| **greedy PoB-in-loop** (marginals от текущего alloc) | 23–34% | BORDERLINE |
| single-shot marginals + Dijkstra | ~5% | FAIL |

**Вывод:** Dijkstra-stitching **работает**. Узкое место — **выбор target-set**, не граф.

### 4.2 Решение для дерева

Не target-set+Dijkstra как финальный реализатор, а **greedy/hill-climb PoB-in-loop** (как для шмота).
Dijkstra — только утилита «проложить путь к выбранному notable».

**Не сделано в спайке:** masteries (нужен effect), cluster jewels, timeless jewels, swap/backtrack
на дереве, dealloc зависимых узлов.

---

## 5. Спайк E — joint fixpoint (дерево ↔ шмот)

**Код:** `scripts/spike_joint/run.py`, `scripts/spikeB/gear_opt.py`  
**Запуск:**
```bash
python -m scripts.spike_joint.run --tree-only          # expert gear, только дерево
python -m scripts.spike_joint.run                      # joint 2 итерации
python -m scripts.spike_joint.run --gear-start stripped  # cold start
```

**Метод (D1 из DESIGN-v2):** чередование фаз:
1. Greedy tree (PoB-маржиналы по notables, кратчайший путь через Dijkstra)
2. CP-SAT + hybrid gear (от текущего состояния шмота)

### 5.1 Результаты

**Tree-only** (экспертный шмот, дерево с minimal до бюджета):

| билд | DPS % от ref | tree overlap |
|---|---|---|
| 10 | 19.4% | 31.6% |
| 8 | 13.2% | 26.7% |
| 2 | 6.1% | 20.0% |

**Cold joint** (stripped rares + minimal tree, билд 10): **~9%** DPS.

### 5.2 Ключевой вывод (важнее цифр overlap)

**Слои нельзя оптимизировать независимо:**
- Spike B (экспертное дерево + оптимизация редких): **50–100%** ref DPS (по архетипу)
- Spike E tree-only (экспертный шмот + greedy дерево): **6–19%** ref DPS

Дерево и шмот **сопряжены**. Joint fixpoint обязателен в Phase 1, но **cold start с нуля не
дотягивает** — нужен warm-start от скелета/axis-descriptor или tree hill-climb (swap notables).

---

## 6. Таблица статуса рисков (честная, после всей работы)

| Риск | Статус | Комментарий |
|---|---|---|
| PoB-оракул (спайк A / Phase 0) | ✅ **прочно** | gold 0.000%, 1 билд в golden — мало |
| CP-SAT + гибрид шмот (спайк B) | 🟡 **погранично** | PASS на аддитиве, FAIL на Flicker-крите |
| Дерево D14 (спайк D/D2) | 🟡 **погранично** | stitching OK, greedy overlap 23–34% |
| Joint fixpoint (спайк E) | 🟡 **погранично** | cold ~9%, tree-only 6–19% даже с expert gear |
| Экономика/крафт (К5) | 🔴 не начат | ±100% риск на «vs реальный» |
| Гемы/линки (К4/К6) | 🔴 не начат | фиксированы во всех спайках |
| Спайк C (ценность H_C1/H_C2) | 🔴 не сделан | осознанно отложен |
| Golden-корпус (5–7 архетипов) | 🔴 не расширен | 1 билд в тестах |

**Итог:** «Phase 0 готов, Phase 1 можно начинать» — **только с оговорками**. Фундамент PoB стоит;
реализатор проверен **прототипами в `scripts/`**, не как библиотека в `poebuildgen/`.

---

## 7. Ответ на ROUND2-FINDINGS (что принято / что оспорено)

| Пункт ROUND2 | Наша позиция |
|---|---|
| «Готов к Phase 1» преувеличено | **Согласны.** 1.5/5 крупных рисков закрыто погранично |
| Расширить корпус CP-SAT до 20 билдов | **Частично:** корпус B = 6; важнее **гибрид**, не чистый CP-SAT |
| Multi-start + seed stability | **Сделано** (`hybrid.py`); instability подтверждена на билде 4 |
| D1 fixpoint явно | **Сделано** (`fixpoint.py`); в спайке B раньше был «оставить лучший» |
| modLine.extra для продукта | **Сделано** (`caveats()`); research-режим оставляет warning |
| Дерево — главный непроверенный риск | **Подтверждено спайками D/E** |
| H_C1 vs H_C2 не задокументирован | **Всё ещё не сделано** — нужен §1.5 в DESIGN-v2 |

---

## 8. Карта кода (куда смотреть)

```
poebuildgen/
  worker.py          # --serve тёплый воркер
  pool.py            # WorkerPool
  headless.py        # PobHeadless (Phase 0)
  validator.py       # + caveats для modLine.extra
  model.py           # Build/Tree/Spec XML model

scripts/spikeB/      # спайк B: шмот
  run.py             # end-to-end
  gear_opt.py        # optimize_gear (для joint)
  fixpoint.py, hybrid.py, life_probe.py, solve.py, ...

scripts/spikeC/      # спайк D: дерево
  tree_build.py      # граф, Dijkstra, sd-scoring
  tree_marginals.py  # greedy tree, PoB-маржиналы
  tree_xml.py        # правка nodes в XML
  run.py             # батч --oracle|--greedy|--marginal

scripts/spike_joint/ # спайк E: joint
  run.py             # fixpoint tree <-> gear

builds/              # эталонные билды (1.txt … 11.txt)
SPIKES.md            # цифры и вердикты
HANDOFF.md           # Phase 0 + follow-up
ROUND2-FINDINGS.md   # критика для ревью
DESIGN-v2.md         # каноническая спека
```

---

## 9. Рекомендации для следующей нейросети (приоритеты)

### 9.1 Не делать без обсуждения
- Не начинать Phase 1 как «большой рефакторинг всего DESIGN-v2» — риски локализованы.
- Не возвращать seed-and-mutate как ядро — отвергнуто в §1.3 DESIGN-v2.
- Не считать overlap notables = качество билда — эксперт оптимизирует DPS, не совпадение имён.

### 9.2 Следующие шаги (по убыванию ценности)

1. **Tree hill-climb** (swap notable / dealloc ветки) поверх greedy — в `scripts/spikeC/` или сразу в
   `poebuildgen/realizer/tree.py`. Ожидаемый прирост overlap и DPS%.

2. **Warm-start дерева от скелета** (axis-descriptor из DESIGN-v2 §3), не с `minimal` (class start +
   ascend). Без скелет-генератора Phase 1 — использовать class+ascendancy+skill как слабый скелет.

3. **Перенос прототипов в `poebuildgen/realizer/`** — единый API:
   `realize(build_skeleton) -> PobBuild` с joint fixpoint 2–3 итерации.

4. **Golden-корпус** 5–7 архетипов в `tests/` — иначе регрессии не видны.

5. **Зафиксировать H_C1 vs H_C2** в DESIGN-v2 §1.5 (стратегическая ставка throughput).

6. **Почистить строки пула** (`item_problems`) — занижают абсолютный DPS, не влияют на вердикт
   спайка (пул одинаков для CP-SAT и best-in-space).

### 9.3 Критерии готовности Phase 1 (предложение)

- Joint realizer на warm-start: **≥40% ref DPS** на 3+ архетипах из корпуса (сейчас cold 9%,
  tree-only 6–19%).
- Gear hybrid: **≥85% best-in-space** на аддитивных; **≥60%** на конверсия; крит — явный caveat.
- Валидатор: gem/socket/attribute constraints (К6) — минимум для «готового билда».

---

## 10. Команды для воспроизведения

```bash
# Спайк B (один билд, hybrid)
python -m scripts.spikeB.run builds/10.txt --bis-evals 400

# Спайк D (дерево, greedy)
python -m scripts.spikeC.run --greedy --max-candidates 35

# Спайк E (joint)
python -m scripts.spike_joint.run --tree-only --tree-rounds 25
python -m scripts.spike_joint.run --joint-iters 2

# Тесты Phase 0
pytest tests/
```

---

## 11. Главная мысль для преемника

Проект **не провалил ставку** — он **честно измерил границы** линейного CP-SAT и sd-эвристик
дерева. Выигрышная архитектура Phase 1 уже видна из данных:

```
скелет → joint fixpoint (2–3×):
  [PoB-greedy/hill-climb дерево] ↔ [CP-SAT seed + PoB-hybrid шмот]
→ gate'ы → fitness → BuildOutput (+ caveats)
```

PoB-in-loop — не оптимизация поверх «готового ядра», а **само ядро** для обоих слоёв. CP-SAT —
вспомогательный солвер ограничений на шмоте. Дерево — почти чистый PoB-in-loop.

Не переоценивай готовность: экономика, гемы, кластера, discovery — впереди. Но и не откатывайся к
seed-and-mutate: спайки показали, **легальность + PoB-оракул** достижимы; проблема в **качестве
поиска**, не в интеграции PoB.

---

*Документ создан для передачи контекста. Не удаляет и не заменяет `HANDOFF.md` / `SPIKES.md`.*
