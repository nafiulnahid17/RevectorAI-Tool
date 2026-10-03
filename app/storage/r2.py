"""Optional R2 object adapter. Processing uses local scratch; synchronization is explicit."""
from pathlib import PurePosixPath
from app.storage.base import Storage
from app.core.exceptions import EngineError


class R2Storage(Storage):
    def __init__(self, endpoint: str, bucket: str, access_key_id: str, secret_access_key: str):
        import boto3
        self.bucket = bucket
        self.client = boto3.client("s3", endpoint_url=endpoint, aws_access_key_id=access_key_id,
                                   aws_secret_access_key=secret_access_key)

    @staticmethod
    def key(key: str) -> str:
        if not key or key.startswith("/") or ".." in PurePosixPath(key).parts or "\\" in key:
            raise EngineError("INVALID_STORAGE_KEY", "Invalid R2 object key")
        return key

    def put(self, key: str, data: bytes) -> None:
        self.client.put_object(Bucket=self.bucket, Key=self.key(key), Body=data)

    def get(self, key: str) -> bytes:
        return self.client.get_object(Bucket=self.bucket, Key=self.key(key))["Body"].read()

    def exists(self, key: str) -> bool:
        from botocore.exceptions import ClientError
        try:
            self.client.head_object(Bucket=self.bucket, Key=self.key(key))
            return True
        except ClientError as exc:
            if exc.response["Error"]["Code"] in {"404", "NoSuchKey", "NotFound"}:
                return False
            raise

    def delete_prefix(self, prefix: str) -> None:
        prefix = self.key(prefix).rstrip("/") + "/"
        for page in self.client.get_paginator("list_objects_v2").paginate(Bucket=self.bucket, Prefix=prefix):
            objects = [{"Key": o["Key"]} for o in page.get("Contents", [])]
            if objects:
                self.client.delete_objects(Bucket=self.bucket, Delete={"Objects": objects})
