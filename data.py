"""
Default recipe & machine database for Chainsmith.

Values are inspired by vanilla Factorio (approximate, for demo/planning
purposes) rather than pulled from a live game export. For byte-exact
numbers from your own save/mod set, use engine.load_factorio_data()
with a real recipe export instead of this module.
"""

from engine import MachineTier, Task

# ---- Machines ---------------------------------------------------------

ASM1 = MachineTier(name="Assembler-1", crafting_speed=0.5, power_kw=75, idle_power_kw=4, build_cost=1.0)
ASM2 = MachineTier(name="Assembler-2", crafting_speed=0.75, power_kw=150, idle_power_kw=6, build_cost=2.0)
ASM3 = MachineTier(name="Assembler-3", crafting_speed=1.25, power_kw=210, idle_power_kw=8, build_cost=4.0)

FURNACE_STONE = MachineTier(name="Stone-Furnace", crafting_speed=1.0, power_kw=90, idle_power_kw=0, build_cost=1.0)
FURNACE_STEEL = MachineTier(name="Steel-Furnace", crafting_speed=2.0, power_kw=90, idle_power_kw=0, build_cost=2.0)
FURNACE_ELECTRIC = MachineTier(name="Electric-Furnace", crafting_speed=2.0, power_kw=180, idle_power_kw=8, build_cost=3.5)

CHEM_PLANT = MachineTier(name="Chemical-Plant", crafting_speed=1.0, power_kw=210, idle_power_kw=8, build_cost=2.5)
OIL_REFINERY = MachineTier(name="Oil-Refinery", crafting_speed=1.0, power_kw=420, idle_power_kw=12, build_cost=4.0)

ALL_MACHINES = [ASM1, ASM2, ASM3, FURNACE_STONE, FURNACE_STEEL, FURNACE_ELECTRIC, CHEM_PLANT, OIL_REFINERY]

# ---- Raw materials (externally supplied) -------------------------------

RAW_MATERIALS = {
    "iron-ore", "copper-ore", "coal", "stone", "water", "crude-oil",
}

# Items that are actually piped rather than belted. Used to fill in
# Task.fluid_items below for any recipe that mixes solid and fluid items
# (e.g. sulfur: fluid inputs, solid output) -- the recipe-wide
# is_fluid_recipe flag can't express that distinction on its own, and
# without it the solid side of a mixed recipe would silently inherit the
# far more generous pipe capacity instead of the belt capacity.
FLUID_ITEMS = {
    "crude-oil", "water", "petroleum-gas", "heavy-oil", "light-oil",
    "sulfuric-acid", "lubricant",
}

# ---- Recipes ------------------------------------------------------------

def default_recipes():
    recipes = [
        Task(name="iron-plate", input_items={"iron-ore": 1.0}, output_items={"iron-plate": 1.0},
             allowed_machines=[FURNACE_STONE, FURNACE_STEEL, FURNACE_ELECTRIC], craft_time=3.2,
             pollution_rate=1.0, cost_weight=1.0, category="smelting"),

        Task(name="copper-plate", input_items={"copper-ore": 1.0}, output_items={"copper-plate": 1.0},
             allowed_machines=[FURNACE_STONE, FURNACE_STEEL, FURNACE_ELECTRIC], craft_time=3.2,
             pollution_rate=1.0, cost_weight=1.0, category="smelting"),

        Task(name="steel-plate", input_items={"iron-plate": 5.0}, output_items={"steel-plate": 1.0},
             allowed_machines=[FURNACE_STONE, FURNACE_STEEL, FURNACE_ELECTRIC], craft_time=16.0,
             pollution_rate=1.0, cost_weight=1.2, category="smelting"),

        Task(name="stone-brick", input_items={"stone": 2.0}, output_items={"stone-brick": 1.0},
             allowed_machines=[FURNACE_STONE, FURNACE_STEEL, FURNACE_ELECTRIC], craft_time=3.2,
             pollution_rate=1.0, cost_weight=0.5, category="smelting"),

        Task(name="iron-gear-wheel", input_items={"iron-plate": 2.0}, output_items={"iron-gear-wheel": 1.0},
             allowed_machines=[ASM1, ASM2, ASM3], craft_time=0.5,
             pollution_rate=1.0, cost_weight=1.0, category="intermediate"),

        Task(name="copper-cable", input_items={"copper-plate": 1.0}, output_items={"copper-cable": 2.0},
             allowed_machines=[ASM1, ASM2, ASM3], craft_time=0.5,
             pollution_rate=1.0, cost_weight=0.5, category="intermediate"),

        Task(name="iron-stick", input_items={"iron-plate": 1.0}, output_items={"iron-stick": 2.0},
             allowed_machines=[ASM1, ASM2, ASM3], craft_time=0.5,
             pollution_rate=1.0, cost_weight=0.5, category="intermediate"),

        Task(name="pipe", input_items={"iron-plate": 1.0}, output_items={"pipe": 1.0},
             allowed_machines=[ASM1, ASM2, ASM3], craft_time=0.5,
             pollution_rate=1.0, cost_weight=0.5, category="intermediate"),

        Task(name="electronic-circuit", input_items={"iron-plate": 1.0, "copper-cable": 3.0},
             output_items={"electronic-circuit": 1.0}, allowed_machines=[ASM1, ASM2, ASM3], craft_time=0.5,
             pollution_rate=1.0, cost_weight=1.5, category="circuit"),

        Task(name="basic-oil-processing", input_items={"crude-oil": 100.0},
             output_items={"petroleum-gas": 45.0}, allowed_machines=[OIL_REFINERY], craft_time=5.0,
             pollution_rate=4.0, cost_weight=1.0, is_fluid_recipe=True, category="chemical"),

        Task(name="advanced-oil-processing", input_items={"crude-oil": 100.0, "water": 50.0},
             output_items={"petroleum-gas": 55.0}, byproducts={"heavy-oil": 25.0, "light-oil": 45.0},
             allowed_machines=[OIL_REFINERY], craft_time=5.0, pollution_rate=6.0, cost_weight=1.2,
             is_fluid_recipe=True, category="chemical"),

        Task(name="plastic-bar", input_items={"petroleum-gas": 20.0, "coal": 1.0},
             output_items={"plastic-bar": 2.0}, allowed_machines=[CHEM_PLANT], craft_time=1.0,
             pollution_rate=3.0, cost_weight=1.5, is_fluid_recipe=True, category="chemical"),

        Task(name="sulfur", input_items={"water": 30.0, "petroleum-gas": 30.0}, output_items={"sulfur": 2.0},
             allowed_machines=[CHEM_PLANT], craft_time=1.0, pollution_rate=2.0, cost_weight=1.0,
             is_fluid_recipe=True, category="chemical"),

        Task(name="sulfuric-acid", input_items={"iron-plate": 1.0, "sulfur": 5.0, "water": 100.0},
             output_items={"sulfuric-acid": 50.0}, allowed_machines=[CHEM_PLANT], craft_time=1.0,
             pollution_rate=3.0, cost_weight=1.0, is_fluid_recipe=True, category="chemical"),

        Task(name="battery", input_items={"iron-plate": 1.0, "copper-plate": 1.0, "sulfuric-acid": 20.0},
             output_items={"battery": 1.0}, allowed_machines=[CHEM_PLANT], craft_time=4.0,
             pollution_rate=3.0, cost_weight=2.0, is_fluid_recipe=True, category="chemical"),

        Task(name="advanced-circuit", input_items={"electronic-circuit": 2.0, "plastic-bar": 2.0, "copper-cable": 4.0},
             output_items={"advanced-circuit": 1.0}, allowed_machines=[ASM1, ASM2, ASM3], craft_time=6.0,
             pollution_rate=1.5, cost_weight=3.0, category="circuit"),

        Task(name="processing-unit",
             input_items={"electronic-circuit": 20.0, "advanced-circuit": 2.0, "sulfuric-acid": 5.0},
             output_items={"processing-unit": 1.0}, allowed_machines=[ASM1, ASM2, ASM3], craft_time=10.0,
             pollution_rate=2.0, cost_weight=6.0, is_fluid_recipe=True, category="circuit"),

        Task(name="engine-unit", input_items={"steel-plate": 1.0, "iron-gear-wheel": 1.0, "pipe": 2.0},
             output_items={"engine-unit": 1.0}, allowed_machines=[ASM1, ASM2, ASM3], craft_time=10.0,
             pollution_rate=2.0, cost_weight=3.0, category="intermediate"),

        Task(name="lubricant", input_items={"heavy-oil": 10.0}, output_items={"lubricant": 10.0},
             allowed_machines=[CHEM_PLANT], craft_time=1.0, pollution_rate=1.0, cost_weight=0.5,
             is_fluid_recipe=True, category="chemical"),

        Task(name="electric-engine-unit",
             input_items={"engine-unit": 1.0, "electronic-circuit": 2.0, "lubricant": 15.0},
             output_items={"electric-engine-unit": 1.0}, allowed_machines=[ASM1, ASM2, ASM3], craft_time=10.0,
             pollution_rate=2.0, cost_weight=4.0, is_fluid_recipe=True, category="intermediate"),

        Task(name="inserter", input_items={"iron-plate": 1.0, "iron-gear-wheel": 1.0, "electronic-circuit": 1.0},
             output_items={"inserter": 1.0}, allowed_machines=[ASM1, ASM2, ASM3], craft_time=0.5,
             pollution_rate=1.0, cost_weight=2.0, category="logistics"),

        Task(name="automation-science-pack", input_items={"copper-plate": 1.0, "iron-gear-wheel": 1.0},
             output_items={"automation-science-pack": 1.0}, allowed_machines=[ASM1, ASM2, ASM3], craft_time=5.0,
             pollution_rate=1.0, cost_weight=2.0, category="science"),

        Task(name="logistic-science-pack", input_items={"inserter": 1.0, "iron-gear-wheel": 1.0},
             output_items={"logistic-science-pack": 1.0}, allowed_machines=[ASM1, ASM2, ASM3], craft_time=6.0,
             pollution_rate=1.0, cost_weight=2.5, category="science"),

        Task(name="chemical-science-pack",
             input_items={"engine-unit": 2.0, "advanced-circuit": 3.0, "sulfur": 1.0},
             output_items={"chemical-science-pack": 2.0}, allowed_machines=[ASM1, ASM2, ASM3], craft_time=24.0,
             pollution_rate=2.0, cost_weight=5.0, category="science"),
    ]

    # For every recipe that touches a fluid at all, work out exactly *which*
    # of its own items are the fluid ones, so mixed recipes (solid output
    # from fluid inputs, or vice versa) get correct per-item belt/pipe
    # capacity instead of one recipe-wide guess.
    for t in recipes:
        if t.is_fluid_recipe:
            item_names = set(t.input_items) | set(t.output_items) | set(t.byproducts)
            t.fluid_items = FLUID_ITEMS & item_names

    return recipes


def build_engine():
    from engine import ChainsmithEngine
    eng = ChainsmithEngine()
    eng.load_recipes(default_recipes())
    return eng
