"""Mailer Lambda: emails the improved CV to the address the user typed on the website.

Flow: S3 results/{jobId}.json saved -> S3 event -> SNS topic -> this function -> Amazon SES.
The email has the CV in the body and attached as improved-cv.md.
Nothing is stored here; the result object is deleted by the cleanup queue within 15 minutes.
"""

import html
import json
import os
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr
from urllib.parse import unquote_plus

import boto3
from botocore.exceptions import ClientError

BUCKET = os.environ["BUCKET_NAME"]
SENDER = os.environ["SENDER_EMAIL"]
SENDER_NAME = os.environ.get("SENDER_NAME", "Blue Pencil CV editor")
WEBSITE_URL = os.environ.get("WEBSITE_URL", "")

s3 = boto3.client("s3")
ses = boto3.client("sesv2")

# SES errors that will never succeed on retry (e.g. unverified recipient while in the SES sandbox)
PERMANENT_ERRORS = {"MessageRejected", "MailFromDomainNotVerifiedException", "BadRequestException", "NotFoundException"}


def handler(event, context):
    for record in event.get("Records", []):
        s3_event = json.loads(record["Sns"]["Message"])
        for item in s3_event.get("Records", []):  # s3:TestEvent has no Records
            key = unquote_plus(item["s3"]["object"]["key"])
            if key.startswith("results/") and key.endswith(".json"):
                send_for(key)


def send_for(key: str):
    try:
        result = json.loads(s3.get_object(Bucket=BUCKET, Key=key)["Body"].read())
    except ClientError as exc:
        if exc.response["Error"]["Code"] in ("NoSuchKey", "404"):
            print(f"{key} already deleted, nothing to send")
            return
        raise

    to = result.get("email")
    if not to:
        return  # user didn't ask for an email copy

    message = build_message(result, to)
    try:
        ses.send_email(
            FromEmailAddress=formataddr((SENDER_NAME, SENDER)),
            Destination={"ToAddresses": [to]},
            Content={"Raw": {"Data": message.as_bytes()}},
        )
        print(f"sent job {result.get('jobId')} ({result.get('status')})")
    except ClientError as exc:
        code = exc.response["Error"]["Code"]
        if code in PERMANENT_ERRORS:
            # Never log the address itself - just the job and the reason.
            print(f"SES rejected job {result.get('jobId')}: {code} - {exc.response['Error'].get('Message')}")
            return
        raise  # throttling etc. -> SNS/Lambda retries the invocation


def build_message(result: dict, to: str) -> MIMEMultipart:
    completed = result.get("status") == "completed"
    footer_text = (
        "You received this email because this address was entered on the CV editor website"
        + (f" ({WEBSITE_URL})" if WEBSITE_URL else "")
        + ". Your CV is not kept: it is deleted from our servers within 15 minutes."
    )

    msg = MIMEMultipart("mixed")
    msg["From"] = formataddr((SENDER_NAME, SENDER))
    msg["To"] = to

    if completed:
        msg["Subject"] = "Your improved CV is ready"
        cv = result.get("improvedCv", "")
        changes = result.get("changes") or []
        text = (
            "Here is your CV, edited by AI. It is also attached as improved-cv.md.\n\n"
            "Anything in [square brackets] is a placeholder: replace it with your real number before you send the CV.\n\n"
            + ("What the editor changed:\n" + "\n".join(f"- {c}" for c in changes) + "\n\n" if changes else "")
            + "-" * 60 + "\n\n" + cv + "\n\n" + "-" * 60 + "\n" + footer_text + "\n"
        )
        changes_html = "".join(f"<li>{html.escape(c)}</li>" for c in changes)
        body_html = f"""<!doctype html><html><body style="margin:0;background:#e4e9f0;padding:24px;font-family:Arial,Helvetica,sans-serif;color:#18202e">
<div style="max-width:680px;margin:0 auto">
  <p style="font-size:16px">Here is your CV, edited by AI. It is also attached as <b>improved-cv.md</b>.</p>
  <p style="font-size:14px;color:#586274">Anything in [square brackets] is a placeholder: replace it with your real number before you send the CV.</p>
  {f'<h3 style="color:#2350d4;font-size:16px">What the editor changed</h3><ul style="font-size:14px;line-height:1.5">{changes_html}</ul>' if changes else ''}
  <div style="background:#ffffff;padding:32px;margin-top:16px;white-space:pre-wrap;font-family:Georgia,serif;font-size:15px;line-height:1.55">{html.escape(cv)}</div>
  <p style="font-size:12px;color:#586274;margin-top:20px">{html.escape(footer_text)}</p>
</div></body></html>"""
    else:
        msg["Subject"] = "We couldn't improve your CV"
        reason = result.get("error") or "The AI editor couldn't process this CV."
        text = f"{reason}\n\nYou can try again on the website.\n\n{footer_text}\n"
        body_html = f"""<!doctype html><html><body style="font-family:Arial,Helvetica,sans-serif;color:#18202e;padding:24px">
<p>{html.escape(reason)}</p><p>You can try again on the website.</p>
<p style="font-size:12px;color:#586274">{html.escape(footer_text)}</p></body></html>"""

    alternative = MIMEMultipart("alternative")
    alternative.attach(MIMEText(text, "plain", "utf-8"))
    alternative.attach(MIMEText(body_html, "html", "utf-8"))
    msg.attach(alternative)

    if completed:
        attachment = MIMEText(result.get("improvedCv", ""), "markdown", "utf-8")
        attachment.add_header("Content-Disposition", "attachment", filename="improved-cv.md")
        msg.attach(attachment)
    return msg
