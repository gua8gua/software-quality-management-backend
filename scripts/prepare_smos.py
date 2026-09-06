"""Convert original SMOS bodies to API input JSON; keep gold separate from model input.

Run from the backend root: python -m scripts.prepare_smos
"""

import argparse
import json
from pathlib import Path

from app.modules.tlr.importers import file_provenance, read_collection, read_gold
from app.modules.tlr.schemas import DatasetInput, EvaluationInput
from scripts.fetch_lissa import COMMIT, REPOSITORY, ROOT


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dataset",
        type=Path,
        default=(ROOT / "vendor/lissa/LiSSA-RATLR-V2/lissa/datasets/req2code/SMOS"),
    )
    parser.add_argument("--output", type=Path, default=ROOT / "artifacts/tlr/smos")
    args = parser.parse_args()
    source_file, target_file = args.dataset / "source_req.xml", args.dataset / "target_code.xml"
    sources = read_collection(source_file, "requirement", COMMIT)
    targets = read_collection(target_file, "code", COMMIT)
    for artifact in targets:
        artifact.structure = {**artifact.structure, "language": "java"}
    provenance = {
        "repository": f"https://github.com/{REPOSITORY}",
        "commit": COMMIT,
        "dataset": "SMOS",
        "license": "See upstream LICENSE.md and dataset README.txt",
        "files": [file_provenance(f) for f in (source_file, target_file)],
    }
    dataset = DatasetInput(
        tenant_id="local",
        project_id="smos",
        version=COMMIT,
        provenance=provenance,
        artifacts=sources + targets,
    )
    gold = EvaluationInput(
        gold_links=read_gold(args.dataset / "answer.csv"),
        provenance={**provenance, "gold_file": file_provenance(args.dataset / "answer.csv")},
    )
    if any(
        g.source_id not in {a.external_id for a in sources}
        or g.target_id not in {a.external_id for a in targets}
        for g in gold.gold_links
    ):
        raise ValueError("Gold identifiers do not match collections")
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "dataset.json").write_text(dataset.model_dump_json(indent=2), encoding="utf-8")
    (args.output / "gold.json").write_text(gold.model_dump_json(indent=2), encoding="utf-8")
    template = {
        "tenant_id": "local",
        "project_id": "smos",
        "dataset_id": "REPLACE_WITH_IMPORT_ID",
        "source_ids": [a.external_id for a in sources],
        "target_ids": [a.external_id for a in targets],
        "options": {
            "top_k": 20,
            "source_preprocessor": "artifact",
            "target_preprocessor": "chunk",
            "chunk_size": 4000,
            "retrieval_backend": "python",
        },
    }
    (args.output / "run-template.json").write_text(json.dumps(template, indent=2), encoding="utf-8")
    print(
        f"Prepared {len(sources)} sources, {len(targets)} targets, "
        f"{len(gold.gold_links)} gold links"
    )


if __name__ == "__main__":
    main()
