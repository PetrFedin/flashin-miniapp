#!/usr/bin/env python3
import os
import sys
import uuid

import boto3

endpoint = os.getenv("S3_ENDPOINT_URL")
bucket = os.getenv("S3_BUCKET")
region = os.getenv("S3_REGION", "auto")
access_key = os.getenv("S3_ACCESS_KEY_ID")
secret = os.getenv("S3_SECRET_ACCESS_KEY")

missing = [k for k, v in {
    "S3_ENDPOINT_URL": endpoint,
    "S3_BUCKET": bucket,
    "S3_ACCESS_KEY_ID": access_key,
    "S3_SECRET_ACCESS_KEY": secret,
}.items() if not v]

if missing:
    print("Missing S3/R2 env:", missing)
    sys.exit(1)

client = boto3.session.Session().client(
    "s3",
    endpoint_url=endpoint,
    region_name=region,
    aws_access_key_id=access_key,
    aws_secret_access_key=secret,
)

key = f"preflight/{uuid.uuid4().hex}.txt"
client.put_object(Bucket=bucket, Key=key, Body=b"flashin-r2-ok", ContentType="text/plain")
obj = client.get_object(Bucket=bucket, Key=key)
body = obj["Body"].read()
client.delete_object(Bucket=bucket, Key=key)

if body != b"flashin-r2-ok":
    print("R2/S3 check failed: body mismatch")
    sys.exit(1)

print("R2/S3 check OK")
