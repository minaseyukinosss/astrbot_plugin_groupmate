"""SMTP send helpers using the Python standard library."""

from __future__ import annotations

import smtplib
from email.header import Header
from email.mime.text import MIMEText
from email.utils import formataddr, formatdate, make_msgid


def qq_mailbox(qq: str) -> str:
    cleaned = str(qq or "").strip()
    if not cleaned.isdigit():
        raise ValueError("qq must be digits")
    return "{}@qq.com".format(cleaned)


def format_mailbox(display_name: str, address: str) -> str:
    """Build an RFC2047-safe ``Name <addr>`` header value for QQ SMTP."""

    addr = str(address or "").strip()
    if not addr or "@" not in addr:
        raise ValueError("mailbox address is required")
    name = str(display_name or "").strip()
    if not name or name == addr or name.isdigit():
        return addr
    # Header.encode() avoids QQ "From header is missing or invalid" / mojibake.
    return formataddr((Header(name, "utf-8").encode(), addr))


def normalize_mail_body(body: str) -> str:
    """Normalize newlines for a readable plain-text mail body."""

    text = str(body or "").replace("\r\n", "\n").replace("\r", "\n")
    lines = [line.rstrip() for line in text.split("\n")]
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    cleaned = []
    blank_pending = False
    for line in lines:
        if not line.strip():
            if cleaned and not blank_pending:
                cleaned.append("")
                blank_pending = True
            continue
        cleaned.append(line.strip() if len(line.strip()) < 80 else line.rstrip())
        blank_pending = False
    return "\n".join(cleaned).strip()


def build_message(
    *,
    from_address: str,
    to_address: str,
    subject: str,
    body: str,
    display_name: str = "",
    to_display_name: str = "",
) -> MIMEText:
    payload = normalize_mail_body(body)
    if not payload:
        payload = "（空）"
    message = MIMEText(payload, "plain", "utf-8")
    message["From"] = format_mailbox(display_name, from_address)
    message["To"] = format_mailbox(to_display_name, to_address)
    message["Subject"] = Header(str(subject or "(无主题)").strip() or "(无主题)", "utf-8").encode()
    message["Date"] = formatdate(localtime=True)
    message["Message-ID"] = make_msgid(domain=from_address.split("@", 1)[-1])
    message["MIME-Version"] = "1.0"
    return message


def send_smtp(
    *,
    host: str,
    port: int,
    use_ssl: bool,
    username: str,
    password: str,
    from_address: str,
    to_address: str,
    subject: str,
    body: str,
    display_name: str = "",
    to_display_name: str = "",
    timeout: float = 30.0,
) -> None:
    message = build_message(
        from_address=from_address,
        to_address=to_address,
        subject=subject,
        body=body,
        display_name=display_name,
        to_display_name=to_display_name,
    )
    payload = message.as_string()
    if use_ssl:
        with smtplib.SMTP_SSL(host, int(port), timeout=timeout) as server:
            server.login(username, password)
            server.sendmail(from_address, [to_address], payload)
        return
    with smtplib.SMTP(host, int(port), timeout=timeout) as server:
        server.ehlo()
        server.starttls()
        server.ehlo()
        server.login(username, password)
        server.sendmail(from_address, [to_address], payload)
