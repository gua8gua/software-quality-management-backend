from pathlib import Path

from app.core.config import Settings


class ArtifactContentReader:
    """Reads inline content or a safe path relative to the configured artifact root."""

    def __init__(self, settings: Settings):
        self.root = Path(settings.artifact_context_cache_dir).resolve().parent.parent

    def read(self, artifact) -> tuple[bool, str | None, str | None]:
        if artifact.content_mode == "inline":
            if artifact.content and artifact.content.strip():
                return True, artifact.content, None
            return False, None, "当前资料没有可用正文"
        if artifact.content_mode != "path" or not artifact.content_path:
            return False, None, "当前资料没有可用正文"
        candidate = (self.root / artifact.content_path).resolve()
        try:
            candidate.relative_to(self.root)
        except ValueError:
            return False, None, "正文引用路径不在数据根目录内"
        if not candidate.is_file():
            return False, None, "正文文件不存在，当前资料仅提供引用信息"
        try:
            return True, candidate.read_text(encoding="utf-8-sig"), None
        except UnicodeDecodeError:
            return False, None, "正文文件不是可读取的文本编码"
