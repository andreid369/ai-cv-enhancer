"""Status Lambda: GET /cv/{jobId}

Returns {"status": "processing"} until the processor has written results/{jobId}.json.
Once the job is finished it returns the result and deletes it from S3 straight away,
unless the user asked for an email copy: then the mailer Lambda may still need it and the
cleanup queue deletes it within 15 minutes.
"""

import json
import os
import re

import boto3
from botocore.exceptions import ClientError

BUCKET = os.environ["BUCKET_NAME"]
ALLOWED_ORIGIN = os.environ.get("ALLOWED_ORIGIN", "*")
JOB_ID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")

s3 = boto3.client("s3")


def handler(event, context):
    job_id = ((event or {}).get("pathParameters") or {}).get("jobId", "")
    if not JOB_ID_RE.match(job_id):
        return response(400, {"message": "Unknown job ID."})

    key = f"results/{job_id}.json"
    try:
        obj = s3.get_object(Bucket=BUCKET, Key=key)
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") in ("NoSuchKey", "404"):
            return response(200, {"jobId": job_id, "status": "processing"})
        raise

    result = json.loads(obj["Body"].read())
    if result.get("email"):
        # The mailer Lambda may still need it - the cleanup queue deletes it within 15 minutes.
        result.pop("email")
    else:
        s3.delete_object(Bucket=BUCKET, Key=key)  # read once, then gone
    return response(200, result)


def response(status_code: int, body: dict):
    return {
        "statusCode": status_code,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": ALLOWED_ORIGIN,
            "Cache-Control": "no-store",
        },
        "body": json.dumps(body, ensure_ascii=False),
    }
