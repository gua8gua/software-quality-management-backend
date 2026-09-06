"""Fetch pinned original V2 sources and SMOS, verifying Git blob hashes.

No checkout modification or execution of downloaded code. Reuses verified local files.
"""

import argparse
import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.request import Request, urlopen

REPOSITORY = (
    "ArDoCo/Replication-Package-ICSE25_LiSSA-Toward-Generic-Traceability-Link-Recovery-through-RAG"
)
COMMIT = "a8e652f29bbdcc4bdcd3d98cd85aa4a37cb65480"
ROOT = Path(__file__).resolve().parents[1]


def download(url):
    with urlopen(Request(url, headers={"User-Agent": "software-quality-tlr"}), timeout=60) as r:
        return r.read()


def blob_hash(data):
    return hashlib.sha1(f"blob {len(data)}\0".encode() + data).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--destination", type=Path, default=ROOT / "vendor/lissa")
    args = parser.parse_args()
    evidence = ROOT / "artifacts/lissa"
    evidence.mkdir(parents=True, exist_ok=True)
    tree_path = evidence / "upstream-tree.json"
    if not tree_path.exists():
        tree_path.write_bytes(
            download(f"https://api.github.com/repos/{REPOSITORY}/git/trees/{COMMIT}?recursive=1")
        )
    tree = json.loads(tree_path.read_text(encoding="utf-8"))
    if tree.get("truncated") or tree.get("sha") != COMMIT:
        raise ValueError("Unexpected or truncated upstream tree")
    prefix = "LiSSA-RATLR-V2/lissa/"
    entries = [
        x
        for x in tree["tree"]
        if x["type"] == "blob"
        and (
            x["path"] in {"README.md", "LICENSE.md", "CITATION.cff", "LiSSA-RATLR-V2/README.md"}
            or (
                x["path"].startswith(prefix)
                and (
                    x["path"][len(prefix) :].startswith(("src/", "datasets/req2code/SMOS/"))
                    or "/" not in x["path"][len(prefix) :]
                )
            )
        )
    ]
    destination = args.destination.resolve()

    def fetch(entry):
        path = (destination / entry["path"]).resolve()
        if not path.is_relative_to(destination):
            raise ValueError("Unsafe upstream path")
        data = path.read_bytes() if path.exists() else b""
        if blob_hash(data) != entry["sha"]:
            data = download(
                f"https://raw.githubusercontent.com/{REPOSITORY}/{COMMIT}/{entry['path']}"
            )
            if blob_hash(data) != entry["sha"]:
                raise ValueError(f"Hash mismatch: {entry['path']}")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        return {
            "path": entry["path"],
            "git_blob_sha1": entry["sha"],
            "sha256": hashlib.sha256(data).hexdigest(),
            "bytes": len(data),
        }

    with ThreadPoolExecutor(max_workers=8) as pool:
        files = list(pool.map(fetch, entries))
    manifest = {
        "repository": f"https://github.com/{REPOSITORY}",
        "commit": COMMIT,
        "selection": "Original V2 source, build files, SMOS and license; no evaluation caches",
        "files": files,
    }
    (evidence / "dependency-manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"Verified {len(files)} files at {destination}")


if __name__ == "__main__":
    main()
