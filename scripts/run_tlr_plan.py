"""Reproduce directed layer plans through real HTTP; export success and failure evidence."""

import argparse
import json
from pathlib import Path

import httpx


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument(
        "--pair", action="append", help="source_layer:target_layer; omit for adjacent defaults"
    )
    parser.add_argument("--options", type=Path)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--output", type=Path, default=Path("outputs/tlr-plans"))
    args = parser.parse_args()
    payload = json.loads(args.dataset.read_text(encoding="utf-8"))
    scope = {k: payload[k] for k in ("tenant_id", "project_id")}
    with httpx.Client(
        base_url=args.base_url + "/api/v1/tlr", trust_env=False, timeout=1900
    ) as client:

        def call(method, path, **kwargs):
            response = client.request(method, path, **kwargs)
            response.raise_for_status()
            return response.json()["data"]

        dataset = call("POST", "/datasets", json=payload)
        request = {**scope, "dataset_id": dataset["id"]}
        if args.pair:
            request["pairs"] = [
                dict(zip(("source", "target"), pair.split(":"), strict=True)) for pair in args.pair
            ]
        if args.options:
            request["options"] = json.loads(args.options.read_text(encoding="utf-8"))
        plan = call("POST", "/plans", json=request)
        directory = args.output / plan["plan_id"]
        directory.mkdir(parents=True)

        def save(name, value):
            (directory / name).write_text(
                json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8"
            )

        save("dataset.json", dataset)
        save("plan.json", plan)
        save("capabilities.json", call("GET", "/capabilities"))
        save("layers.json", call("GET", f"/datasets/{dataset['id']}/layers", params=scope))
        failed = []
        for run in plan["runs"]:
            identifier = run["id"]
            if args.execute:
                response = client.post(f"/runs/{identifier}/execute", params=scope)
                if response.is_error:
                    failed.append(identifier)
                    save(identifier + "-failure.json", response.json())
            final = call("GET", f"/runs/{identifier}", params=scope)
            save(identifier + "-run.json", final)
            for kind in ("elements", "candidates", "links", "evaluations"):
                rows = []
                while True:
                    page = call(
                        "GET",
                        f"/runs/{identifier}/outputs/{kind}",
                        params={**scope, "offset": len(rows), "limit": 500},
                    )
                    rows.extend(page["items"])
                    if len(rows) >= page["total"]:
                        break
                    if not page["items"]:
                        raise RuntimeError("Incomplete output pagination")
                save(identifier + "-" + kind + ".json", rows)
            print(
                final["config"].get("layer_pair"), final["status"], final["counts"], final["error"]
            )
        save(
            "summary.json",
            {
                "plan_id": plan["plan_id"],
                "run_count": len(plan["runs"]),
                "failed_ids": failed,
                "executed": args.execute,
                "base_url": args.base_url,
            },
        )
        print("Saved", directory)
        if failed:
            raise SystemExit(1)


if __name__ == "__main__":
    main()
