import asyncio
import io
from typing import Protocol

from minio import Minio

from bond_trading.core.config import StorageSettings


class ObjectStorage(Protocol):
    async def ensure_bucket(self) -> None: ...

    async def put(self, object_key: str, content: bytes, content_type: str) -> None: ...

    async def get(self, object_key: str) -> bytes: ...

    async def delete(self, object_key: str) -> None: ...


class MinioObjectStorage:
    def __init__(self, settings: StorageSettings) -> None:
        self._bucket = settings.bucket
        self._client = Minio(
            endpoint=settings.endpoint,
            access_key=settings.access_key,
            secret_key=settings.secret_key,
            secure=settings.secure,
            region=settings.region,
        )

    async def ensure_bucket(self) -> None:
        def ensure() -> None:
            if not self._client.bucket_exists(self._bucket):
                self._client.make_bucket(self._bucket)

        await asyncio.to_thread(ensure)

    async def put(self, object_key: str, content: bytes, content_type: str) -> None:
        await asyncio.to_thread(
            self._client.put_object,
            self._bucket,
            object_key,
            io.BytesIO(content),
            len(content),
            content_type=content_type,
        )

    async def get(self, object_key: str) -> bytes:
        def download() -> bytes:
            response = self._client.get_object(self._bucket, object_key)
            try:
                return response.read()
            finally:
                response.close()
                response.release_conn()

        return await asyncio.to_thread(download)

    async def delete(self, object_key: str) -> None:
        await asyncio.to_thread(self._client.remove_object, self._bucket, object_key)


class MemoryObjectStorage:
    def __init__(self) -> None:
        self.objects: dict[str, tuple[bytes, str]] = {}

    async def ensure_bucket(self) -> None:
        return None

    async def put(self, object_key: str, content: bytes, content_type: str) -> None:
        self.objects[object_key] = (content, content_type)

    async def get(self, object_key: str) -> bytes:
        return self.objects[object_key][0]

    async def delete(self, object_key: str) -> None:
        self.objects.pop(object_key, None)
