"""File-format extraction is separate from user-supplied lifecycle classification."""

from io import BytesIO
from pathlib import PurePath
from zipfile import ZipFile

from docx import Document
from pypdf import PdfReader

KINDS = {
    "hazard": "危险 / 风险分析",
    "natural_language": "其它自然语言文档",
    "lifecycle_document": "其它生命周期文档",
    "architecture_model": "架构模型",
    "package": "包 / 容器结构",
    "requirement": "需求规格 / 用户需求",
    "design": "设计文档",
    "architecture_design": "架构设计",
    "detailed_design": "详细设计",
    "interface": "接口说明",
    "code": "源代码",
    "test_code": "测试代码",
    "test_plan": "测试计划",
    "test_case": "测试用例",
    "test_report": "测试报告",
    "test": "测试资料",
    "build": "构建记录",
    "defect": "缺陷记录",
    "review": "评审记录",
    "release": "发布说明",
    "operation": "运维资料",
    "feedback": "用户反馈",
    "configuration": "配置记录",
    "change": "变更记录",
}
TEXT_EXTENSIONS = {
    ".txt",
    ".md",
    ".csv",
    ".json",
    ".xml",
    ".yaml",
    ".yml",
    ".toml",
    ".ini",
    ".log",
    ".py",
    ".java",
    ".js",
    ".jsx",
    ".ts",
    ".tsx",
    ".c",
    ".cpp",
    ".h",
    ".hpp",
    ".cs",
    ".go",
    ".rs",
    ".sh",
    ".sql",
    ".html",
    ".css",
    ".vue",
    ".feature",
    ".robot",
    ".rst",
}
MAX_BYTES = 10 * 1024 * 1024


def extract_text(filename: str, data: bytes) -> str:
    if not data or len(data) > MAX_BYTES:
        raise ValueError("文件为空或超过 10 MiB")
    extension = PurePath(filename).suffix.lower()
    if extension in TEXT_EXTENSIONS:
        try:
            text = data.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise ValueError("文本文件请使用 UTF-8 编码") from exc
        if "\x00" in text:
            raise ValueError("不支持二进制正文")
    elif extension == ".pdf":
        reader = PdfReader(BytesIO(data))
        if reader.is_encrypted or len(reader.pages) > 500:
            raise ValueError("不支持加密或超过 500 页的 PDF")
        parts = []
        for page in reader.pages:
            parts.append(page.extract_text() or "")
            if sum(map(len, parts)) > 500_000:
                raise ValueError("提取正文超过 500000 字符")
        text = "\n".join(parts)
    elif extension == ".docx":
        with ZipFile(BytesIO(data)) as archive:
            if sum(info.file_size for info in archive.infolist()) > 50 * 1024 * 1024:
                raise ValueError("DOCX 解压内容过大")
        document = Document(BytesIO(data))
        # Keep document order, including test cases represented in tables.
        parts = []
        for block in document.iter_inner_content():
            if hasattr(block, "rows"):
                parts.append("\n".join("\t".join(c.text for c in r.cells) for r in block.rows))
            else:
                parts.append(block.text)
        text = "\n".join(parts)
    else:
        raise ValueError("不支持该文件格式，请上传文本、代码、PDF 或 DOCX")
    if not text.strip():
        raise ValueError("无法提取正文；扫描件需先进行 OCR")
    if len(text) > 500_000:
        raise ValueError("提取正文超过 500000 字符")
    return text
