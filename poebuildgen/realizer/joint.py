from __future__ import annotations

import xml.etree.ElementTree as ET
from typing import Optional

from poebuildgen.model import PobBuild
from poebuildgen.pool import WorkerPool, _dps_from


class JointRealizer:
    def __init__(
        self,
        pool: WorkerPool,
        model_path: Optional[str] = None,
        config_path: Optional[str] = None,
    ) -> None:
        self.pool = pool
        self.model_path = model_path
        self.config_path = config_path
        self._prefer = "CombinedDPS"

    def realize(
        self,
        build: PobBuild,
        *,
        budget: float,
        gear_start: str = "stripped",
        tree_start: str = "ml",
        joint_iters: int = 2,
        tree_rounds: int = 25,
        life_frac: float = 0.6,
        tree_only: bool = False,
    ) -> PobBuild:
        """
        Runs the joint tree and gear optimization fixpoint loop.
        Supports tree_start="both" to try both seeds and pick the highest PoB DPS result.
        """
        if tree_start == "both":
            build_ml = self._realize_single(
                build,
                budget=budget,
                gear_start=gear_start,
                tree_start="ml",
                joint_iters=joint_iters,
                tree_rounds=tree_rounds,
                life_frac=life_frac,
                tree_only=tree_only,
            )
            build_min = self._realize_single(
                build,
                budget=budget,
                gear_start=gear_start,
                tree_start="minimal",
                joint_iters=joint_iters,
                tree_rounds=tree_rounds,
                life_frac=life_frac,
                tree_only=tree_only,
            )

            res_ml, res_min = self.pool.map([
                {"xml": build_ml.to_xml(), "stats": ["CombinedDPS", "TotalDPS", "FullDPS"]},
                {"xml": build_min.to_xml(), "stats": ["CombinedDPS", "TotalDPS", "FullDPS"]},
            ])

            from poebuildgen.realizer.tree import DPS_KEYS
            dps_ml = _dps_from(res_ml, self._prefer, DPS_KEYS)
            dps_min = _dps_from(res_min, self._prefer, DPS_KEYS)

            if dps_ml >= dps_min:
                return build_ml
            else:
                return build_min

        return self._realize_single(
            build,
            budget=budget,
            gear_start=gear_start,
            tree_start=tree_start,
            joint_iters=joint_iters,
            tree_rounds=tree_rounds,
            life_frac=life_frac,
            tree_only=tree_only,
        )

    def _realize_single(
        self,
        build: PobBuild,
        *,
        budget: float,
        gear_start: str,
        tree_start: str,
        joint_iters: int,
        tree_rounds: int,
        life_frac: float,
        tree_only: bool,
    ) -> PobBuild:
        model, meta, backend = None, None, None
        if tree_start == "ml":
            try:
                from poebuildgen.realizer.tree import load_ml_model
                model, meta, backend = load_ml_model(self.model_path, self.config_path)
            except Exception as exc:
                print(f"Warning: Failed to load ML model, falling back to minimal heuristic tree: {exc}")
                tree_start = "minimal"

        xml_ref = build.to_xml()
        res_ref = self.pool.map([
            {"xml": xml_ref, "stats": ["CombinedDPS", "TotalDPS", "FullDPS", "Life"]}
        ])[0]

        if not res_ref.get("ok"):
            raise RuntimeError(f"Failed to evaluate reference build: {res_ref.get('error')}")

        stats_ref = res_ref.get("stats", {})
        from poebuildgen.realizer.tree import DPS_KEYS
        best_key, bv = "CombinedDPS", -1.0
        for k in DPS_KEYS:
            v = stats_ref.get(k) or 0.0
            if v > bv:
                best_key, bv = k, v
        self._prefer = best_key

        ref_dps = float(stats_ref.get(self._prefer) or 0.0)
        fingerprint = {
            "CombinedDPS": stats_ref.get("CombinedDPS"),
            "TotalDPS": stats_ref.get("TotalDPS"),
            "Life": stats_ref.get("Life"),
        }

        # Query tree graph
        res_graph = self.pool.map([
            {"xml": xml_ref, "want_tree_graph": True}
        ])[0]
        if not res_graph.get("ok") or "tree_graph" not in res_graph:
            raise RuntimeError(f"Failed to retrieve tree graph: {res_graph.get('error')}")

        tg_raw = res_graph["tree_graph"]
        from poebuildgen.realizer.tree import TreeGraph, Node, split_main_ascend
        nodes = {nid: Node(nid=nid, type=n["type"], dn=n["dn"], linked=n["linked"], sd=n["sd"], ascendancy=n["ascendancy"])
                 for nid, n in tg_raw["nodes"].items()}
        graph = TreeGraph(
            nodes=nodes,
            class_start=tg_raw["class_start"],
            cur_class=tg_raw["cur_class"],
            cur_ascend=tg_raw["cur_ascend"],
            class_id=tg_raw["class_id"],
            allocated=set(tg_raw["allocated"]),
            points_total=int(budget) if budget is not None else tg_raw["points_total"],
            points_ascend=tg_raw["points_ascend"],
        )

        from poebuildgen.realizer.gear import Build
        b_ref = Build.from_xml(xml_ref)
        opt_slots = b_ref.rare_core_slots()

        if gear_start == "expert":
            gear_ov = {s: (b_ref.by_id[b_ref.slot_to_id[s]].text or "") for s in opt_slots}
        else:
            gear_ov = {s: b_ref.item_for_slot(s).stripped() for s in opt_slots}

        _, ascend = split_main_ascend(graph)
        if tree_start == "expert":
            tree_alloc = set(graph.allocated)
        elif tree_start == "ml" and model is not None:
            from poebuildgen.realizer.tree import predict_tree_alloc
            tree_alloc = predict_tree_alloc(
                xml_ref, model, meta, backend, self.pool, graph,
                prefer=self._prefer, fingerprint=fingerprint
            )
        else:
            tree_alloc = set(ascend) | {graph.class_start}

        from poebuildgen.realizer.tree import parse_mastery_effects
        spec_el = ET.fromstring(xml_ref).find("Tree").findall("Spec")[0]
        etalon_mastery = parse_mastery_effects(spec_el)

        def get_combined_xml(t_alloc, g_ov):
            gxml = b_ref.render(g_ov)
            from poebuildgen.realizer.tree import render_tree_nodes, mastery_subset
            return render_tree_nodes(gxml, t_alloc, mastery_subset(etalon_mastery, t_alloc))

        prev_dps = 0.0
        for k in range(joint_iters):
            cx = get_combined_xml(tree_alloc, gear_ov)
            if tree_start == "ml" and model is not None:
                from poebuildgen.realizer.tree import predict_tree_alloc
                tree_alloc = predict_tree_alloc(
                    cx, model, meta, backend, self.pool, graph,
                    prefer=self._prefer, fingerprint=fingerprint
                )
            elif tree_start == "minimal":
                from poebuildgen.realizer.tree import optimize_tree_heuristic
                tree_alloc = optimize_tree_heuristic(
                    cx, graph, self.pool,
                    prefer=self._prefer, max_greedy_rounds=tree_rounds, fingerprint=fingerprint
                )

            if not tree_only:
                cx = get_combined_xml(tree_alloc, gear_ov)
                b_cur = Build.from_xml(cx)
                from poebuildgen.realizer.gear import optimize_gear
                dps_cur, gear_ov = optimize_gear(
                    self.pool,
                    None,
                    b_cur,
                    b_ref,
                    opt_slots,
                    self._prefer,
                    gear_ov,
                    life_frac=life_frac,
                    fingerprint=fingerprint,
                )
            else:
                cx = get_combined_xml(tree_alloc, gear_ov)
                res_cur = self.pool.map([
                    {"xml": cx, "stats": [self._prefer]}
                ])[0]
                dps_cur = _dps_from(res_cur, self._prefer, DPS_KEYS)

            if prev_dps > 0 and abs(dps_cur - prev_dps) / prev_dps <= 0.005:
                break
            prev_dps = dps_cur

        final_xml = get_combined_xml(tree_alloc, gear_ov)
        return PobBuild.from_xml(final_xml)
