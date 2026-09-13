# Chainsmith

**Author:** Sherwin Samuel Paponette — Trinidad and Tobago — MIT License

A MILP-optimizing factory production planner for Factorio-style production chains.

## What it does

Given a target item and a target rate, Chainsmith finds the optimal machine
layout subject to real constraints: machine selection, grid power cap, belt/pipe
throughput, raw material supply, pollution ceilings, technology tier unlocks,
and fluid vs solid transport.

When a solution is infeasible, it explains why — reporting which constraint is
blocking and by how much, via elastic relaxation.

## Features

- True MILP using scipy.optimize.milp
- Machine-choice honest — power/cost attached to selected machine, not averaged
- Per-machine power via an XP variable so power matches the machine chosen
- Elastic diagnostics for infeasibility
- Bottleneck warning above 85% utilization
- Production-grade Flask API with validation and security headers
- Data guards preventing NaN/inf in the solver

## Install

    pip install -r requirements.txt

## Run

Development:

    python app.py

Production:

    waitress-serve --host=0.0.0.0 --port=5000 app:app

Then open http://127.0.0.1:5000

## API

GET /api/health — status and loaded counts

GET /api/recipes — recipe database

POST /api/solve — solve a production plan

Example request:

    {mode:min_cost,target_item:processing-unit,target_rate:1.0,
     max_power_mw:100}

## Testing

    python3 test_api.py

## Docker

    docker build -t chainsmith .
    docker run -p 5000:5000 chainsmith

## License

MIT — see LICENSE.
