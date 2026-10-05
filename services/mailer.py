"""Outbound email: SMTP (Railway Pro+) with Resend HTTPS API fallback.

Backend selection (EMAIL_BACKEND):
  auto   -> SMTP when SMTP_HOST is set, otherwise Resend
  smtp   -> always SMTP
  resend -> always Resend
"""
import smtplib
import ssl
from email.message import EmailMessage

import requests
from flask import current_app


def _cfg(key, default=None):
    return current_app.config.get(key, default)


def selected_backend():
    backend = (_cfg('EMAIL_BACKEND', 'auto') or 'auto').strip().lower()
    if backend == 'auto':
        return 'smtp' if _cfg('SMTP_HOST') else 'resend'
    return backend


def _smtp_security(port):
    sec = (_cfg('SMTP_SECURITY', '') or '').strip().lower()
    if sec in ('starttls', 'ssl', 'none'):
        return sec
    return 'ssl' if int(port) == 465 else 'starttls'


def _send_smtp(to, subject, body):
    host = _cfg('SMTP_HOST')
    port = int(_cfg('SMTP_PORT', 587) or 587)
    user = _cfg('SMTP_USERNAME')
    password = _cfg('SMTP_PASSWORD')
    sender = _cfg('SMTP_FROM_EMAIL') or user
    timeout = int(_cfg('SMTP_TIMEOUT', 10) or 10)
    security = _smtp_security(port)
    if not sender:
        raise ValueError('SMTP_FROM_EMAIL (or SMTP_USERNAME) is not set')

    msg = EmailMessage()
    msg['From'] = sender
    msg['To'] = to
    msg['Subject'] = subject
    msg.set_content(body)

    ctx = ssl.create_default_context()
    if security == 'ssl':
        server = smtplib.SMTP_SSL(host, port, timeout=timeout, context=ctx)
    else:
        server = smtplib.SMTP(host, port, timeout=timeout)
    with server:
        server.ehlo()
        if security == 'starttls':
            server.starttls(context=ctx)
            server.ehlo()
        if user and password:
            server.login(user, password)
        server.send_message(msg)


def _send_resend(to, subject, body):
    response = requests.post(
        _cfg('RESEND_API_URL'),
        headers={
            'Authorization': f"Bearer {_cfg('RESEND_API_KEY')}",
            'Content-Type': 'application/json',
        },
        json={
            'from': _cfg('RESEND_FROM_EMAIL'),
            'to': [to],
            'subject': subject,
            'text': body,
        },
        timeout=10,
    )
    if not response.ok:
        current_app.logger.error(
            'Resend API rejected email to %s: HTTP %s - %s',
            to, response.status_code, response.text,
        )
        return False
    return True


def send_email(to, subject, body):
    """Send a plain-text email. Returns True on success, never raises."""
    try:
        if _cfg('RESEND_SUPPRESS_SEND'):
            current_app.logger.info('Email suppressed for %s', to)
            return True
        backend = selected_backend()
        if backend == 'smtp':
            _send_smtp(to, subject, body)
        elif backend == 'resend':
            if not _send_resend(to, subject, body):
                return False
        else:
            current_app.logger.error('Unknown EMAIL_BACKEND %r', backend)
            return False
        current_app.logger.info('Email sent to %s via %s', to, backend)
        return True
    except Exception:
        current_app.logger.exception('Failed to send email to %s', to)
        return False
