from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import Settings
from app.core.error_codes import ErrorCode
from app.core.exceptions import AppError


class CredentialVault:
    def __init__(self, settings: Settings):
        key = settings.model_config_encryption_key.strip().encode()
        if not key:
            path = Path(settings.model_config_key_file)
            path.parent.mkdir(parents=True, exist_ok=True)
            try:
                key = path.read_bytes().strip()
            except FileNotFoundError:
                key = Fernet.generate_key()
                try:
                    with path.open("xb") as handle:
                        handle.write(key)
                except FileExistsError:
                    key = path.read_bytes().strip()
        try:
            self._fernet = Fernet(key)
        except ValueError as exc:
            raise AppError(ErrorCode.LLM_CONFIG_ERROR, "模型密钥加密配置无效") from exc

    def encrypt(self, secret: str) -> bytes | None:
        return self._fernet.encrypt(secret.encode()) if secret else None

    def decrypt(self, ciphertext: bytes | None) -> str:
        if not ciphertext:
            return ""
        try:
            return self._fernet.decrypt(ciphertext).decode()
        except InvalidToken as exc:
            raise AppError(ErrorCode.LLM_CONFIG_ERROR, "模型 API 密钥无法解密") from exc
