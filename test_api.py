"""Chainsmith API smoke tests."""
import json, sys, urllib.request, urllib.error

BASE = "http://127.0.0.1:5000"

def call(method, path, body=None):
    url = BASE + path
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    if body is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode())
    except urllib.error.URLError as e:
        return None, {"error": str(e)}

def test(name, method, path, body, expect):
    status, resp = call(method, path, body)
    ok = status == expect
    print(f"[{'PASS' if ok else 'FAIL'}] {name} status={status}")
    return ok

def main():
    results = []
    results.append(test("health", "GET", "/api/health", None, 200))
    results.append(test("recipes", "GET", "/api/recipes", None, 200))
    valid = {"mode":"min_cost","target_item":"iron-gear-wheel",
             "target_rate":10.0,"max_power_mw":100}
    results.append(test("solve valid", "POST", "/api/solve", valid, 200))
    results.append(test("missing target", "POST", "/api/solve",
                        {"mode":"min_cost"}, 400))
    results.append(test("bad mode", "POST", "/api/solve",
                        {"mode":"bogus","target_item":"iron-plate"}, 400))
    results.append(test("negative rate", "POST", "/api/solve",
                        {"mode":"min_cost","target_item":"iron-plate",
                         "target_rate":-5}, 400))
    print()
    passed = sum(results); total = len(results)
    print(f"{passed}/{total} passed")
    return 0 if passed == total else 1

if __name__ == "__main__":
    sys.exit(main())
