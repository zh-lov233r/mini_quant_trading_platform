"""One SMTP/TLS transport; ambiguous acceptance is never blindly retried."""
from datetime import datetime, timezone
from email.message import EmailMessage
import os
import smtplib
import ssl
from sqlalchemy import select
from src.models.tables import SignalReportDelivery
from src.services.signal_report_rendering import render, report_link


def validate_smtp(recipients):
    if not recipients:raise ValueError("at least one recipient is required")
    if os.getenv("SIGNAL_REPORT_DELIVERY_ENABLED","false").lower()!="true":raise ValueError("report email delivery is disabled")
    for key in ("SIGNAL_SMTP_HOST","SIGNAL_SMTP_FROM"):
        if not os.getenv(key):raise ValueError(f"{key} is not configured")
    if any(c in os.environ["SIGNAL_SMTP_FROM"] for c in "\r\n"):raise ValueError("invalid sender")


def enqueue_deliveries(db,report,recipients):
    validate_smtp(recipients)
    if report.status!="completed" or report.expired_at:raise ValueError("report is not available")
    for recipient in recipients:
        if not db.scalar(select(SignalReportDelivery.id).where(SignalReportDelivery.report_id==report.id,
                                                              SignalReportDelivery.recipient==recipient)):
            db.add(SignalReportDelivery(report_id=report.id,recipient=recipient))
    db.flush()


def send_delivery(db,delivery,report):
    validate_smtp([delivery.recipient])
    if report.expired_at or not report.document:raise ValueError("report_expired")
    message=EmailMessage()
    message["From"]=os.environ["SIGNAL_SMTP_FROM"];message["To"]=delivery.recipient
    message["Subject"]=f"[{report.summary['status']}] {report.summary['name']} · {report.summary['session_date']}"
    message["Message-ID"]=f"<signal-{delivery.id}@signal-center.local>"
    message.set_content(report_link(report.document))
    message.add_alternative(render(report.document,"html",summary_only=True).decode(),subtype="html")
    client=None;data_started=False
    try:
        client=smtplib.SMTP(os.environ["SIGNAL_SMTP_HOST"],int(os.getenv("SIGNAL_SMTP_PORT","587")),timeout=30)
        client.ehlo();client.starttls(context=ssl.create_default_context());client.ehlo()
        if os.getenv("SIGNAL_SMTP_USER"):client.login(os.environ["SIGNAL_SMTP_USER"],os.environ.get("SIGNAL_SMTP_PASSWORD",""))
        code,response=client.mail(message["From"])
        if code!=250:raise smtplib.SMTPSenderRefused(code,response,message["From"])
        code,response=client.rcpt(delivery.recipient)
        if code not in (250,251):raise smtplib.SMTPRecipientsRefused({delivery.recipient:(code,response)})
        # Persist uncertainty BEFORE the server can accept message DATA.
        delivery.status="sending";db.commit();data_started=True
        code,response=client.data(message.as_bytes())
        if code!=250:raise smtplib.SMTPDataError(code,response)
        delivery.status="sent";delivery.error=None
    except smtplib.SMTPResponseException as exc:
        delivery.status="queued" if 400<=exc.smtp_code<500 and delivery.attempts<3 else "failed"
        delivery.error=f"SMTP {exc.smtp_code}"
    except (OSError,smtplib.SMTPServerDisconnected) as exc:
        delivery.status="unknown" if data_started else "queued" if delivery.attempts<3 else "failed"
        delivery.error=type(exc).__name__
    except smtplib.SMTPRecipientsRefused:
        delivery.status="failed";delivery.error="recipient_refused"
    finally:
        if client is not None:client.close()
    delivery.lease_expires_at=None
    delivery.finished_at=datetime.now(timezone.utc)
