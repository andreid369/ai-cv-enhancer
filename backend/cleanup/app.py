"""Cleanup Lambda: deletes the CV result from S3 after the email notification went out.

Flow: S3 result saved -> S3 event -> SNS topic -> (1) email  (2) cleanup SQS queue.
The cleanup queue delays every message by 15 minutes (the SQS maximum),
which gives the website time to show the result. Then this function deletes the object.
If the website already fetched the result, the status Lambda deleted it first and this is a no-op.
"""

import json
import os
from urllib.parse import unquote_plus

import boto3

BUCKET = os.environ["BUCKET_NAME"]
s3 = boto3.client("s3")


def handler(event, context):
    for record in event.get("Records", []):
        s3_event = json.loads(record["body"])  # raw S3 event (SNS raw message delivery)
        for item in s3_event.get("Records", []):
            key = unquote_plus(item["s3"]["object"]["key"])
            if not key.startswith("results/"):
                continue
            s3.delete_object(Bucket=BUCKET, Key=key)  # idempotent: no error if already gone
            print(f"deleted s3://{BUCKET}/{key}")
