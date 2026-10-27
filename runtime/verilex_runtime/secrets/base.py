from typing import Protocol


class SecretStore(Protocol):
    def ensure_available(self) -> None:
        raise NotImplementedError

    def get(self, secret_ref: str) -> str | None:
        raise NotImplementedError

    def put(self, secret_ref: str, value: str) -> None:
        raise NotImplementedError

    def delete(self, secret_ref: str) -> None:
        raise NotImplementedError
