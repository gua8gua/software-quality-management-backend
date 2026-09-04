import pytest

from app.utils.text_splitter import CharacterTextSplitter


def test_splitter_creates_overlapping_chunks() -> None:
    splitter = CharacterTextSplitter(chunk_size=5, chunk_overlap=2)
    assert splitter.split("abcdefghij") == ["abcde", "defgh", "ghij"]


def test_splitter_handles_empty_text() -> None:
    splitter = CharacterTextSplitter(chunk_size=10, chunk_overlap=0)
    assert splitter.split("  \n ") == []


def test_splitter_rejects_invalid_overlap() -> None:
    with pytest.raises(ValueError):
        CharacterTextSplitter(chunk_size=10, chunk_overlap=10)

