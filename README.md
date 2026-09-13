# Chainsmith

**Author:** Sherwin Samuel Paponette — Independent Researcher, Trinidad and Tobago
**License:** MIT

A MILP-optimizing factory production planner for Factorio-style production chains.
Given a target item and rate, it finds the optimal machine layout subject to real
constraints: machine selection, grid power cap, belt/pipe throughput, raw supply,
pollution ceilings, and tech tiers.

**What makes it different:** True MILP (not heuristics). Machine-choice-honest power
costs. Elastic infeasibility diagnostics that tell you *which* constraint is blocking
and by how much, rather than just "infeasible".

**Interfaces:** CLI + Flask HTTP API with input validation, security headers, and
body-size limits.

## Quick start

    pip install -r requirements.txt
    python app.py

Then open http://127.0.0.1:5000

## API

- GET /api/health — server readiness
- GET /api/recipes — recipe database
- POST /api/solve — solve a production plan

## Testing

    python3 test_api.py

## Docker

    docker build -t chainsmith .
    docker run -p 5000:5000 chainsmith

## License

MIT — see LICENSE.
