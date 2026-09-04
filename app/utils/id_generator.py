from uuid import uuid4


def _uuid() -> str:
    return str(uuid4())


def generate_kb_id() -> str:
    return _uuid()


def generate_document_id() -> str:
    return _uuid()


def generate_chunk_id() -> str:
    return _uuid()


def generate_retrieval_log_id() -> str:
    return _uuid()

