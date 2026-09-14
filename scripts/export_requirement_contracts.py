"""Export model contracts and readable copies of locally preserved research pages."""

import json
from html.parser import HTMLParser
from pathlib import Path

from app.modules.consistency.schemas import AnalysisInput, Judgement, ReviewInput
from app.modules.requirements.schemas import BuildInput, ClassificationTree, Decomposition


class Reader(HTMLParser):
    def __init__(self):
        super().__init__()
        self.hidden = 0
        self.text = []

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style"}:
            self.hidden += 1

    def handle_endtag(self, tag):
        if tag in {"script", "style"}:
            self.hidden = max(0, self.hidden - 1)
        if tag in {"p", "div", "h1", "h2", "h3", "li", "br"}:
            self.text.append("\n")

    def handle_data(self, data):
        if not self.hidden:
            self.text.append(data)


def main():
    for folder, models in [
        ("requirements", [BuildInput, Decomposition, ClassificationTree]),
        ("consistency", [AnalysisInput, Judgement, ReviewInput]),
    ]:
        path = Path("artifacts") / folder / "contracts.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {m.__name__: m.model_json_schema() for m in models}, ensure_ascii=False, indent=2
            ),
            encoding="utf-8",
        )
    for path in Path("artifacts/requirements/sources").glob("*.html"):
        reader = Reader()
        reader.feed(path.read_text(encoding="utf-8"))
        lines = [" ".join(line.split()) for line in "".join(reader.text).splitlines()]
        path.with_suffix(".txt").write_text(
            "\n".join(line for line in lines if line), encoding="utf-8"
        )


if __name__ == "__main__":
    main()
