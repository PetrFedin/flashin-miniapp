#!/usr/bin/env python3
"""R2/S3 write-read-delete preflight with bounded evidence output."""

from __future__ import annotations

import os
import uuid

import boto3


def main() -> int:
    endpoint = os.getenv("S3_ENDPOINT_URL")
    bucket = os.getenv("S3_BUCKET")
    region = os.getenv("S3_REGION", "auto")
    access_key = os.getenv("S3_ACCESS_KEY_ID")
    secret = os.getenv("S3_SECRET_ACCESS_KEY")

    missing = [
        key
        for key, value in {
            "S3_ENDPOINT_URL": endpoint,
            "S3_BUCKET": bucket,
            "S3_ACCESS_KEY_ID": access_key,
            "S3_SECRET_ACCESS_KEY": secret,
        }.items()
        if not value
    ]
    if missing:
        print("Missing S3/R2 env: " + ", ".join(missing))
        return 1

    key = f"preflight/{uuid.uuid4().hex}.txt"
    client = None
    created = False
    try:
        client = boto3.session.Session().client(
            "s3",
            endpoint_url=endpoint,
            region_name=region,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret,
        )
        client.put_object(Bucket=bucket, Key=key, Body=b"flashin-r2-ok", ContentType="text/plain")
        created = True
        obj = client.get_object(Bucket=bucket, Key=key)
        body = obj["Body"].read()
        if body != b"flashin-r2-ok":
            print("R2/S3 check failed: body mismatch")
            return 1
        client.delete_object(Bucket=bucket, Key=key)
        created = False
    except Exception as exc:
        print(f"R2/S3 check failed: {exc.__class__.__name__}")
        return 1
    finally:
        if created and client is not None:
            try:
                client.delete_object(Bucket=bucket, Key=key)
            except Exception:
                pass

    print("R2/S3 check OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
