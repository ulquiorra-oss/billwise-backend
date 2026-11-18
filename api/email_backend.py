"""
Django email backend that sends mail through a Google Apps Script web app (see Code.gs).

Why: Railway blocks outbound SMTP, and Apps Script sends over plain HTTPS. With this backend every
existing django.core.mail.send_mail(...) / EmailMessage call keeps working unchanged.

settings.py:
    EMAIL_BACKEND = 'api.email_backend.AppsScriptEmailBackend'      # 'api' = your app's folder name
    DEFAULT_FROM_EMAIL = 'BillWise <your-gmail@gmail.com>'          # informational, see note below

Environment variables (set them on Railway, and in your terminal for local runs). NEVER commit them:
    APPS_SCRIPT_MAIL_URL      the Web app URL ending in /exec
    APPS_SCRIPT_MAIL_SECRET   the same value as the MAIL_SECRET script property
    APPS_SCRIPT_MAIL_TIMEOUT  optional, seconds to wait (default 20)

Notes:
  - Mail is sent from the Google account that owns the script; the From address Django passes is
    ignored.
  - A normal Gmail account allows about 100 recipients per day through Apps Script. See Code.gs.
  - Uses only the standard library, so there is nothing new to install.
  - The message body (the OTP) and the secret are never written to the logs.
"""
import json
import logging
import os
import urllib.error
import urllib.request

from django.core.mail.backends.base import BaseEmailBackend

logger = logging.getLogger(__name__)


class AppsScriptError(Exception):
    """The Apps Script mailer could not send the message."""


class AppsScriptEmailBackend(BaseEmailBackend):
    def __init__(self, fail_silently=False, **kwargs):
        super().__init__(fail_silently=fail_silently, **kwargs)
        self.url = os.environ.get('APPS_SCRIPT_MAIL_URL', '').strip()
        self.secret = os.environ.get('APPS_SCRIPT_MAIL_SECRET', '').strip()
        try:
            self.timeout = float(os.environ.get('APPS_SCRIPT_MAIL_TIMEOUT', '20'))
        except ValueError:
            self.timeout = 20.0

    def send_messages(self, email_messages):
        """Send each message to each recipient. Returns how many messages were sent."""
        sent = 0
        for message in email_messages or []:
            try:
                for recipient in message.recipients():
                    self._send_one(message, recipient)
                sent += 1
            except Exception as exc:
                if not self.fail_silently:
                    raise
                logger.error('Email not sent (%s: %s)', type(exc).__name__, exc)
        return sent

    def _send_one(self, message, recipient):
        if not self.url or not self.secret:
            raise AppsScriptError(
                'APPS_SCRIPT_MAIL_URL and APPS_SCRIPT_MAIL_SECRET must both be set in the environment.')

        payload = {
            'secret': self.secret,
            'to': recipient,
            'subject': message.subject,
            'body': message.body,
        }
        for content, mimetype in getattr(message, 'alternatives', None) or []:
            if mimetype == 'text/html':
                payload['html'] = content
        if getattr(message, 'reply_to', None):
            payload['reply_to'] = message.reply_to[0]

        # Apps Script answers a POST with a redirect to the page holding the result;
        # urllib follows it (and re-requests it with GET), which is exactly what Apps Script expects.
        request = urllib.request.Request(
            self.url,
            data=json.dumps(payload).encode('utf-8'),
            headers={'Content-Type': 'application/json'},
            method='POST',
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                raw = response.read().decode('utf-8', errors='replace')
        except urllib.error.HTTPError as exc:
            raise AppsScriptError(f'the mailer returned HTTP {exc.code}') from None
        except urllib.error.URLError as exc:
            raise AppsScriptError(f'could not reach the mailer ({exc.reason})') from None

        try:
            result = json.loads(raw)
        except ValueError:
            # Google's sign-in page comes back as HTML when the deployment isn't open to "Anyone"
            raise AppsScriptError(
                'the mailer did not return JSON. Check the web app is deployed with "Who has access: '
                'Anyone", the URL ends in /exec, and you deployed a NEW version after editing the script.'
            ) from None

        if not result.get('ok'):
            reasons = {
                'unauthorized': 'the secret does not match the MAIL_SECRET script property',
                'quota_exhausted': "today's Apps Script email quota is used up",
                'bad_recipient': 'the recipient address was rejected',
                'empty_message': 'the subject or body was empty',
            }
            error = str(result.get('error', 'unknown error'))
            raise AppsScriptError(reasons.get(error, error))