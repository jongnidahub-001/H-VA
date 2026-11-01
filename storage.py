"""JSON document storage: Cloud Storage on Cloud Run (DATA_BUCKET set), local folder otherwise.

Keys look like "chat_history/<session_id>.json" or "score/<file>.json".
On Cloud Storage, chat_history/ is deleted after one day by a bucket lifecycle rule.
"""
import json
import os
import time
from typing import Iterator, Optional, Tuple


class LocalStore:
    def __init__(self, root: str):
        self.root = root

    def _path(self, key: str) -> str:
        return os.path.join(self.root, key)

    def get(self, key: str) -> Optional[dict]:
        try:
            with open(self._path(key), "r", encoding="utf-8") as f:
                return json.load(f)
        except FileNotFoundError:
            return None

    def put(self, key: str, data: dict) -> None:
        path = self._path(key)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

    def items(self, prefix: str) -> Iterator[Tuple[str, dict]]:
        folder = self._path(prefix)
        if not os.path.isdir(folder):
            return
        for name in sorted(os.listdir(folder)):
            if name.endswith(".json"):
                data = self.get(prefix + name)
                if data is not None:
                    yield prefix + name, data

    def delete_older_than(self, prefix: str, max_age_hours: int) -> None:
        folder = self._path(prefix)
        if not os.path.isdir(folder):
            return
        cutoff = time.time() - max_age_hours * 3600
        for name in os.listdir(folder):
            path = os.path.join(folder, name)
            if name.endswith(".json") and os.path.getmtime(path) < cutoff:
                os.remove(path)


class GCSStore:
    def __init__(self, bucket: str):
        from google.cloud import storage

        self.bucket = storage.Client().bucket(bucket)

    def get(self, key: str) -> Optional[dict]:
        from google.api_core.exceptions import NotFound

        try:
            return json.loads(self.bucket.blob(key).download_as_bytes())
        except NotFound:
            return None

    def put(self, key: str, data: dict) -> None:
        self.bucket.blob(key).upload_from_string(
            json.dumps(data, ensure_ascii=False), content_type="application/json"
        )

    def items(self, prefix: str) -> Iterator[Tuple[str, dict]]:
        for blob in self.bucket.list_blobs(prefix=prefix):
            if blob.name.endswith(".json"):
                yield blob.name, json.loads(blob.download_as_bytes())

    def delete_older_than(self, prefix: str, max_age_hours: int) -> None:
        pass  # handled by the bucket lifecycle rule


def get_store():
    bucket = os.getenv("DATA_BUCKET")
    if bucket:
        return GCSStore(bucket)
    return LocalStore(os.getenv("DATA_DIR", "log"))


# Shared by the app and settings
store = get_store()
