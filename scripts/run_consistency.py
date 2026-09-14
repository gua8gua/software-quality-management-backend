"""Run consistency on an existing TLR snapshot and export reusable evidence/results."""

import argparse
import asyncio
import json
from pathlib import Path

from app.api.dependencies import get_llm_provider
from app.core.config import get_settings
from app.db.session import AsyncSessionLocal, engine
from app.modules.consistency.schemas import AnalysisInput
from app.modules.consistency.service import ConsistencyService


def markdown(report):
    lines = [
        "# 需求—代码实现分析",
        "",
        f"运行：{report['id']}",
        f"状态：{report['status']}",
        "",
        "结果标签可共存；静态分析不等于已经执行验证。",
        "",
    ]
    for key, result in report["results"].get("requirements", {}).items():
        lines += [
            f"## {key}",
            "",
            "结果：" + "、".join(result.get("outcome_labels", [])),
            "",
            result["packet"]["requirement"]["text"],
            "",
        ]
        for index, record in enumerate(result.get("implementations", []), 1):
            lines += [f"### 代码实现记录 {index}", ""]
            for citation in record["citations"]:
                lines += [
                    f"代码位置：{citation['locator']}:{citation['start_line']}",
                    "",
                    "~~~text",
                    citation["quote"],
                    "~~~",
                    "",
                ]
            if record.get("unassessed_code"):
                lines += ["未分析代码：" + str(record["unassessed_code"]), ""]
            for part in record["parts"]:
                lines += [
                    "需求部分：" + part["requirement_quote"],
                    "实现行为：" + part["implemented_behavior"],
                    "实现情况：" + ", ".join(part["outcomes"]),
                    "解释：" + part["reason"],
                    "",
                ]
                if part.get("grounding_errors"):
                    lines += ["引用/范围问题：" + ", ".join(part["grounding_errors"]), ""]
        lines += ["整体实现：" + str(result.get("whole_requirement_implemented", False)), ""]
    return "\n".join(lines)


async def main(args):
    settings = get_settings()
    try:
        async with AsyncSessionLocal() as session:
            service = ConsistencyService(session, settings, get_llm_provider(settings))
            if args.run_id:
                run = await service.get(args.run_id, args.tenant_id, args.project_id)
            else:
                if not args.tlr_run_id:
                    raise SystemExit("--tlr-run-id or --run-id is required")
                run = await service.create(
                    AnalysisInput(
                        tenant_id=args.tenant_id,
                        project_id=args.project_id,
                        tlr_run_id=args.tlr_run_id,
                        requirements_run_id=args.requirements_run_id,
                        requirement_ids=args.requirement_id,
                        additional_artifact_ids=args.context_id,
                        max_evidence_chars=args.max_evidence_chars,
                        max_expansion_rounds=args.max_expansion_rounds,
                    )
                )
            print(f"Prepared consistency run: {run.id}", flush=True)
            if not args.prepare_only and run.status != "completed":
                run = await service.execute(run.id, args.tenant_id, args.project_id)
            report = await service.view(run)
            output = Path(args.output) / run.id
            output.mkdir(parents=True, exist_ok=True)
            (output / "report.json").write_text(
                json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            (output / "report.md").write_text(markdown(report), encoding="utf-8")
            print(
                json.dumps(
                    {
                        "status": report["status"],
                        "metrics": report["metrics"],
                        "output": str(output),
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
            if run.status == "failed":
                raise SystemExit(1)
    finally:
        await engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tenant-id", required=True)
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--tlr-run-id")
    parser.add_argument("--requirements-run-id")
    parser.add_argument("--run-id")
    parser.add_argument("--requirement-id", action="append", default=[])
    parser.add_argument("--context-id", action="append", default=[])
    parser.add_argument("--prepare-only", action="store_true")
    parser.add_argument("--max-evidence-chars", type=int, default=60000)
    parser.add_argument("--max-expansion-rounds", type=int, default=1)
    parser.add_argument("--output", default="outputs/consistency")
    asyncio.run(main(parser.parse_args()))
