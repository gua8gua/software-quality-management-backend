# Dataset ingestion

The registry accepts a normalized dataset adapter and persists its project, artifacts and
dataset-provided (gold) trace links idempotently. Model-generated links remain in `tlr_links`.

Example:

```powershell
python scripts/import_dataset.py --dataset safa --file ..\software-quality-agent\datasets\SAFA-Artifacts\V1-simplified.json
python scripts/import_dataset.py --dataset dronology-dataset01 --file ..\software-quality-agent\datasets\dronology-dataset01\dronologydataset01.json --download
```

`--download` is opt-in and currently applies to Dataset 01 only. It never overwrites an existing
source file. The importer does not run at service startup.
