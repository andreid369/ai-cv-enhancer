"""Processor Lambda: SQS -> Amazon Bedrock -> S3.

For every SQS message (one uploaded CV):
  1. asks the Bedrock model to improve it (Converse API)
  2. saves the job result (improved CV + changes + optional email address) to s3://BUCKET/results/{jobId}.json
     -> S3 event -> SNS -> mailer Lambda (SES email to the user) + delayed cleanup queue

The original CV is never written to S3. The result is deleted as soon as the website reads it,
or 15 minutes after the email notification at the latest (see backend/cleanup).
"""

import json
import logging
import os
import re
from datetime import datetime, timezone

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

from prompt import SYSTEM_PROMPT, build_user_prompt

logger = logging.getLogger()
logger.setLevel(logging.INFO)

BUCKET = os.environ["BUCKET_NAME"]
MODEL_ID = os.environ["MODEL_ID"]
MAX_TOKENS = int(os.environ.get("MAX_TOKENS", "8000"))
MAX_RECEIVE_COUNT = int(os.environ.get("MAX_RECEIVE_COUNT", "3"))

s3 = boto3.client("s3")
bedrock = boto3.client(
    "bedrock-runtime",
    config=Config(read_timeout=170, connect_timeout=10, retries={"max_attempts": 3, "mode": "adaptive"}),
)

# Bedrock errors worth retrying via SQS (message becomes visible again)
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

RETRYABLE_ERRORS = {
    "ThrottlingException",
    "ServiceUnavailableException",
    "InternalServerException",
    "ModelNotReadyException",
    "ModelTimeoutException",
}


class InvalidInput(Exception):
    """The message can never succeed - record a failed result instead of retrying."""


def handler(event, context):
    failures = []
    for record in event.get("Records", []):
        try:
            process_record(record)
        except Exception:  # noqa: BLE001 - report the message back to SQS for a retry
            logger.exception("Failed to process message %s", record.get("messageId"))
            failures.append({"itemIdentifier": record["messageId"]})
    return {"batchItemFailures": failures}


def process_record(record):
    job_id = (
        record.get("messageAttributes", {}).get("jobId", {}).get("stringValue")
        or record["messageId"]
    )
    receive_count = int(record.get("attributes", {}).get("ApproximateReceiveCount", "1"))
    logger.info("Processing job %s (attempt %s)", job_id, receive_count)

    try:
        payload = parse_payload(record["body"])
    except InvalidInput as exc:
        write_result(job_id, status="failed", error=str(exc))
        return

    try:
        model_text, usage, stop_reason = improve_cv(payload["cvText"], payload.get("targetRole"))
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "Unknown")
        if code in RETRYABLE_ERRORS and receive_count < MAX_RECEIVE_COUNT:
            raise  # let SQS retry later
        logger.error("Bedrock error for job %s: %s", job_id, exc)
        write_result(
            job_id,
            status="failed",
            error=f"The AI model could not process this CV ({code}). Try again later.",
            email=payload.get("email"),
        )
        return

    improved_cv, changes = parse_model_output(model_text)
    if stop_reason == "max_tokens":
        changes.append("The answer hit the output length limit, so the end of the CV may be cut off.")

    write_result(
        job_id,
        status="completed",
        fileName=payload["fileName"],
        targetRole=payload.get("targetRole") or None,
        email=payload.get("email"),  # mailer Lambda sends the CV here; deleted with the result
        improvedCv=improved_cv,
        changes=changes,
        model=MODEL_ID,
        usage=usage,
    )


def parse_payload(body: str) -> dict:
    try:
        payload = json.loads(body)
    except json.JSONDecodeError as exc:
        raise InvalidInput("The upload was not valid JSON.") from exc
    cv_text = (payload.get("cvText") or "").strip()
    if len(cv_text) < 200:
        raise InvalidInput("The CV text is too short. Upload a CV with selectable text, not a scanned image.")
    payload["cvText"] = cv_text[:60000]
    payload["fileName"] = (payload.get("fileName") or "cv")[:200]
    email = (payload.get("email") or "").strip()
    payload["email"] = email if EMAIL_RE.match(email) and len(email) <= 254 else None
    return payload


def improve_cv(cv_text: str, target_role: str | None):
    response = bedrock.converse(
        modelId=MODEL_ID,
        system=[{"text": SYSTEM_PROMPT}],
        messages=[{"role": "user", "content": [{"text": build_user_prompt(cv_text, target_role)}]}],
        inferenceConfig={"maxTokens": MAX_TOKENS},
    )
    text = "".join(
        block.get("text", "") for block in response["output"]["message"]["content"]
    )
    usage = response.get("usage", {})
    logger.info("Bedrock usage: %s, stopReason: %s", usage, response.get("stopReason"))
    return text, usage, response.get("stopReason")


def parse_model_output(text: str):
    cv_match = re.search(r"<improved_cv>\s*(.*?)\s*(?:</improved_cv>|$)", text, re.S)
    changes_match = re.search(r"<changes>\s*(.*?)\s*(?:</changes>|$)", text, re.S)

    improved_cv = cv_match.group(1).strip() if cv_match else text.strip()
    changes = []
    if changes_match:
        for line in changes_match.group(1).splitlines():
            line = line.strip()
            if line.startswith(("-", "*", "•")):
                changes.append(line.lstrip("-*• ").strip())
    return improved_cv, changes


def write_result(job_id: str, status: str, **fields):
    result = {
        "jobId": job_id,
        "status": status,
        "completedAt": datetime.now(timezone.utc).isoformat(),
        **fields,
    }
    s3.put_object(
        Bucket=BUCKET,
        Key=f"results/{job_id}.json",
        Body=json.dumps(result, ensure_ascii=False).encode("utf-8"),
        ContentType="application/json",
    )
    logger.info("Job %s %s", job_id, status)
