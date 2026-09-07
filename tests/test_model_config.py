from cryptography.fernet import Fernet

from app.api.middleware import _preview_body
from app.core.config import Settings
from app.core.exceptions import AppError
from app.modules.model_config.service import (
    normalize_url,
    ollama_model_metadata,
    ollama_native_root,
)
from app.modules.model_config.vault import CredentialVault


def test_url_classifies_local_and_requires_https_for_remote():
    url, local = normalize_url("http://127.0.0.1:11434/v1/")
    assert (url, local) == ("http://127.0.0.1:11434/v1", True)
    assert normalize_url("https://api.example.com/v1") == (
        "https://api.example.com/v1",
        False,
    )
    try:
        normalize_url("http://api.example.com/v1")
    except AppError as exc:
        assert "HTTPS" in exc.message
    else:
        raise AssertionError("remote HTTP URL was accepted")


def test_vault_encrypts_credentials_and_request_log_redacts_them(tmp_path):
    settings = Settings(
        database_url="sqlite+aiosqlite://",
        model_config_encryption_key=Fernet.generate_key().decode(),
        model_config_key_file=str(tmp_path / "unused"),
    )
    vault = CredentialVault(settings)
    encrypted = vault.encrypt("top-secret")
    assert encrypted and b"top-secret" not in encrypted
    assert vault.decrypt(encrypted) == "top-secret"
    preview = _preview_body(b'{"name":"cloud","api_key":"top-secret"}')
    assert "top-secret" not in preview
    assert "<redacted>" in preview


def test_ollama_model_capabilities_and_embedding_dimension_are_mapped():
    assert ollama_native_root("http://127.0.0.1:11434/v1") == "http://127.0.0.1:11434"
    metadata = ollama_model_metadata(
        {
            "capabilities": ["embedding"],
            "details": {
                "family": "bert",
                "parameter_size": "300M",
                "quantization_level": "F16",
            },
            "model_info": {"bert.embedding_length": 768},
        }
    )
    assert metadata["capabilities"] == ["embedding"]
    assert metadata["embedding_dimension"] == 768
    assert metadata["capability_source"] == "ollama"


def test_ollama_internal_embedding_length_does_not_misclassify_chat_model():
    metadata = ollama_model_metadata(
        {
            "capabilities": ["completion"],
            "model_info": {"llama.embedding_length": 4096},
        }
    )
    assert metadata["capabilities"] == ["chat"]
    assert metadata["embedding_dimension"] is None
