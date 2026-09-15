"""Usage: python scripts/import_dataset.py --dataset safa --file PATH [--validate]."""
from __future__ import annotations
import argparse
import asyncio
import json
from pathlib import Path
from urllib.request import urlopen
from app.db.session import AsyncSessionLocal
from app.modules.dataset_import.dronology import DronologyDataset01Adapter
from app.modules.dataset_import.safa import SafaAdapter
from app.modules.dataset_import.service import DatasetImportService
from app.modules.dataset_import.dataset02 import DronologyDataset02Adapter

DATASET01_URL = "https://sarec.nd.edu/dronology/datasets/01/dronologydataset01.json"

async def main(args):
    path = Path(args.file)
    if args.download:
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            with urlopen(DATASET01_URL, timeout=120) as response:
                path.write_bytes(response.read())
    importer = DatasetImportService()
    importer.register(DronologyDataset01Adapter()); importer.register(DronologyDataset02Adapter()); importer.register(SafaAdapter())
    async with AsyncSessionLocal() as session:
        result = await importer.import_file(session, args.dataset, path)
    print(json.dumps(result, ensure_ascii=False, indent=2))

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", choices=["safa", "dronology-dataset01", "dronology-dataset02"], required=True)
    parser.add_argument("--file", required=True)
    parser.add_argument("--download", action="store_true")
    parser.add_argument("--validate", action="store_true", help="reserved for import validation output")
    asyncio.run(main(parser.parse_args()))
