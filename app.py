#!/usr/bin/env python3
"""
Chainsmith — a MILP-optimizing factory production planner.

Local development:
    pip install flask numpy scipy
    python app.py
Then open http://127.0.0.1:5000

Production:
    Don't use the built-in Flask dev server (it's single-threaded-ish and its
    debugger is a remote-code-execution risk if ever left on). Use a real
    WSGI server instead, e.g.:
        pip install waitress
        waitress-serve --host=0.0.0.0 --port=5000 app:app
    or gunicorn on a machine that has it available:
        gunicorn -w 4 -b 0.0.0.0:5000 app:app

Environment variables:
    CHAINSMITH_HOST          default 127.0.0.1
    CHAINSMITH_PORT          default 5000
    CHAINSMITH_DEBUG         "1" to enable Flask debug mode (dev only — never in prod)
    CHAINSMITH_MAX_BODY_KB   max request body size in KB, default 256
"""

from __future__ import annotations

import logging
import os
import threading
import traceback
from typing import Any

from flask import Flask, jsonify, request, send_from_directory
from werkzeug.exceptions import HTTPException

from engine import ChainsmithEngine, Mode
from data import build_engine, RAW_MATERIALS

# --------------------------------------------------------------------------
# Config
# --------------------------------------------------------------------------
DEBUG = os.environ.get("CHAINSMITH_DEBUG") == "1"
HOST = os.environ.get("CHAINSMITH_HOST", "127.0.0.1")
PORT = int(os.environ.get("CHAINSMITH_PORT", "5000"))
MAX_BODY_KB = int(os.environ.get("CHAINSMITH_MAX_BODY_KB", "256"))

logging.basicConfig(
    level=logging.DEBUG if DEBUG else logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
log = logging.getLogger("chainsmith")

app = Flask(__name__, static_folder="static", static_url_path="")
app.config["MAX_CONTENT_LENGTH"] = MAX_BODY_KB * 1024
app.config["JSON_SORT_KEYS"] = False

# --------------------------------------------------------------------------
# Engine startup — fail loud and clean instead of a bare traceback mid-import
# --------------------------------------------------------------------------
try:
    ENGINE: ChainsmithEngine = build_engine()
    ALL_KNOWN_ITEMS: set[str] = set(RAW_MATERIALS)
    for r in ENGINE.recipes.values():
        ALL_KNOWN_ITEMS.update(r.input_items.keys())
        ALL_KNOWN_ITEMS.update(r.output_items.keys())
        ALL_KNOWN_ITEMS.update(r.byproducts.keys())
    log.info(
        "Engine ready: %d recipes, %d known items",
        len(ENGINE.recipes), len(ALL_KNOWN_ITEMS),
    )
except Exception:
    log.critical("Failed to build engine at startup:\n%s", traceback.format_exc())
    raise SystemExit(1)

# scipy's MILP/linprog paths and any engine-internal caches are not guaranteed
# reentrant. Serializing solves keeps concurrent requests from corrupting
# shared state; drop this if ChainsmithEngine.solve() is confirmed thread-safe.
_SOLVE_LOCK = threading.Lock()


# --------------------------------------------------------------------------
# Security headers on every response
# --------------------------------------------------------------------------
@app.after_request
def add_security_headers(resp):
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["X-Frame-Options"] = "DENY"
    resp.headers["Referrer-Policy"] = "no-referrer"
    return resp


# --------------------------------------------------------------------------
# Error handlers — never leak a raw traceback to the client
# --------------------------------------------------------------------------
@app.errorhandler(413)
def too_large(_e):
    return jsonify({"success": False, "error": f"Request body exceeds {MAX_BODY_KB}KB limit."}), 413


@app.errorhandler(404)
def not_found(_e):
    return jsonify({"success": False, "error": "Not found."}), 404


@app.errorhandler(HTTPException)
def http_error(e: HTTPException):
    return jsonify({"success": False, "error": e.description}), e.code


@app.errorhandler(Exception)
def unhandled_error(e: Exception):
    log.error("Unhandled exception on %s %s:\n%s", request.method, request.path, traceback.format_exc())
    payload: dict[str, Any] = {"success": False, "error": "Internal server error."}
    if DEBUG:
        payload["debug_detail"] = str(e)
    return jsonify(payload), 500


# --------------------------------------------------------------------------
# Routes
# --------------------------------------------------------------------------
@app.route("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


@app.route("/api/health")
def api_health():
    return jsonify({
        "status": "ok",
        "recipes_loaded": len(ENGINE.recipes),
        "items_known": len(ALL_KNOWN_ITEMS),
    })


@app.route("/api/recipes")
def api_recipes():
    out = []
    for r in ENGINE.recipes.values():
        out.append({
            "name": r.name,
            "inputs": r.input_items,
            "outputs": r.output_items,
            "byproducts": r.byproducts,
            "machines": [m.name for m in r.allowed_machines],
            "category": r.category,
            "fluid": r.is_fluid_recipe,
        })
    return jsonify({
        "recipes": sorted(out, key=lambda x: x["name"]),
        "raw_materials": sorted(RAW_MATERIALS),
        "all_items": sorted(ALL_KNOWN_ITEMS),
    })


def _parse_positive_float(payload: dict, key: str, default: float, *, allow_zero: bool = False) -> float:
    raw = payload.get(key, default)
    if raw in (None, ""):
        raw = default
    value = float(raw)
    floor = 0.0 if allow_zero else 0.0
    if value < floor or (not allow_zero and value <= 0):
        raise ValueError(f"'{key}' must be a positive number, got {raw!r}")
    return value


def _parse_optional_nonneg_float(payload: dict, key: str) -> float | None:
    raw = payload.get(key)
    if raw in (None, ""):
        return None
    value = float(raw)
    if value < 0:
        raise ValueError(f"'{key}' must be >= 0, got {raw!r}")
    return value


def _validate_solve_payload(payload: dict) -> dict:
    """Parse and validate the /api/solve request body. Raises ValueError
    with a human-readable message on any bad input."""
    mode_raw = payload.get("mode", "min_cost")
    if mode_raw not in ("max_yield", "min_cost"):
        raise ValueError(f"'mode' must be 'max_yield' or 'min_cost', got {mode_raw!r}")
    mode = Mode.MAXIMIZE_YIELD if mode_raw == "max_yield" else Mode.MINIMIZE_COST

    target_item = payload.get("target_item")
    if not isinstance(target_item, str) or not target_item.strip():
        raise ValueError("'target_item' is required and must be a non-empty string")
    if ALL_KNOWN_ITEMS and target_item not in ALL_KNOWN_ITEMS:
        raise ValueError(f"'target_item' {target_item!r} is not a known item")

    target_rate = None
    raw_rate = payload.get("target_rate", 1.0)
    if raw_rate not in (None, ""):
        target_rate = float(raw_rate)
        if target_rate <= 0:
            raise ValueError(f"'target_rate' must be positive, got {raw_rate!r}")

    raw_materials_in = payload.get("raw_materials")
    if raw_materials_in is None:
        raw_materials = set(RAW_MATERIALS)
    else:
        if not isinstance(raw_materials_in, list) or not all(isinstance(x, str) for x in raw_materials_in):
            raise ValueError("'raw_materials' must be a list of item name strings")
        raw_materials = set(raw_materials_in)

    supply_caps_in = payload.get("supply_caps") or {}
    if not isinstance(supply_caps_in, dict):
        raise ValueError("'supply_caps' must be an object of {item: rate}")
    supply_caps = {}
    for k, v in supply_caps_in.items():
        if v in (None, ""):
            continue
        fv = float(v)
        if fv < 0:
            raise ValueError(f"supply cap for {k!r} must be >= 0, got {v!r}")
        supply_caps[k] = fv

    max_power_mw = _parse_positive_float(payload, "max_power_mw", 1000.0)
    max_belt = _parse_positive_float(payload, "max_belt", 45.0)
    max_pipe = _parse_positive_float(payload, "max_pipe", 1200.0)
    max_pollution = _parse_optional_nonneg_float(payload, "max_pollution")

    zero_waste = bool(payload.get("zero_waste_loop", False))

    alpha_power = payload.get("alpha_power_weight", 0.1)
    alpha_power = 0.1 if alpha_power in (None, "") else float(alpha_power)
    if alpha_power < 0:
        raise ValueError("'alpha_power_weight' must be >= 0")

    alpha_pollution = payload.get("alpha_pollution_weight", 0.1)
    alpha_pollution = 0.1 if alpha_pollution in (None, "") else float(alpha_pollution)
    if alpha_pollution < 0:
        raise ValueError("'alpha_pollution_weight' must be >= 0")

    player_tier_raw = payload.get("player_tier", 99)
    player_tier = int(player_tier_raw)
    if player_tier < 1:
        raise ValueError(f"'player_tier' must be >= 1, got {player_tier_raw!r}")

    return dict(
        mode=mode,
        target_item=target_item,
        target_rate=target_rate,
        raw_materials=raw_materials,
        supply_caps=supply_caps,
        max_power_mw=max_power_mw,
        max_belt=max_belt,
        max_pipe=max_pipe,
        max_pollution=max_pollution,
        zero_waste=zero_waste,
        alpha_power=alpha_power,
        alpha_pollution=alpha_pollution,
        player_tier=player_tier,
    )


@app.route("/api/solve", methods=["POST"])
def api_solve():
    payload = request.get_json(silent=True)
    if payload is None:
        return jsonify({"success": False, "error": "Request body must be valid JSON."}), 400
    if not isinstance(payload, dict):
        return jsonify({"success": False, "error": "Request body must be a JSON object."}), 400

    try:
        parsed = _validate_solve_payload(payload)
    except (ValueError, TypeError) as e:
        return jsonify({"success": False, "error": f"Invalid input: {e}"}), 400

    try:
        with _SOLVE_LOCK:
            result = ENGINE.solve(
                mode=parsed["mode"],
                target_item=parsed["target_item"],
                raw_materials=parsed["raw_materials"],
                target_rate=parsed["target_rate"],
                supply_caps=parsed["supply_caps"],
                max_power_mw=parsed["max_power_mw"],
                max_single_belt_capacity=parsed["max_belt"],
                max_single_pipe_capacity=parsed["max_pipe"],
                max_pollution_limit=parsed["max_pollution"],
                zero_waste_loop=parsed["zero_waste"],
                alpha_power_weight=parsed["alpha_power"],
                alpha_pollution_weight=parsed["alpha_pollution"],
                player_tier=parsed["player_tier"],
            )
    except Exception:
        log.error("Solver raised for target_item=%r:\n%s", parsed["target_item"], traceback.format_exc())
        return jsonify({"success": False, "error": "Solver failed unexpectedly."}), 500

    return jsonify({
        "success": result.success,
        "mode": result.mode.value,
        "total_objective_value": result.total_objective_value,
        "total_power_mw": result.total_power_mw,
        "total_pollution": result.total_pollution,
        "machine_assignments": {k: {"machine": v[0], "count": v[1]} for k, v in result.machine_assignments.items()},
        "actual_recipe_rates": result.actual_recipe_rates,
        "item_net_rates": result.item_net_rates,
        "raw_materials_consumed": result.raw_materials_consumed,
        "bottlenecks": result.bottlenecks,
        "infeasibility_reason": result.infeasibility_reason.value if result.infeasibility_reason else None,
        "diagnostic_message": result.diagnostic_message,
    })


if __name__ == "__main__":
    if DEBUG:
        log.warning("Running with CHAINSMITH_DEBUG=1 — never do this in production (Werkzeug debugger = RCE risk).")
    app.run(host=HOST, port=PORT, debug=DEBUG)
