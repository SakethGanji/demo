"""End-to-end smoke test for $vars / environment-scoped variables.

Exercises the backend exactly the way the studio UI does:
  1. POST /variables — create a secret in env 'e2e-test'
  2. GET /variables/environments — confirm 'e2e-test' shows up
  3. GET /variables?environment=e2e-test — confirm secret value is masked (null)
  4. POST /execution-stream/adhoc — run a Start → Set workflow that resolves
     {{ $vars.E2E_TOKEN }}, with environment='e2e-test' in the body
  5. Read the SSE stream and assert the Set node's output contains the real value
  6. DELETE the variable to clean up

Run while the workflow-engine is up at http://localhost:8000:
    ./venv/bin/python scripts/test_vars_e2e.py
"""

from __future__ import annotations

import json
import sys

import httpx

BASE_URL = "http://localhost:8000"
ENV = "e2e-test"
KEY = "E2E_TOKEN"
SECRET_VALUE = "xoxb-secret-resolved-correctly"


def main() -> int:
    with httpx.Client(base_url=BASE_URL, timeout=15.0) as client:
        # ------------------------------------------------------------------
        # Clean up any leftover var from a previous failed run
        # ------------------------------------------------------------------
        existing = client.get(
            "/api/variables", params={"environment": ENV, "team_id": "default"}
        ).json()
        for v in existing:
            if v["key"] == KEY:
                client.delete(f"/api/variables/{v['id']}")
                print(f"  cleaned up leftover variable id={v['id']}")

        # ------------------------------------------------------------------
        # 1. Create the secret
        # ------------------------------------------------------------------
        print("\n[1] POST /variables — create secret")
        r = client.post(
            "/api/variables",
            json={
                "key": KEY,
                "value": SECRET_VALUE,
                "type": "secret",
                "environment": ENV,
                "team_id": "default",
            },
        )
        r.raise_for_status()
        created = r.json()
        var_id = created["id"]
        assert created["environment"] == ENV, created
        assert created["value"] is None, f"create response should mask secret value, got {created!r}"
        print(f"  ✓ created id={var_id}, value masked in response")

        # ------------------------------------------------------------------
        # 2. environments lister includes our env
        # ------------------------------------------------------------------
        print("\n[2] GET /variables/environments")
        r = client.get("/api/variables/environments")
        r.raise_for_status()
        envs = r.json()
        assert ENV in envs, f"expected {ENV!r} in {envs!r}"
        print(f"  ✓ envs returned: {envs}")

        # ------------------------------------------------------------------
        # 3. List masks the secret value
        # ------------------------------------------------------------------
        print("\n[3] GET /variables?environment=...")
        r = client.get("/api/variables", params={"environment": ENV})
        r.raise_for_status()
        listed = r.json()
        ours = next((v for v in listed if v["key"] == KEY), None)
        assert ours is not None, f"variable not in listing: {listed!r}"
        assert ours["value"] is None, f"secret value should be masked in list, got {ours!r}"
        assert ours["type"] == "secret"
        print(f"  ✓ list returns key={ours['key']} with value=None (masked)")

        # ------------------------------------------------------------------
        # 4. Run an adhoc workflow that resolves $vars.E2E_TOKEN
        # ------------------------------------------------------------------
        print(f"\n[4] POST /execution-stream/adhoc — workflow uses {{{{ $vars.{KEY} }}}}")
        workflow_body = {
            "name": "vars-e2e",
            "environment": ENV,
            "nodes": [
                {
                    "name": "Start",
                    "type": "Start",
                    "parameters": {},
                    "position": {"x": 0, "y": 0},
                },
                {
                    "name": "Resolve",
                    "type": "Set",
                    "parameters": {
                        "mode": "manual",
                        "fields": [
                            {
                                "name": "resolved",
                                "value": f"{{{{ $vars.{KEY} }}}}",
                                "type": "string",
                            },
                        ],
                    },
                    "position": {"x": 200, "y": 0},
                },
            ],
            "connections": [
                {"source_node": "Start", "target_node": "Resolve"},
            ],
        }

        resolved_value = None
        with client.stream(
            "POST",
            "/execution-stream/adhoc",
            json=workflow_body,
            headers={"Accept": "text/event-stream"},
        ) as stream:
            stream.raise_for_status()
            for line in stream.iter_lines():
                if not line.startswith("data:"):
                    continue
                payload = line[len("data:"):].strip()
                if not payload:
                    continue
                event = json.loads(payload)
                if event.get("type") == "node:complete" and event.get("nodeName") == "Resolve":
                    data = event.get("data") or []
                    if data:
                        resolved_value = data[0].get("json", {}).get("resolved")
                    print(f"  Resolve node output: {data}")

        assert resolved_value == SECRET_VALUE, (
            f"\n  ✗ EXPECTED {SECRET_VALUE!r}\n  ✗ GOT      {resolved_value!r}\n"
            "  → Secret was NOT resolved server-side. Check engine wiring."
        )
        print(f"  ✓ workflow resolved {{{{ $vars.{KEY} }}}} → {SECRET_VALUE!r}")

        # ------------------------------------------------------------------
        # 5. Different env yields nothing
        # ------------------------------------------------------------------
        print("\n[5] Same workflow against environment='default' (no var defined)")
        workflow_body["environment"] = "default"
        resolved_other = None
        with client.stream(
            "POST", "/execution-stream/adhoc", json=workflow_body
        ) as stream:
            stream.raise_for_status()
            for line in stream.iter_lines():
                if not line.startswith("data:"):
                    continue
                payload = line[len("data:"):].strip()
                if not payload:
                    continue
                event = json.loads(payload)
                if event.get("type") == "node:complete" and event.get("nodeName") == "Resolve":
                    data = event.get("data") or []
                    if data:
                        resolved_other = data[0].get("json", {}).get("resolved")

        # When the key isn't in the active env's vars, expression engine returns None
        # which Set stringifies to "" (empty string).
        assert resolved_other in (None, "", "None"), (
            f"  ✗ default env shouldn't have {KEY}; got {resolved_other!r}"
        )
        print(f"  ✓ different env returned {resolved_other!r} (expected empty/None)")

        # ------------------------------------------------------------------
        # 5b. Server-side substitution on the REQUEST BODY itself.
        #     Caller embeds {{ $vars.E2E_TOKEN }} literally in input_data;
        #     server resolves before the workflow runs. Secret never leaves
        #     the backend; the caller doesn't need to know its value.
        # ------------------------------------------------------------------
        print("\n[5b] body substitution: pass {{ $vars.E2E_TOKEN }} in input_data")
        body_sub_workflow = {
            "name": "vars-e2e-body",
            "environment": ENV,
            # Echo whatever came in as $json.body.auth into output.resolved
            "nodes": [
                {"name": "Start", "type": "Start", "parameters": {}, "position": {"x": 0, "y": 0}},
                {
                    "name": "Echo",
                    "type": "Set",
                    "parameters": {
                        "mode": "manual",
                        "fields": [
                            {
                                "name": "resolved",
                                "value": "{{ $json.body.auth }}",
                                "type": "string",
                            },
                        ],
                    },
                    "position": {"x": 200, "y": 0},
                },
            ],
            "connections": [{"source_node": "Start", "target_node": "Echo"}],
            # The caller embeds the placeholder — server substitutes BEFORE
            # the workflow sees $json.body.auth.
            "input_data": {"auth": f"Bearer {{{{ $vars.{KEY} }}}}"},
        }
        body_sub_resolved = None
        with client.stream(
            "POST", "/execution-stream/adhoc", json=body_sub_workflow
        ) as stream:
            stream.raise_for_status()
            for line in stream.iter_lines():
                if not line.startswith("data:"):
                    continue
                payload = line[len("data:"):].strip()
                if not payload:
                    continue
                event = json.loads(payload)
                if event.get("type") == "node:complete" and event.get("nodeName") == "Echo":
                    data = event.get("data") or []
                    if data:
                        body_sub_resolved = data[0].get("json", {}).get("resolved")
        expected_body = f"Bearer {SECRET_VALUE}"
        assert body_sub_resolved == expected_body, (
            f"\n  ✗ EXPECTED {expected_body!r}\n  ✗ GOT      {body_sub_resolved!r}\n"
            "  → Body substitution failed — caller's {{ $vars.X }} in input_data\n"
            "    should be resolved server-side before the workflow sees it."
        )
        print(f"  ✓ caller sent placeholder in body, server resolved → {body_sub_resolved!r}")

        # ------------------------------------------------------------------
        # 6. Cleanup
        # ------------------------------------------------------------------
        print(f"\n[6] DELETE /variables/{var_id}")
        r = client.delete(f"/api/variables/{var_id}")
        r.raise_for_status()
        print(f"  ✓ deleted, status={r.json()}")

    print("\nALL E2E CHECKS PASSED ✅")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except httpx.HTTPError as e:
        print(f"\nHTTP error: {e}", file=sys.stderr)
        sys.exit(2)
    except AssertionError as e:
        print(f"\nAssertion failed: {e}", file=sys.stderr)
        sys.exit(1)
