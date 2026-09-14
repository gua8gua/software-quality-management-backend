"""Build requirement hierarchy, optionally run hierarchical TLR and implementation analysis."""

import argparse
import asyncio
import json
from pathlib import Path

from app.api.dependencies import get_embedding_provider, get_llm_provider
from app.core.config import get_settings
from app.db.session import AsyncSessionLocal, engine
from app.modules.consistency.schemas import AnalysisInput
from app.modules.consistency.service import ConsistencyService
from app.modules.requirements.schemas import BuildInput
from app.modules.requirements.service import RequirementService
from app.modules.tlr.schemas import RunInput, RunOptions
from app.modules.tlr.service import TlrService
from scripts.run_consistency import markdown


async def main(args):
    settings = get_settings()
    try:
        async with AsyncSessionLocal() as session:
            llm, embedding = get_llm_provider(settings), get_embedding_provider(settings)
            scope = {"tenant_id": args.tenant_id, "project_id": args.project_id}
            hierarchy = await RequirementService(session, settings, llm).build(
                BuildInput(**scope, dataset_id=args.dataset_id, requirement_ids=args.requirement_id)
            )
            output = Path(args.output) / hierarchy["id"]
            output.mkdir(parents=True, exist_ok=True)

            def save(name, value):
                (output / name).write_text(
                    json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8"
                )

            save("requirements.json", hierarchy)
            print("Requirement hierarchy:", hierarchy["id"], flush=True)
            if not (args.match or args.consistency):
                return
            service = TlrService(session, embedding, llm, settings)
            artifacts = await service.repo.artifacts(args.dataset_id)
            targets = args.target_id or [
                a.external_id
                for a in artifacts
                if a.kind == "code"
                and (a.structure or {}).get("content_status") != "reference_only"
            ]
            run = await service.create_run(
                RunInput(
                    **scope,
                    dataset_id=args.dataset_id,
                    source_ids=args.requirement_id,
                    target_ids=targets,
                    options=RunOptions(
                        requirements_run_id=hierarchy["id"],
                        target_preprocessor="auto",
                        top_k=args.top_k,
                    ),
                )
            )
            save("tlr-run-id.json", {"id": run.id})
            run = await service.execute(run.id, args.tenant_id, args.project_id)
            save("tlr-manifest.json", run.manifest)
            print("TLR:", run.id, flush=True)
            if args.consistency:
                judge = ConsistencyService(session, settings, llm)
                for start in range(0, len(args.requirement_id), 50):
                    analysis = await judge.create(
                        AnalysisInput(
                            **scope,
                            tlr_run_id=run.id,
                            requirement_ids=args.requirement_id[start : start + 50],
                        )
                    )
                    save(f"consistency-{start}-id.json", {"id": analysis.id})
                    await judge.execute(analysis.id, args.tenant_id, args.project_id)
                    report = await judge.view(analysis)
                    save(f"consistency-{start}.json", report)
                    (output / f"consistency-{start}.md").write_text(
                        markdown(report), encoding="utf-8"
                    )
                    if analysis.status == "failed":
                        raise SystemExit("Consistency calls failed; saved run ID can be retried")
            print("Output:", output)
    finally:
        await engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("tenant-id", "project-id", "dataset-id"):
        parser.add_argument("--" + name, required=True)
    parser.add_argument("--requirement-id", action="append", required=True)
    parser.add_argument("--target-id", action="append", default=[])
    parser.add_argument("--match", action="store_true")
    parser.add_argument("--consistency", action="store_true")
    parser.add_argument("--top-k", type=int, default=10)
    parser.add_argument("--output", default="outputs/requirement-workflow")
    asyncio.run(main(parser.parse_args()))
