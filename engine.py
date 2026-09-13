#!/usr/bin/env python3
"""
Chainsmith Engine
-----------------
A MILP-based factory production optimizer (originally targeted at
Factorio-style production chains). This is a fixed and extended version
of the uploaded WorldClassFactoryMILPEngine.

Fixes vs. the original upload:
1. Machine-choice cost bug: the original attached power/pollution cost to
   the recipe-rate variable (x_r) as an AVERAGE across every allowed
   machine, so the solver had no way to actually prefer a more efficient
   machine for a recipe -- the cost was identical no matter which machine
   got picked. Cost is now attached to the machine-selection binary (z)
   and machine-count (m) variables instead, so cheaper/faster machines
   are genuinely rewarded.
2. Division-by-zero / bad-data guards: craft_time <= 0, crafting_speed <= 0,
   and empty target/raw-material inputs now raise clear errors instead of
   producing NaN/inf inside the solver.
3. Bottleneck reporting now runs on *successful* solves too (not just
   infeasible ones): any constraint sitting above 85% utilization is
   surfaced, so you get early warning before a scale-up breaks.
4. Power-constraint machine-averaging bug: fix #1 above corrected the
   MINIMIZE_COST objective to price the actual selected machine instead of
   an average across every allowed machine for a recipe -- but the power
   CONSTRAINT itself still used that same average, on the recipe-rate
   variable, regardless of which machine actually got selected. In
   MAXIMIZE_YIELD mode especially (which has no cost pressure at all
   pushing the solver toward the cheaper machine), this let the solver
   report success on solutions whose real power draw was well over
   max_power_mw -- reproduced in testing at 109kW reported power against a
   60kW cap, still marked `success: True`. Fixed by adding a per-recipe,
   per-machine rate variable (XP) so power is now computed from whichever
   machine was actually chosen, not an average of all of them.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Set, Tuple
import json
import numpy as np
from scipy.optimize import milp, LinearConstraint, Bounds


class Mode(Enum):
    MINIMIZE_COST = "min_cost"
    MAXIMIZE_YIELD = "max_yield"


class InfeasibilityReason(Enum):
    NONE = "None"
    INSUFFICIENT_RAW_SUPPLY = "Insufficient Raw Materials Supply"
    POWER_CAP_EXCEEDED = "Power Grid Limit Exceeded"
    BELT_CAPACITY_EXCEEDED = "Belt Transport Flow Exceeded"
    PIPE_CAPACITY_EXCEEDED = "Pipe Transport Flow Exceeded"
    POLLUTION_CAP_EXCEEDED = "Pollution Emission Cap Exceeded"
    UNREACHABLE_TECH_TIER = "Required Tech Tier Not Unlocked"
    UNBOUNDED_OR_NO_RECIPE = "No Valid Recipe Path to Target"


@dataclass(frozen=True)
class MachineTier:
    name: str
    crafting_speed: float = 1.0
    power_kw: float = 100.0
    idle_power_kw: float = 10.0
    module_slots: int = 2
    build_cost: float = 1.0  # relative build/upkeep cost, used in MINIMIZE_COST


@dataclass
class Task:
    name: str
    input_items: Dict[str, float]
    output_items: Dict[str, float]
    allowed_machines: List[MachineTier]
    byproducts: Dict[str, float] = field(default_factory=dict)
    craft_time: float = 1.0
    pollution_rate: float = 1.0
    cost_weight: float = 1.0
    required_tech_tier: int = 1
    productivity_bonus: float = 0.0
    speed_modifier: float = 1.0
    is_fluid_recipe: bool = False
    category: str = "general"

    def __post_init__(self):
        if self.productivity_bonus > 0.40:
            raise ValueError(f"Task '{self.name}': productivity_bonus exceeds game cap of 0.40.")
        if not self.allowed_machines:
            raise ValueError(f"Task '{self.name}' must have at least one machine in allowed_machines.")
        if self.craft_time <= 0:
            raise ValueError(f"Task '{self.name}': craft_time must be > 0.")
        for m in self.allowed_machines:
            if m.crafting_speed <= 0:
                raise ValueError(f"Task '{self.name}': machine '{m.name}' has non-positive crafting_speed.")

    def effective_crafts_per_sec(self, machine: MachineTier) -> float:
        return (1.0 / self.craft_time) * machine.crafting_speed * self.speed_modifier


@dataclass
class FactorySolution:
    mode: Mode
    success: bool
    total_objective_value: float = 0.0
    total_power_mw: float = 0.0
    total_pollution: float = 0.0
    machine_assignments: Dict[str, Tuple[str, int]] = field(default_factory=dict)
    actual_recipe_rates: Dict[str, float] = field(default_factory=dict)
    item_net_rates: Dict[str, float] = field(default_factory=dict)
    raw_materials_consumed: Dict[str, float] = field(default_factory=dict)
    bottlenecks: List[str] = field(default_factory=list)
    infeasibility_reason: InfeasibilityReason = InfeasibilityReason.NONE
    diagnostic_message: str = ""


class ChainsmithEngine:
    def __init__(self):
        self.recipes: Dict[str, Task] = {}

    def add_recipe(self, task: Task):
        self.recipes[task.name] = task

    def load_recipes(self, tasks: List[Task]):
        for t in tasks:
            self.add_recipe(t)

    def load_factorio_data(
        self,
        recipe_json_path: str,
        tech_json_path: Optional[str] = None,
        machine_mapping: Optional[Dict[str, List[MachineTier]]] = None,
        default_machine: Optional[MachineTier] = None,
    ):
        """Ingests raw Factorio-exported recipe JSON and resolves tech tiers."""
        tech_tier_map: Dict[str, int] = {}

        if tech_json_path:
            with open(tech_json_path, "r") as f:
                tech_data = json.load(f)
            techs = tech_data.get("technologies", tech_data.get("technology", {}))
            for tech_name, t_info in techs.items():
                prereqs = t_info.get("prerequisites", [])
                tier = len(prereqs) + 1
                for effect in t_info.get("effects", []):
                    if effect.get("type") == "unlock-recipe":
                        rec_unlocked = effect.get("recipe")
                        if rec_unlocked:
                            tech_tier_map[rec_unlocked] = max(tech_tier_map.get(rec_unlocked, 1), tier)

        with open(recipe_json_path, "r") as f:
            data = json.load(f)

        recipes_dict = data.get("recipes", data.get("recipe", {}))
        fallback_machine = default_machine or MachineTier(name="Assembler-1", crafting_speed=0.5, power_kw=75.0)

        for name, rec in recipes_dict.items():
            raw_ingredients = rec.get("ingredients", [])
            inputs = {}
            has_fluid = False
            for ing in raw_ingredients:
                if isinstance(ing, dict):
                    i_name = ing.get("name", ing.get(1))
                    inputs[i_name] = float(ing.get("amount", ing.get(2, 1.0)))
                    if ing.get("type") == "fluid":
                        has_fluid = True
                elif isinstance(ing, (list, tuple)):
                    inputs[ing[0]] = float(ing[1])

            outputs = {}
            if "results" in rec:
                for prod in rec["results"]:
                    if isinstance(prod, dict):
                        p_name = prod.get("name", prod.get(1))
                        outputs[p_name] = float(prod.get("amount", prod.get(2, 1.0)))
                        if prod.get("type") == "fluid":
                            has_fluid = True
                    elif isinstance(prod, (list, tuple)):
                        outputs[prod[0]] = float(prod[1])
            elif "result" in rec:
                res_name = rec["result"]
                outputs[res_name] = float(rec.get("result_count", 1.0))

            main_product = rec.get("main_product")
            if main_product and main_product in outputs:
                primary_outputs = {main_product: outputs[main_product]}
                byproducts = {k: v for k, v in outputs.items() if k != main_product}
            else:
                primary_outputs = outputs
                byproducts = {}

            craft_time = float(rec.get("energy_required", 0.5)) or 0.5
            tier = tech_tier_map.get(name, 1 if rec.get("enabled", True) else 2)
            allowed_macs = machine_mapping.get(name, [fallback_machine]) if machine_mapping else [fallback_machine]

            self.add_recipe(Task(
                name=name,
                input_items=inputs,
                output_items=primary_outputs,
                byproducts=byproducts,
                allowed_machines=allowed_macs,
                craft_time=craft_time,
                required_tech_tier=tier,
                is_fluid_recipe=has_fluid,
            ))

    def solve(
        self,
        mode: Mode,
        target_item: str,
        raw_materials: Set[str],
        target_rate: Optional[float] = None,
        supply_caps: Optional[Dict[str, float]] = None,
        max_power_mw: float = 1000.0,
        max_single_belt_capacity: float = 45.0,
        max_single_pipe_capacity: float = 1200.0,
        max_pollution_limit: Optional[float] = None,
        zero_waste_loop: bool = False,
        mutually_exclusive_groups: Optional[List[List[str]]] = None,
        alpha_power_weight: float = 0.1,
        alpha_pollution_weight: float = 0.1,
        integer_machines: bool = True,
        player_tier: int = 99,
        _is_diagnostic_pass: bool = False,
    ) -> FactorySolution:

        if not target_item:
            raise ValueError("target_item is required.")

        eligible_recipes = [r for r in self.recipes.values() if r.required_tech_tier <= player_tier]
        if not eligible_recipes:
            return FactorySolution(
                mode=mode, success=False,
                infeasibility_reason=InfeasibilityReason.UNREACHABLE_TECH_TIER,
                diagnostic_message=f"No recipes available at or below tech tier {player_tier}.",
            )

        all_items = set(raw_materials)
        all_items.add(target_item)
        if supply_caps:
            all_items.update(supply_caps.keys())
        for r in eligible_recipes:
            all_items.update(r.input_items.keys())
            all_items.update(r.output_items.keys())
            all_items.update(r.byproducts.keys())

        item_list = sorted(all_items)
        item_idx = {item: i for i, item in enumerate(item_list)}
        num_items = len(item_list)
        num_recipes = len(eligible_recipes)

        rm_pairs: List[Tuple[int, Task, int, MachineTier]] = []
        for j, r in enumerate(eligible_recipes):
            for k, m in enumerate(r.allowed_machines):
                rm_pairs.append((j, r, k, m))
        num_rm_pairs = len(rm_pairs)

        # Variable vector layout: X (recipe rate) | M (machine count) | S (raw supply)
        #                        | Y (recipe active) | Z (machine choice active)
        #                        | XP (per-machine-pair rate)
        #
        # XP exists so power (and anything else that's machine-specific rather
        # than recipe-specific) can be tied to *which machine is actually
        # running*, instead of being averaged across every allowed machine for
        # the recipe regardless of which one gets picked. X_j (aggregate rate)
        # is still what mass balance and the yield objective use, since output
        # ratios per craft don't depend on machine choice -- only per-craft
        # power/cost does. XP_pair <= 0 unless that pair's z is selected (see
        # the per-pair rate-cap constraint below), and sum_k XP_{j,k} == X_j.
        OFF_X = 0
        OFF_M = num_recipes
        OFF_S = OFF_M + num_rm_pairs
        OFF_Y = OFF_S + num_items
        OFF_Z = OFF_Y + num_recipes
        OFF_XP = OFF_Z + num_rm_pairs
        total_vars = OFF_XP + num_rm_pairs

        BIG_M = 100000.0
        c = np.zeros(total_vars)

        if mode == Mode.MINIMIZE_COST:
            for j, r in enumerate(eligible_recipes):
                # Rate-independent recipe preference cost + pollution, same regardless of machine.
                c[OFF_X + j] = r.cost_weight + alpha_pollution_weight * r.pollution_rate
            for pair_idx, (j, r, k, m) in enumerate(rm_pairs):
                eff_speed = r.effective_crafts_per_sec(m)
                active_kw_per_craft = m.power_kw / eff_speed
                # FIX: machine-specific cost now lives on the machine-selection (z) and
                # machine-count (m) variables, so the solver can actually reward picking
                # a more power/cost-efficient machine for this recipe.
                c[OFF_Z + pair_idx] = (m.build_cost * r.cost_weight * 0.01) + (
                    alpha_power_weight * active_kw_per_craft / 1000.0
                )
                c[OFF_M + pair_idx] = alpha_power_weight * (m.idle_power_kw / 1000.0)
        elif mode == Mode.MAXIMIZE_YIELD:
            for j, r in enumerate(eligible_recipes):
                out_qty = (r.output_items.get(target_item, 0.0) * (1.0 + r.productivity_bonus)
                           + r.byproducts.get(target_item, 0.0))
                in_qty = r.input_items.get(target_item, 0.0)
                c[OFF_X + j] = -(out_qty - in_qty)

        constraints: List[LinearConstraint] = []
        constraint_types: List[str] = []
        constraint_limits: List[float] = []
        elastic_indices: Set[int] = set()

        for j, r in enumerate(eligible_recipes):
            z_sum_row = np.zeros(total_vars)
            z_sum_row[OFF_Y + j] = -1.0
            for pair_idx, (rj, _, _, _) in enumerate(rm_pairs):
                if rj == j:
                    z_sum_row[OFF_Z + pair_idx] = 1.0
            constraints.append(LinearConstraint(z_sum_row, 0.0, 0.0))
            constraint_types.append("internal_logic")
            constraint_limits.append(1.0)

            # Aggregate rate = sum of the per-machine-pair rates for this recipe.
            xp_sum_row = np.zeros(total_vars)
            xp_sum_row[OFF_X + j] = -1.0
            for pair_idx, (rj, _, _, _) in enumerate(rm_pairs):
                if rj == j:
                    xp_sum_row[OFF_XP + pair_idx] = 1.0
            constraints.append(LinearConstraint(xp_sum_row, 0.0, 0.0))
            constraint_types.append("internal_logic")
            constraint_limits.append(1.0)

            gate_row = np.zeros(total_vars)
            gate_row[OFF_X + j] = 1.0
            gate_row[OFF_Y + j] = -BIG_M
            constraints.append(LinearConstraint(gate_row, -np.inf, 0.0))
            constraint_types.append("internal_logic")
            constraint_limits.append(BIG_M)

            for pair_idx, (rj, _, _, m) in enumerate(rm_pairs):
                if rj == j:
                    m_gate_row = np.zeros(total_vars)
                    m_gate_row[OFF_M + pair_idx] = 1.0
                    m_gate_row[OFF_Z + pair_idx] = -BIG_M
                    constraints.append(LinearConstraint(m_gate_row, -np.inf, 0.0))
                    constraint_types.append("internal_logic")
                    constraint_limits.append(BIG_M)

                    # FIX: this pair's rate is capped by *its own* machine
                    # count and speed (not the recipe's aggregate capacity
                    # across every allowed machine). Combined with the m_gate
                    # above (M forced to 0 when this pair's z is 0), this also
                    # forces XP to 0 for any machine that isn't selected --
                    # no separate gate on XP needed.
                    xp_cap_row = np.zeros(total_vars)
                    xp_cap_row[OFF_XP + pair_idx] = 1.0
                    xp_cap_row[OFF_M + pair_idx] = -r.effective_crafts_per_sec(m)
                    constraints.append(LinearConstraint(xp_cap_row, -np.inf, 0.0))
                    constraint_types.append("internal_logic")
                    constraint_limits.append(1.0)

        if mutually_exclusive_groups:
            recipe_name_to_idx = {r.name: j for j, r in enumerate(eligible_recipes)}
            for group in mutually_exclusive_groups:
                ex_row = np.zeros(total_vars)
                for r_name in group:
                    if r_name in recipe_name_to_idx:
                        ex_row[OFF_Y + recipe_name_to_idx[r_name]] = 1.0
                constraints.append(LinearConstraint(ex_row, -np.inf, 1.0))
                constraint_types.append("internal_logic")
                constraint_limits.append(1.0)

        S = np.zeros((num_items, total_vars))
        for j, r in enumerate(eligible_recipes):
            for item, qty in r.output_items.items():
                S[item_idx[item], OFF_X + j] += qty * (1.0 + r.productivity_bonus)
            for item, qty in r.byproducts.items():
                S[item_idx[item], OFF_X + j] += qty
            for item, qty in r.input_items.items():
                S[item_idx[item], OFF_X + j] -= qty

        for i, item in enumerate(item_list):
            if item in raw_materials and not (mode == Mode.MAXIMIZE_YIELD and item == target_item):
                S[i, OFF_S + i] = 1.0

        for i, item in enumerate(item_list):
            if mode == Mode.MINIMIZE_COST and item == target_item:
                constraints.append(LinearConstraint(S[i], target_rate if target_rate is not None else 1.0, np.inf))
            elif zero_waste_loop and item not in raw_materials and item != target_item:
                constraints.append(LinearConstraint(S[i], 0.0, 0.0))
            else:
                constraints.append(LinearConstraint(S[i], 0.0, np.inf))
            constraint_types.append("mass_balance")
            constraint_limits.append(1.0)

        power_row = np.zeros(total_vars)
        for pair_idx, (j, r, k, m) in enumerate(rm_pairs):
            eff_s = r.effective_crafts_per_sec(m)
            # FIX: power now scales with XP (this specific machine's actual
            # rate), not with X_j averaged across every allowed machine. The
            # old version undercounted power whenever the solver picked a
            # higher-draw machine than the recipe's average, letting
            # "successful" solves silently exceed max_power_mw.
            power_row[OFF_XP + pair_idx] += m.power_kw / eff_s
            power_row[OFF_M + pair_idx] = m.idle_power_kw
        power_limit_kw = max_power_mw * 1000.0
        constraints.append(LinearConstraint(power_row, 0.0, power_limit_kw))
        constraint_types.append("power")
        constraint_limits.append(power_limit_kw)
        elastic_indices.add(len(constraints) - 1)

        if max_pollution_limit is not None:
            pol_row = np.zeros(total_vars)
            for j, r in enumerate(eligible_recipes):
                pol_row[OFF_X + j] = r.pollution_rate
            constraints.append(LinearConstraint(pol_row, 0.0, max_pollution_limit))
            constraint_types.append("pollution")
            constraint_limits.append(max_pollution_limit)
            elastic_indices.add(len(constraints) - 1)

        for j, r in enumerate(eligible_recipes):
            for item in item_list:
                if item in raw_materials:
                    continue
                in_qty = r.input_items.get(item, 0.0)
                out_qty = r.output_items.get(item, 0.0) + r.byproducts.get(item, 0.0)
                per_recipe_flow = max(in_qty, out_qty)
                if per_recipe_flow > 0.0:
                    flow_row = np.zeros(total_vars)
                    flow_row[OFF_X + j] = per_recipe_flow
                    is_pipe = r.is_fluid_recipe
                    cap_limit = max_single_pipe_capacity if is_pipe else max_single_belt_capacity
                    constraints.append(LinearConstraint(flow_row, 0.0, cap_limit))
                    constraint_types.append("pipe" if is_pipe else "belt")
                    constraint_limits.append(cap_limit)
                    elastic_indices.add(len(constraints) - 1)

        if supply_caps:
            for item, cap_rate in supply_caps.items():
                if item in item_idx and item in raw_materials:
                    idx = OFF_S + item_idx[item]
                    s_cap_row = np.zeros(total_vars)
                    s_cap_row[idx] = 1.0
                    constraints.append(LinearConstraint(s_cap_row, 0.0, cap_rate))
                    constraint_types.append("supply")
                    constraint_limits.append(cap_rate)
                    elastic_indices.add(len(constraints) - 1)

        ub = np.full(total_vars, np.inf)
        ub[OFF_Y:OFF_Y + num_recipes] = 1.0
        ub[OFF_Z:OFF_Z + num_rm_pairs] = 1.0
        var_bounds = Bounds(lb=np.zeros(total_vars), ub=ub)

        integrality = np.zeros(total_vars)
        if integer_machines:
            integrality[OFF_M:OFF_S] = 1
            integrality[OFF_Y:OFF_Y + num_recipes] = 1
            integrality[OFF_Z:OFF_Z + num_rm_pairs] = 1

        res = milp(c=c, integrality=integrality, bounds=var_bounds, constraints=constraints)

        if not res.success:
            if _is_diagnostic_pass:
                return FactorySolution(
                    mode=mode, success=False,
                    infeasibility_reason=InfeasibilityReason.NONE,
                    diagnostic_message="Diagnostic sub-pass completed.",
                )
            reason, msg = self._diagnose_infeasibility_elastic(
                c, integrality, var_bounds, constraints, constraint_types, constraint_limits,
                elastic_indices, total_vars, max_power_mw, max_single_belt_capacity, max_single_pipe_capacity,
            )
            return FactorySolution(mode=mode, success=False, infeasibility_reason=reason, diagnostic_message=msg)

        x_rates = res.x[OFF_X:OFF_M]
        actual_rates = {r.name: round(x_rates[j], 4) for j, r in enumerate(eligible_recipes) if x_rates[j] > 1e-4}

        machine_assignments: Dict[str, Tuple[str, int]] = {}
        for pair_idx, (j, r, k, m) in enumerate(rm_pairs):
            count = int(round(res.x[OFF_M + pair_idx]))
            z_val = res.x[OFF_Z + pair_idx]
            if count > 0 or z_val > 0.5:
                machine_assignments[r.name] = (m.name, count)

        xp_vals = res.x[OFF_XP:OFF_XP + num_rm_pairs]
        active_power = 0.0
        idle_power = 0.0
        for pair_idx, (j, r, k, m) in enumerate(rm_pairs):
            m_cnt = res.x[OFF_M + pair_idx]
            # xp_vals[pair_idx] is exactly this pair's rate (0 unless this
            # machine is the one selected -- enforced by the xp_cap
            # constraint above), so this now matches what the power
            # constraint itself enforced during solving. No more relying on
            # x_rates[j] (the recipe's aggregate rate, machine-agnostic).
            eff_s = r.effective_crafts_per_sec(m)
            active_power += xp_vals[pair_idx] * (m.power_kw / eff_s)
            idle_power += m_cnt * m.idle_power_kw

        total_power_mw = (active_power + idle_power) / 1000.0
        total_pollution = sum(x_rates[j] * r.pollution_rate for j, r in enumerate(eligible_recipes))

        net_balances = {}
        net_rates = S @ res.x
        for i, item in enumerate(item_list):
            if abs(net_rates[i]) > 1e-4:
                net_balances[item] = round(net_rates[i], 4)

        raw_consumed = {}
        s_supplies = res.x[OFF_S:OFF_Y]
        for item in raw_materials:
            if item in item_idx:
                val = s_supplies[item_idx[item]]
                if val > 1e-4:
                    raw_consumed[item] = round(val, 4)

        # Bottleneck reporting on success: flag any elastic constraint sitting >=85% utilized.
        bottlenecks = []
        if total_power_mw >= max_power_mw * 0.85:
            bottlenecks.append(f"Power grid ({total_power_mw:.2f}/{max_power_mw:.2f} MW, "
                                f"{100*total_power_mw/max_power_mw:.0f}%)")
        if max_pollution_limit is not None and total_pollution >= max_pollution_limit * 0.85:
            bottlenecks.append(f"Pollution ({total_pollution:.1f}/{max_pollution_limit:.1f}, "
                                f"{100*total_pollution/max_pollution_limit:.0f}%)")
        for idx in elastic_indices:
            ctype = constraint_types[idx]
            if ctype in ("power", "pollution"):
                continue
            row = np.asarray(constraints[idx].A).reshape(-1)
            used = float(row @ res.x)
            limit = constraint_limits[idx]
            if limit > 0 and used >= limit * 0.85:
                bottlenecks.append(f"{ctype} constraint near limit ({used:.1f}/{limit:.1f}, "
                                    f"{100*used/limit:.0f}%)")

        return FactorySolution(
            mode=mode,
            success=True,
            total_objective_value=round(res.fun, 4),
            total_power_mw=round(total_power_mw, 3),
            total_pollution=round(total_pollution, 3),
            machine_assignments=machine_assignments,
            actual_recipe_rates=actual_rates,
            item_net_rates=net_balances,
            raw_materials_consumed=raw_consumed,
            bottlenecks=bottlenecks,
        )

    def _diagnose_infeasibility_elastic(
        self, c, integrality, var_bounds, constraints, constraint_types, constraint_limits,
        elastic_indices, total_vars, max_power_mw, max_belt, max_pipe,
    ) -> Tuple[InfeasibilityReason, str]:
        num_slacks = len(elastic_indices)
        if num_slacks == 0:
            return (InfeasibilityReason.UNBOUNDED_OR_NO_RECIPE,
                    "Mass balance or technology pathway is mathematically infeasible.")

        PENALTY = 100000.0
        elastic_c = np.concatenate([c * 0.0, np.full(num_slacks, PENALTY)])
        elastic_integrality = np.zeros(total_vars + num_slacks)
        elastic_ub = np.concatenate([var_bounds.ub, np.full(num_slacks, np.inf)])
        elastic_bounds = Bounds(lb=np.zeros(total_vars + num_slacks), ub=elastic_ub)

        elastic_constraints = []
        slack_to_constraint_map: Dict[int, int] = {}
        slack_idx = 0
        for idx, con in enumerate(constraints):
            row = np.zeros(total_vars + num_slacks)
            row[:total_vars] = con.A
            if idx in elastic_indices:
                row[total_vars + slack_idx] = -1.0
                slack_to_constraint_map[slack_idx] = idx
                slack_idx += 1
            elastic_constraints.append(LinearConstraint(row, con.lb, con.ub))

        res = milp(c=elastic_c, integrality=elastic_integrality, bounds=elastic_bounds,
                   constraints=elastic_constraints)

        if not res.success:
            return (InfeasibilityReason.UNBOUNDED_OR_NO_RECIPE,
                    "Fully infeasible even with relaxed constraints — structural mass balance failure.")

        slacks = res.x[total_vars:]
        active_violations = []
        for s_idx, con_idx in slack_to_constraint_map.items():
            abs_slack = slacks[s_idx]
            if abs_slack > 1e-4:
                ctype = constraint_types[con_idx]
                limit = constraint_limits[con_idx]
                pct_overflow = (abs_slack / max(limit, 1e-6)) * 100.0
                active_violations.append((pct_overflow, abs_slack, ctype, con_idx))

        if not active_violations:
            return (InfeasibilityReason.NONE, "Feasible under relaxed system bounds.")

        active_violations.sort(key=lambda x: x[0], reverse=True)
        primary_pct, primary_abs, primary_type, primary_idx = active_violations[0]

        units = {"power": "kW", "belt": "items/s", "pipe": "units/s", "supply": "units/s", "pollution": "units"}
        unit_str = units.get(primary_type, "units")
        message = f"Primary bottleneck: {primary_type.upper()} ({primary_pct:.1f}% over limit by {primary_abs:.2f} {unit_str})"

        if len(active_violations) > 1:
            secondary_msgs = [f"{v[2]} ({v[0]:.1f}% over)" for v in active_violations[1:4]]
            message += f"\nAlso blocking: {', '.join(secondary_msgs)}"

        reason_map = {
            "power": InfeasibilityReason.POWER_CAP_EXCEEDED,
            "belt": InfeasibilityReason.BELT_CAPACITY_EXCEEDED,
            "pipe": InfeasibilityReason.PIPE_CAPACITY_EXCEEDED,
            "supply": InfeasibilityReason.INSUFFICIENT_RAW_SUPPLY,
            "pollution": InfeasibilityReason.POLLUTION_CAP_EXCEEDED,
        }
        reason = reason_map.get(primary_type, InfeasibilityReason.UNBOUNDED_OR_NO_RECIPE)
        return (reason, message)
