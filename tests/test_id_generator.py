from uuid import UUID

from app.utils.id_generator import (
    generate_chunk_id,
    generate_document_id,
    generate_kb_id,
    generate_retrieval_log_id,
)


def test_all_internal_ids_are_uuid_strings() -> None:
    generated = {
        generate_kb_id(),
        generate_document_id(),
        generate_chunk_id(),
        generate_retrieval_log_id(),
    }
    assert len(generated) == 4
    assert all(str(UUID(value)) == value for value in generated)

