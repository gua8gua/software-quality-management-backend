"""Audit None/Chunk/Method against original SMOS Java, without model calls."""

import asyncio
import json
from collections import Counter
from pathlib import Path
from types import SimpleNamespace

from app.modules.tlr.preprocessing import preprocess_typed
from app.modules.tlr.schemas import RunOptions


async def main():
    directory = Path("artifacts/tlr/preprocessing-audit")
    await asyncio.to_thread(directory.mkdir, parents=True, exist_ok=True)
    dataset = json.loads(
        await asyncio.to_thread(Path("artifacts/tlr/smos/dataset.json").read_text, encoding="utf-8")
    )
    report = []
    for artifact in dataset["artifacts"]:
        if artifact["kind"] != "code":
            continue
        a = SimpleNamespace(
            **{
                **artifact,
                "id": artifact["external_id"],
                "structure": artifact.get("structure", {}),
            }
        )
        # SMOS XML IDs name Java source files; preserve original locator in the input record.
        if not a.locator.lower().endswith(".java"):
            a.locator = (
                a.external_id if a.external_id.endswith(".java") else a.external_id + ".java"
            )
        for mode in ("artifact", "chunk", "method", "auto"):
            try:
                units = await preprocess_typed(
                    a,
                    "source",
                    "audit",
                    RunOptions(source_preprocessor=mode, chunk_size=2000),
                    None,
                )
                valid = all(a.content[u.start : u.end] == u.content for u in units)
                # No non-whitespace original source character may be lost by segmentation.
                restored = "".join(u.content for u in units)
                if restored.strip() != a.content.strip() or not valid:
                    raise AssertionError("source coverage or offsets differ")
                row = {
                    "artifact": a.external_id,
                    "strategy": mode,
                    "unit_count": len(units),
                    "status": "passed",
                    "units": [
                        {
                            "start": u.start,
                            "end": u.end,
                            "sha256": u.sha256,
                            "processing": u.processing,
                        }
                        for u in units
                    ],
                }
            except Exception as exc:
                row = {
                    "artifact": a.external_id,
                    "strategy": mode,
                    "status": "failed",
                    "reason": str(exc),
                }
            report.append(row)
    (directory / "units.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    summary = {
        mode: dict(Counter(r["status"] for r in report if r["strategy"] == mode))
        for mode in ("artifact", "chunk", "method", "auto")
    }
    (directory / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(summary)


if __name__ == "__main__":
    asyncio.run(main())
