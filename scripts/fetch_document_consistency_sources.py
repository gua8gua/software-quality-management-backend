"""Preserve primary research pages and repository inventories without invoking Git."""
import asyncio
import hashlib
import json
from pathlib import Path

import httpx

from scripts.export_requirement_contracts import Reader

ROOT = Path("artifacts/document-consistency/sources")
SOURCES = {
    "cross-level.html": "https://ink.library.smu.edu.sg/sis_research/10806/",
    "rsa.html": "https://arxiv.org/html/2312.04463v1",
    "rsa.pdf": "https://arxiv.org/pdf/2312.04463",
    "coverage.html": "https://www.researchgate.net/publication/380270963_Supporting_High-Level_to_Low-Level_Requirements_Coverage_Reviewing_with_Large_Language_Models",
    "coverage-replication.json": "https://zenodo.org/api/records/10149490",
    "cross-level-tree.json": "https://api.github.com/repos/Gechuyan/Cross-Level-Requirements-Tracing-Based-on-Large-Language-Model/git/trees/main?recursive=1",
}


async def main():
    ROOT.mkdir(parents=True, exist_ok=True)
    async with httpx.AsyncClient(follow_redirects=True, timeout=40) as client:
        async def save(name, url):
            try:
                response = await client.get(url)
                response.raise_for_status()
                (ROOT / name).write_bytes(response.content)
                if name.endswith(".html"):
                    reader = Reader()
                    reader.feed(response.text)
                    (ROOT / name).with_suffix(".txt").write_text(
                        "\n".join(line.strip() for line in "".join(reader.text).splitlines()
                                  if line.strip()), encoding="utf-8")
                return {"file": name, "url": url, "sha256": hashlib.sha256(response.content).hexdigest()}
            except Exception as exc:
                return {"file": name, "url": url, "error": type(exc).__name__}
        manifest = await asyncio.gather(*(save(n, u) for n, u in SOURCES.items()))
    (ROOT / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2),
                                       encoding="utf-8")
    print(json.dumps(manifest, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())
