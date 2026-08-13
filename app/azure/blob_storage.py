"""Azure Blob Storage helper for chat / study asset uploads."""

from __future__ import annotations

import logging
import re
import uuid
from dataclasses import dataclass
from pathlib import Path

from azure.core.exceptions import AzureError
from azure.storage.blob import ContentSettings

from app.core.config import get_settings
from app.core.exceptions import AppError

logger = logging.getLogger(__name__)
# Keep Azure HTTP dump logs out of the hot upload path.
logging.getLogger("azure").setLevel(logging.WARNING)
logging.getLogger("azure.core.pipeline.policies.http_logging_policy").setLevel(
    logging.WARNING
)

_SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")

ALLOWED_CONTENT_TYPES = {
    "image/jpeg",
    "image/png",
    "image/webp",
    "image/gif",
    "application/pdf",
    "text/plain",
    "text/csv",
    "application/msword",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.ms-excel",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/zip",
}

MAX_UPLOAD_BYTES = 25 * 1024 * 1024  # 25 MB


@dataclass(frozen=True)
class UploadedBlob:
    url: str
    filename: str
    content_type: str
    size_bytes: int
    blob_name: str


class AzureBlobStorage:
    def __init__(self) -> None:
        settings = get_settings()
        self._connection_string = settings.AZURE_STORAGE_CONNECTION_STRING
        self._container = settings.AZURE_STORAGE_CONTAINER_NAME
        self._client = None
        if self._connection_string and self._container:
            from azure.storage.blob import BlobServiceClient

            self._client = BlobServiceClient.from_connection_string(
                self._connection_string
            )

    @property
    def configured(self) -> bool:
        return self._client is not None and bool(self._container)

    def upload_bytes(
        self,
        *,
        data: bytes,
        filename: str,
        content_type: str,
        folder: str,
    ) -> UploadedBlob:
        if not self.configured or self._client is None:
            raise AppError(
                "File upload is not configured yet. Please try again later.",
                status_code=503,
            )
        if not data:
            raise AppError("Empty file uploads are not allowed.", status_code=422)
        if len(data) > MAX_UPLOAD_BYTES:
            raise AppError("File is too large (max 25 MB).", status_code=422)

        ctype = (content_type or "application/octet-stream").split(";")[0].strip().lower()
        # Browsers sometimes send application/octet-stream for .docx / .pdf — infer from extension.
        if ctype not in ALLOWED_CONTENT_TYPES:
            suffix = Path(filename or "").suffix.lower()
            inferred = {
                ".pdf": "application/pdf",
                ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                ".doc": "application/msword",
                ".txt": "text/plain",
                ".csv": "text/csv",
                ".png": "image/png",
                ".jpg": "image/jpeg",
                ".jpeg": "image/jpeg",
                ".webp": "image/webp",
                ".gif": "image/gif",
                ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                ".xls": "application/vnd.ms-excel",
                ".zip": "application/zip",
            }.get(suffix)
            if inferred:
                ctype = inferred
        if ctype not in ALLOWED_CONTENT_TYPES:
            raise AppError(
                "This file type isn’t supported. Try an image, PDF, Word, Excel, CSV, or ZIP.",
                status_code=422,
            )

        safe = _SAFE_NAME.sub("_", filename.strip())[:120] or "upload.bin"
        blob_name = f"{folder.strip('/')}/{uuid.uuid4().hex}_{safe}"

        try:
            blob = self._client.get_blob_client(
                container=self._container, blob=blob_name
            )
            blob.upload_blob(
                data,
                overwrite=False,
                content_settings=ContentSettings(content_type=ctype),
                max_concurrency=4,
                timeout=60,
            )
            url = blob.url
        except AzureError:
            logger.exception("Azure blob upload failed")
            raise AppError(
                "We couldn’t upload your file. Please try again.",
                status_code=502,
            ) from None

        return UploadedBlob(
            url=url,
            filename=safe,
            content_type=ctype,
            size_bytes=len(data),
            blob_name=blob_name,
        )


_storage: AzureBlobStorage | None = None


def get_blob_storage() -> AzureBlobStorage:
    global _storage
    if _storage is None:
        _storage = AzureBlobStorage()
    return _storage


def reset_blob_storage() -> None:
    """Test helper."""
    global _storage
    _storage = None
