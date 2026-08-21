"""Delivery by email, over any ordinary mail server.

Sends the portal link by default rather than the files themselves, for the
reasons set out in delivery_portal: attachment limits, deliverability, and the
download telemetry a link produces. Attaching is available with
`attach=True` for the publisher who would rather their buyer never click a
link, and it warns when a batch is large enough that a mail server is likely to
refuse it.

Nothing here is Gmail-specific or Microsoft-specific. Both of those will accept
an app password over ordinary SMTP, which avoids an OAuth consent screen, a
verification review, and a token refresh path -- the same trade the Drive
connector makes for the same reasons. A dedicated Gmail API adapter can come
later if sending as a shared mailbox turns out to matter.

CREDENTIALS
    An app password is still a password. It belongs in an environment variable
    or a credential store, not on a command line where it lands in shell
    history, and not in the repository.
"""

import mimetypes
import smtplib
import ssl
from datetime import datetime, timezone
from email.message import EmailMessage
from pathlib import Path

from .base import BUILT, ConnectorError, DeliveryChannel, NotConfigured, register

# Most providers reject a message over ~25 MB, and several are stricter once
# base64 encoding inflates it by a third.
ATTACH_WARN_BYTES = 18 * 1024 * 1024

DEFAULT_SUBJECT = "Your sheet music from {publisher}"

DEFAULT_BODY = """Thank you for your order.

{files_line}

{link_line}
Each file is stamped with the licensee's name and locked against editing. It
opens and prints with no password.

Licensed to: {licensee}
Order: {order_ref}

-- {publisher}
"""


def _now_iso():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


@register
class SMTPDelivery(DeliveryChannel):
    name = "smtp"
    label = "Email (SMTP)"
    description = ("Sends through any mail server with a username and "
                   "password. Sends the portal link by default.")
    state = BUILT

    def __init__(self, host=None, port=587, username=None, password=None,
                 sender=None, publisher="", use_tls=True, attach=False,
                 timeout=30):
        self.host = host
        self.port = int(port)
        self.username = username
        self.password = password
        self.sender = sender
        self.publisher = publisher or "the publisher"
        self.use_tls = bool(use_tls)
        self.attach = bool(attach)
        self.timeout = int(timeout)

    def configure(self, host=None, port=None, username=None, password=None,
                  sender=None, publisher=None, use_tls=None, attach=None,
                  timeout=None, **_ignored):
        for key, val in (("host", host), ("username", username),
                         ("password", password), ("sender", sender)):
            if val:
                setattr(self, key, val)
        if publisher:
            self.publisher = publisher
        if port is not None:
            self.port = int(port)
        if use_tls is not None:
            self.use_tls = bool(use_tls)
        if attach is not None:
            self.attach = bool(attach)
        if timeout is not None:
            self.timeout = int(timeout)
        return self

    def health(self):
        if not self.host:
            return False, "No SMTP server set."
        if not self.from_address():
            return False, "No sender address set."
        try:
            with self._connect() as smtp:
                smtp.noop()
        except ConnectorError as exc:
            return False, str(exc)
        except Exception as exc:
            return False, "{}: {}".format(type(exc).__name__, exc)
        return True, "Signed in to {}:{} as {}".format(
            self.host, self.port, self.from_address())

    def from_address(self):
        return self.sender or self.username

    # -- transport ----------------------------------------------------------

    def _connect(self):
        if not self.host:
            raise NotConfigured("No SMTP server set.")
        try:
            if self.port == 465:
                smtp = smtplib.SMTP_SSL(self.host, self.port,
                                        timeout=self.timeout,
                                        context=ssl.create_default_context())
            else:
                smtp = smtplib.SMTP(self.host, self.port, timeout=self.timeout)
                if self.use_tls:
                    smtp.ehlo()
                    if smtp.has_extn("starttls"):
                        smtp.starttls(context=ssl.create_default_context())
                        smtp.ehlo()
        except (OSError, smtplib.SMTPException) as exc:
            raise ConnectorError("Could not reach {}:{} -- {}".format(
                self.host, self.port, exc))

        if self.username and self.password:
            try:
                smtp.login(self.username, self.password)
            except smtplib.SMTPAuthenticationError as exc:
                smtp.close()
                raise ConnectorError(
                    "The mail server rejected those credentials. For Gmail or "
                    "Microsoft 365 this usually means an app password is "
                    "required rather than the account password. ({})".format(exc))
            except smtplib.SMTPException as exc:
                smtp.close()
                raise ConnectorError("Sign-in failed: {}".format(exc))
        return smtp

    # -- message ------------------------------------------------------------

    def build_message(self, order, files, receipt=None, subject=None, body=None):
        """Compose the message. Split out so it can be tested without sending."""
        to = (order.get("email") or "").strip()
        if not to:
            raise ConnectorError(
                "No email address on order {}, so there is nowhere to send it."
                .format(order.get("order_ref") or "(no ref)"))

        files = [Path(f) for f in files]
        url = (receipt or {}).get("url", "")
        expires = str((receipt or {}).get("expires_at", ""))[:10]

        if url:
            link_line = "Download your files here:\n{}\n".format(url)
            if expires:
                link_line += "\nThis link expires on {}.\n".format(expires)
            files_line = "Your order contains {} file(s).".format(len(files))
        else:
            link_line = ""
            files_line = "Your files are attached ({}).".format(len(files))

        msg = EmailMessage()
        msg["Subject"] = (subject or DEFAULT_SUBJECT).format(
            publisher=self.publisher)
        msg["From"] = self.from_address()
        msg["To"] = to
        msg.set_content((body or DEFAULT_BODY).format(
            publisher=self.publisher,
            licensee=order.get("buyer", ""),
            order_ref=order.get("order_ref", ""),
            files_line=files_line,
            link_line=link_line))

        if self.attach:
            total = 0
            for f in files:
                if not f.is_file():
                    raise ConnectorError("Cannot attach missing file: {}".format(f))
                data = f.read_bytes()
                total += len(data)
                ctype, _ = mimetypes.guess_type(f.name)
                maintype, _, subtype = (ctype or "application/pdf").partition("/")
                msg.add_attachment(data, maintype=maintype, subtype=subtype,
                                   filename=f.name)
            if total > ATTACH_WARN_BYTES:
                raise ConnectorError(
                    "These {} file(s) come to {:.1f} MB, which most mail "
                    "servers will refuse. Deliver by link instead."
                    .format(len(files), total / 1e6))
        return msg

    # -- contract -----------------------------------------------------------

    def deliver(self, order, files, receipt=None, dry_run=False):
        """Send one order. `receipt` is the portal receipt, when there is one."""
        msg = self.build_message(order, files, receipt=receipt)

        if dry_run:
            return {"channel": self.name, "sent_at": _now_iso(),
                    "detail": "dry run -- not sent",
                    "to": msg["To"], "subject": msg["Subject"]}

        try:
            with self._connect() as smtp:
                smtp.send_message(msg)
        except ConnectorError:
            raise
        except smtplib.SMTPRecipientsRefused:
            raise ConnectorError("The mail server refused {}.".format(msg["To"]))
        except smtplib.SMTPException as exc:
            raise ConnectorError("Send failed: {}".format(exc))

        return {
            "channel": self.name,
            "sent_at": _now_iso(),
            "detail": "emailed to {}".format(msg["To"]),
            "to": msg["To"],
            "subject": msg["Subject"],
        }
