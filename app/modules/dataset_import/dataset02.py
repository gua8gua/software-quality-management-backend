from pathlib import Path
import hashlib
from .service import NormalizedArtifact, NormalizedDataset

class DronologyDataset02Adapter:
    name = "dronology-dataset02"
    def load(self, path: Path) -> NormalizedDataset:
        files = sorted(path.rglob("*.md")) if path.is_dir() else [path]
        artifacts = [NormalizedArtifact(f.stem, "use case", f.read_text(encoding="utf-8-sig"),
            str(f.relative_to(path) if path.is_dir() else f.name), str(f), {"source_file": str(f)}) for f in files]
        return NormalizedDataset("dronology_dataset_02", "SPLC-2020", "dronology", "Dronology",
            {"source_dataset": "dronology_dataset_02", "source_version": "SPLC-2020",
             "source_sha256": hashlib.sha256(b"".join(f.read_bytes() for f in files)).hexdigest(),
             "repository": "https://github.com/SAREC-Lab/sUAS-UseCases"}, artifacts, [])
