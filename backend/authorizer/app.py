"""Lambda authorizer for POST /cv: allows at most MAX_PER_IP CV submissions per client IP.

API Gateway gives the real client IP in requestContext.identity.sourceIp (it can't be faked
with an X-Forwarded-For header). The IP is stored only as a salted SHA-256 hash.

A DynamoDB conditional update increments the counter atomically, so two parallel requests
from the same IP can't both slip past the limit.
"""

import hashlib
import os
import time

import boto3
from botocore.exceptions import ClientError

TABLE = boto3.resource("dynamodb").Table(os.environ["TABLE_NAME"])
MAX_PER_IP = int(os.environ.get("MAX_PER_IP", "2"))
RESET_DAYS = int(os.environ.get("RESET_DAYS", "0"))  # 0 = the limit never resets
SALT = os.environ["HASH_SALT"]


def handler(event, context):
    source_ip = event["requestContext"]["identity"]["sourceIp"]
    ip_hash = hashlib.sha256(f"{SALT}:{source_ip}".encode()).hexdigest()

    update = "ADD submissions :one SET firstSeen = if_not_exists(firstSeen, :now)"
    values = {":one": 1, ":max": MAX_PER_IP, ":now": int(time.time())}
    if RESET_DAYS > 0:
        update += ", expiresAt = if_not_exists(expiresAt, :exp)"
        values[":exp"] = int(time.time()) + RESET_DAYS * 86400

    try:
        result = TABLE.update_item(
            Key={"ipHash": ip_hash},
            UpdateExpression=update,
            ConditionExpression="attribute_not_exists(submissions) OR submissions < :max",
            ExpressionAttributeValues=values,
            ReturnValues="UPDATED_NEW",
        )
        used = int(result["Attributes"]["submissions"])
        print(f"allow ip={ip_hash[:12]} used={used}/{MAX_PER_IP}")
        return policy("Allow", event["methodArn"], ip_hash, MAX_PER_IP - used)
    except ClientError as exc:
        if exc.response["Error"]["Code"] == "ConditionalCheckFailedException":
            print(f"deny ip={ip_hash[:12]} limit reached")
            return policy("Deny", event["methodArn"], ip_hash, 0)
        raise  # fail closed: API Gateway answers 500


def policy(effect, method_arn, principal, remaining):
    return {
        "principalId": principal[:32],
        "policyDocument": {
            "Version": "2012-10-17",
            "Statement": [{"Action": "execute-api:Invoke", "Effect": effect, "Resource": method_arn}],
        },
        "context": {"remaining": remaining},
    }
