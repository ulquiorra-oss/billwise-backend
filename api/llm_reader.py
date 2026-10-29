"""
Reads a photographed bill with a Claude vision model (Anthropic API).

Why: dot-matrix prints on glare-washed phone photos (CEPALCO slips, COWD stubs) defeat Tesseract.
A vision model reads them like a person does.

Settings (environment variables, set on the Django SERVER only: never put the key in the app):
  ANTHROPIC_API_KEY     your key. Without it the reader is switched off.
  BILLWISE_LLM_MODE     off (default) | always | fallback
                          always:   photos go to Claude first; Tesseract only runs if Claude fails
                          fallback: Tesseract first; Claude only when the amount or due date is missing
  BILLWISE_LLM_MODEL    model ID. Default: "claude-sonnet-5-5", and if that is not found for your
                        account, "claude-haiku-5-5". Current IDs and prices are listed at
                        https://platform.claude.com/docs/en/models/overview
                        Haiku is far cheaper per scan; try it, and keep Sonnet if it misreads your bills.

Privacy: the photo is sent to Anthropic's API, and bills show the customer's name and address.
Tell your users, and check Anthropic's current data-handling terms.

Nothing here raises: any failure (no key, no network, bad reply) returns None and the caller falls
back to Tesseract.
"""

import base64
import io
import json
import logging
import math
import os
import re
from datetime import datetime

from PIL import Image, ImageOps

logger = logging.getLogger(__name__)

DEFAULT_MODELS = ('claude-sonnet-5-5', 'claude-haiku-5-5')  # tried in order until one is found
MAX_EDGE = 1600        # longest image side sent; small dot-matrix digits need the resolution
JPEG_QUALITY = 90      # keep compression light: artefacts turn 5s into 6s

SYSTEM_PROMPT = (
    "You read photographed Philippine utility bills and payment slips (electricity, water, telecom, "
    "rent and similar) and report ONLY what is printed.\n"
    "\n"
    "Rules:\n"
    "- Never guess and never calculate. If a character or number is not clearly legible, use null for "
    "that field. A missing value is far better than a wrong one.\n"
    "- The photo may show other papers (an old official receipt, another stub, fingers, a table). Use "
    "the bill the photo is mainly of and ignore every other document. \"Amount Paid\" on a receipt is "
    "a payment already made, never an amount payable.\n"
    "- Amounts are in Philippine pesos. Return plain numbers (7165.56, not \"7,165.56\" or "
    "\"P7,165.56\").\n"
    "- VAT or tax is never the amount payable. Report it in its own field so it cannot be confused.\n"
    "\n"
    "Reply with ONE JSON object and nothing else (no prose, no code fences), with exactly these keys:\n"
    "{\n"
    '  "merchant": the company that ISSUED the bill, e.g. "CEPALCO" or "Cagayan de Oro City Water '
    'District" (not the customer), or null,\n'
    '  "amount_payable": the total to pay now, printed as e.g. "TOTAL PAYABLE AMOUNT", "AMOUNT DUE '
    'UNTIL DUE DATE" or "Total Amount Due". It includes any arrears if the bill\'s total does. Number '
    'or null,\n'
    '  "current_charges": this month\'s charge ALONE, excluding arrears and any previous balance, e.g. '
    'the "AMOUNT (PESOS)" cell of a water bill\'s readings table. null if the bill does not show it '
    'separately,\n'
    '  "arrears": unpaid balance from earlier months if printed (e.g. "ARREARS"), else null,\n'
    '  "months_delinquent": whole number if printed, else null,\n'
    '  "disconnection_notice": true if the bill prints a disconnection warning (e.g. "FOR '
    'DISCONNECTION"), else false,\n'
    '  "due_date": the date the payable amount must be paid by, as YYYY-MM-DD, or null. If the bill '
    'prints only month and day (e.g. "9-23"), take the year from the billing month printed on the '
    'bill,\n'
    '  "amount_after_due_date": the higher amount payable after the due date if printed, else null,\n'
    '  "vat": the VAT/tax amount if printed, else null,\n'
    '  "legible": "clear" if you read everything confidently, "partly" if some fields were hard to '
    'read, "poor" if the photo is too blurred or dark to trust\n'
    "}"
)

USER_PROMPT = 'Read this bill and reply with the JSON object.'


# ------------------------------------------------------------
# Settings
# ------------------------------------------------------------

def mode():
    """'off', 'always' or 'fallback'. Anything else, or a missing key, means off."""
    value = (os.environ.get('BILLWISE_LLM_MODE') or 'off').strip().lower()
    if value not in ('always', 'fallback'):
        return 'off'
    if not os.environ.get('ANTHROPIC_API_KEY'):
        logger.warning('BILLWISE_LLM_MODE=%s but ANTHROPIC_API_KEY is not set; LLM reader is off.', value)
        return 'off'
    return value


def status():
    """One line for the server console: is Claude going to be used, and if not, exactly why."""
    import importlib.util
    raw_mode = (os.environ.get('BILLWISE_LLM_MODE') or 'off').strip().lower()
    if raw_mode not in ('always', 'fallback'):
        return ('Claude reader: OFF. Set BILLWISE_LLM_MODE=always (or fallback) and ANTHROPIC_API_KEY in the '
                'terminal that starts the server to turn it on.')
    if not os.environ.get('ANTHROPIC_API_KEY'):
        return f'Claude reader: OFF. BILLWISE_LLM_MODE={raw_mode} but ANTHROPIC_API_KEY is not set in this terminal.'
    if importlib.util.find_spec('anthropic') is None:
        return ("Claude reader: OFF. The 'anthropic' package is not installed in this Python. "
                "Run: pip install anthropic (inside the backend's virtual environment), then restart.")
    return f'Claude reader: ON, mode={raw_mode}, model={_model()}'


_working_model = None  # remembered after the first successful call


def _models():
    """The model IDs to try, in order. A model set in BILLWISE_LLM_MODEL is the only one used."""
    forced = os.environ.get('BILLWISE_LLM_MODEL')
    if forced:
        return [forced]
    return [_working_model] if _working_model else list(DEFAULT_MODELS)


def _model():
    return _models()[0]


def _is_model_not_found(exc):
    return getattr(exc, 'status_code', None) == 404 or type(exc).__name__ == 'NotFoundError'


def _client():
    """The Anthropic client. Imported lazily so the rest of the app runs without the package."""
    import anthropic
    return anthropic.Anthropic(timeout=45.0, max_retries=2)  # reads ANTHROPIC_API_KEY from the environment


# ------------------------------------------------------------
# Image and reply handling
# ------------------------------------------------------------

def _encode_image(image):
    """JPEG, upright, longest side <= MAX_EDGE, as base64."""
    image = ImageOps.exif_transpose(image)
    if image.mode != 'RGB':
        image = image.convert('RGB')
    if max(image.size) > MAX_EDGE:
        scale = MAX_EDGE / max(image.size)
        image = image.resize((int(image.width * scale), int(image.height * scale)), Image.LANCZOS)
    buf = io.BytesIO()
    image.save(buf, 'JPEG', quality=JPEG_QUALITY)
    return base64.standard_b64encode(buf.getvalue()).decode('ascii')


def _parse_json(text):
    """The JSON object in the model's reply, tolerating stray prose or code fences. None if absent."""
    if not text:
        return None
    cleaned = re.sub(r'^```(?:json)?|```$', '', text.strip(), flags=re.M).strip()
    start, end = cleaned.find('{'), cleaned.rfind('}')
    if start < 0 or end <= start:
        return None
    try:
        data = json.loads(cleaned[start:end + 1])
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def _number(value, maximum=10_000_000):
    """A positive, finite number from a JSON value (number or numeric string), else None."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, str):
        value = re.sub(r'[^\d.\-]', '', value.replace(',', ''))
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number) or number <= 0 or number > maximum:
        return None
    return round(number, 2)


def _iso_date(value):
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.strptime(value.strip()[:10], '%Y-%m-%d')
    except ValueError:
        return None
    return parsed.strftime('%Y-%m-%d') if 2000 <= parsed.year <= 2100 else None


# ------------------------------------------------------------
# The app's amount rules, applied to what the model read
# ------------------------------------------------------------

def interpret(raw):
    """
    Validate the model's JSON and decide the amount the app should record.

    - A bill with arrears records THIS MONTH'S charge: the model's current_charges, which must agree
      with  amount_payable - arrears  (so a single misread digit is caught, same check as the OCR path).
      If they disagree the amount is left empty and both readings come back as candidates.
    - Otherwise it records amount_payable.
    - An amount equal to the VAT figure is rejected: that is the classic mix-up on CEPALCO slips.
    """
    payable = _number(raw.get('amount_payable'))
    current = _number(raw.get('current_charges'))
    arrears = _number(raw.get('arrears'))
    vat = _number(raw.get('vat'))

    amount, candidates = None, []
    if arrears:
        derived = round(payable - arrears, 2) if payable and payable > arrears else None
        if current and derived:
            if abs(current - derived) <= 0.02:
                amount = current
            else:
                candidates = [current, derived]  # one of them was misread: let the user choose
        else:
            amount = current or derived
    else:
        amount = payable or current

    if amount is not None and vat is not None and abs(amount - vat) < 0.005 and payable != current:
        candidates, amount = [amount], None  # looks like the VAT, not the amount payable

    legible = raw.get('legible') if raw.get('legible') in ('clear', 'partly', 'poor') else 'partly'
    merchant = raw.get('merchant')
    merchant = merchant.strip() if isinstance(merchant, str) and merchant.strip() else None
    months = raw.get('months_delinquent')

    return {
        'amount': amount,
        'amount_candidates': candidates,
        'due_date': _iso_date(raw.get('due_date')),
        'merchant': merchant,
        'arrears': arrears,
        'months_delinquent': months if isinstance(months, int) and not isinstance(months, bool) and months >= 0 else None,
        'disconnection_notice': raw.get('disconnection_notice') is True,
        'amount_after_due_date': _number(raw.get('amount_after_due_date')),
        'vat': vat,
        'legible': legible,
    }


# ------------------------------------------------------------
# Public entry point
# ------------------------------------------------------------

def read_bill(image):
    """
    Read a PIL image with Claude. Returns the same dict shape scan_bill_image returns
    (amount, due_date, merchant, raw_text, method, confidence, ...) or None on any failure.

    If a model ID is "not found" (retired, or not available to your account) the next one in
    DEFAULT_MODELS is tried, so a model rename can't silently switch the reader off.
    """
    global _working_model
    try:
        client = _client()
        content = [  # the image goes first, then the instruction
            {'type': 'image', 'source': {'type': 'base64', 'media_type': 'image/jpeg',
                                         'data': _encode_image(image)}},
            {'type': 'text', 'text': USER_PROMPT},
        ]
    except Exception as exc:
        logger.warning('LLM reader failed (%s: %s); falling back to OCR.', type(exc).__name__, exc)
        return None

    for model in _models():
        try:
            response = client.messages.create(
                model=model,
                max_tokens=2000,  # headroom for models that think before answering
                system=SYSTEM_PROMPT,
                messages=[{'role': 'user', 'content': content}],
            )
            reply = ''.join(block.text for block in response.content if getattr(block, 'type', '') == 'text')
            raw = _parse_json(reply)
            if raw is None:
                logger.warning('LLM reader: reply was not a JSON object: %r', reply[:200])
                return None
            result = interpret(raw)
        except Exception as exc:  # no key, no network, API error...: never break scanning
            if _is_model_not_found(exc):
                logger.warning('LLM reader: model "%s" not found; trying the next one. (%s)', model, exc)
                continue
            logger.warning('LLM reader failed (%s: %s); falling back to OCR.', type(exc).__name__, exc)
            return None

        _working_model = model
        confidence = {'clear': 95.0, 'partly': 75.0, 'poor': 50.0}[result['legible']]
        return {
            **result,
            # biller detection reads this, so put the issuer's name in it
            'raw_text': result['merchant'] or '',
            'method': 'llm',
            'confidence': confidence,
        }

    logger.warning('LLM reader failed: none of the models %s was found. Set BILLWISE_LLM_MODEL to a current '
                   'ID from https://platform.claude.com/docs/en/models/overview; falling back to OCR.', _models())
    return None