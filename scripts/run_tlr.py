"""Import, execute, optionally evaluate, and export a TLR run through the backend API."""

import argparse
import json
from pathlib import Path

import httpx


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument(
        "--trust-env", action="store_true", help="Explicitly use environment HTTP proxies"
    )
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--gold", type=Path)
    parser.add_argument("--output", type=Path, default=Path("outputs/tlr"))
    parser.add_argument("--timeout", type=float, default=1900)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    dataset_input, run_input = read_json(args.dataset), read_json(args.run)
    scope = {key: dataset_input[key] for key in ("tenant_id", "project_id")}
    if any(run_input[key] != scope[key] for key in scope):
        raise ValueError("Dataset and run scopes differ")
    with httpx.Client(
        base_url=args.base_url.rstrip("/"), timeout=args.timeout, trust_env=args.trust_env
    ) as client:

        def call(method, path, **kwargs):
            response = client.request(method, "/api/v1/tlr" + path, **kwargs)
            response.raise_for_status()
            return response.json()["data"]

        dataset = call("POST", "/datasets", json=dataset_input)
        run_input["dataset_id"] = dataset["id"]
        run = call("POST", "/runs", json=run_input)
        run_id = run["id"]
        output = args.output / run_id
        output.mkdir()

        def save(name, value):
            (output / name).write_text(
                json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8"
            )

        save("run.json", run)  # Run ID remains recoverable if execution times out.
        save("dataset.json", dataset)
        error = None
        try:
            call("POST", f"/runs/{run_id}/execute", params=scope)
            if args.gold:
                call("POST", f"/runs/{run_id}/evaluations", params=scope, json=read_json(args.gold))
        except httpx.HTTPError as exc:
            error = exc
            if isinstance(exc, httpx.HTTPStatusError):
                try:
                    save("failure.json", exc.response.json())
                except ValueError:
                    save("failure.json", {"status": exc.response.status_code})
        finally:
            save("run.json", call("GET", f"/runs/{run_id}", params=scope))
            for kind in ("elements", "candidates", "links", "evaluations"):
                rows = []
                while True:
                    page = call(
                        "GET",
                        f"/runs/{run_id}/outputs/{kind}",
                        params={**scope, "offset": len(rows), "limit": 500},
                    )
                    rows.extend(page["items"])
                    if len(rows) >= page["total"]:
                        break
                    if not page["items"]:
                        raise RuntimeError("Unexpected empty page")
                save(f"{kind}.json", rows)
        print(f"Run {run_id}: {output}")
        if error:
            saved = read_json(output / "run.json")
            print("Stage:", saved["stage"], "Reason:", saved["error"])
            raise SystemExit(1)


if __name__ == "__main__":
    main()
