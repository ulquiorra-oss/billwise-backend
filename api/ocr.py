"""
OCR Bill Scanning Pipeline
BillWise - Rule-Based Household Bill Prioritization and Financial Risk Assessment System

Supports:
  - Images (JPEG, PNG, WEBP, HEIC, BMP, TIFF)
  - PDFs. Digital PDFs have their text read directly (near-100% accurate);
    scanned PDFs are rendered with PyMuPDF and OCR'd (no Poppler needed).

Public interface: scan_bill_image(file_obj, is_pdf=False) returns a dict with
amount, due_date, merchant, raw_text, method, confidence.

How photos are read
  1. Three views of the photo (darkest colour channel, blue channel, grey), each LOCALLY
     binarized. Local thresholding matters most: a dark background, shadow or finger in the
     frame makes Tesseract's own global threshold drop the text elsewhere in the picture.
  2. Two OCR passes first; four more only if those weren't conclusive. Passes run in parallel.
  3. The passes vote. On a bill with arrears, the amount is this month's charge, found as the
     combination of readings that satisfies  (amount due until due date) - arrears = this
     month's charge, so a single misread digit can't slip through.
  4. If the bill's label is there but no pass can read its number, the amount is left empty
     (the app then opens the edit form) rather than guessing.

Amount rules (single text, used for digital PDFs), in order:
  1. Bills that show ARREARS: this month's charge = (amount due until due date) - arrears,
     cross-checked against the AMOUNT cell of the readings table.
  2. Otherwise the labelled total ("Total Amount Due", "Amount Due Until Due Date", ...).
  3. Last resort: the largest plausible amount not on a subtotal/VAT/penalty/paid line.

Set BILLWISE_OCR_DEBUG_DIR=/some/folder to save what Tesseract sees on every pass.
"""

import io
import os
import re
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from difflib import SequenceMatcher

from PIL import Image, ImageChops, ImageFilter, ImageOps
import pytesseract

try:
    import pymupdf as fitz  # PyMuPDF: reads PDF text and renders pages, no Poppler needed
except ImportError:  # pragma: no cover
    fitz = None

try:  # optional: much better binarization for photos with shadows / uneven light
    import cv2
    import numpy as np
except ImportError:  # pragma: no cover
    cv2 = None
    np = None

try:  # optional: robust date parsing fallback
    from dateutil import parser as date_parser
except ImportError:  # pragma: no cover
    date_parser = None

DEBUG_DIR = os.environ.get('BILLWISE_OCR_DEBUG_DIR')

# We run several Tesseract processes in parallel; without this each one also spawns its own
# threads and they fight over the CPU.
os.environ.setdefault('OMP_THREAD_LIMIT', '1')


# ---- Tesseract path (Windows) ----
if os.name == 'nt':
    possible_paths = [
        r'C:\Program Files\Tesseract-OCR\tesseract.exe',
        r'C:\Program Files (x86)\Tesseract-OCR\tesseract.exe',
        r'C:\Users\jeeya\AppData\Local\Programs\Tesseract-OCR\tesseract.exe',
    ]
    for path in possible_paths:
        if os.path.exists(path):
            pytesseract.pytesseract.tesseract_cmd = path
            break


# ============================================================
# IMAGE PREPROCESSING
# ============================================================

TARGET_WIDTH = 2200
MAX_WIDTH = 4096


def _ocr(image, psm):
    config = f'--oem 3 --psm {psm} -c preserve_interword_spaces=1'
    data = pytesseract.image_to_data(
        image, lang='eng', config=config, output_type=pytesseract.Output.DICT
    )

    lines = {}
    confidences = []
    for i, word in enumerate(data['text']):
        word = word.strip()
        if not word:
            continue
        try:
            conf = float(data['conf'][i])
        except (TypeError, ValueError):
            conf = -1
        key = (data['block_num'][i], data['par_num'][i], data['line_num'][i])
        lines.setdefault(key, []).append(word)
        if conf >= 0:
            confidences.append(conf)

    text = '\n'.join(' '.join(words) for _, words in sorted(lines.items()))
    mean_conf = sum(confidences) / len(confidences) if confidences else 0.0
    return text, mean_conf


# ============================================================
# TEXT HELPERS
# ============================================================

def _lines(text):
    return [_fix_digits(ln.strip()) for ln in text.replace('\u00a0', ' ').splitlines() if ln.strip()]


# OCR often reads 0 as O/o and 1 as I/l inside numbers like "1,2O0.OO"
_FIXABLE_NUMBER = re.compile(r'(?<![A-Za-z0-9])[0-9OoIl,]*\d[0-9OoIl,]*\.[0-9OoIl]{2}(?![A-Za-z0-9])')
_DIGIT_FIX = str.maketrans('OoIl', '0011')


def _fix_digits(line):
    return _FIXABLE_NUMBER.sub(lambda m: m.group(0).translate(_DIGIT_FIX), line)


# ============================================================
# AMOUNT EXTRACTION
# ============================================================

# Strict: 1,234.56 | 1234.56 | 12.00   (always two decimals; skips % and negatives)
AMOUNT_STRICT_RE = re.compile(r'(?<![\d.,\-])((?:\d{1,3}(?:,\d{3})+|\d+)\.\d{2})(?![\d%])')

# Loose: also accepts the spaces OCR sometimes inserts ("6, 255.86", "506. 71").
# Only used on lines that carry an amount label, because on other lines it can glue two
# separate numbers together ("Qty 2, 100.00" -> 2100.00, "Sep 23, 2026. 15 days" -> 2026.15).
AMOUNT_LOOSE_RE = re.compile(r'(?<![\d.,\-])((?:\d{1,3}(?:,\s?\d{3})+|\d+)\.\s?\d{2})(?![\d%])')

# (priority, pattern). Higher priority wins.
AMOUNT_KEYWORDS = [
    # Highest priority: "amount due until due date" is what you actually pay.
    (110, re.compile(r'amount\s+due\s+until\s+due\s+date', re.I)),
    (108, re.compile(r'amount\s+due\s+(?:before|on)\s+due\s+date', re.I)),
    (105, re.compile(r'(?:total\s+)?amount\s+due\s+(?:until|before|on)\b', re.I)),

    # Standard labelled totals.
    (100, re.compile(r'total\s+amount\s+due|amount\s+due|total\s+due|net\s+amount\s+due', re.I)),
    (95, re.compile(r'(?:please\s+)?pay\s+(?:this\s+)?amount|amount\s+payable|balance\s+due|amount\s+to\s+pay', re.I)),

    # Fuzzy matches that tolerate common OCR mistakes in "total bill amount".
    (90, re.compile(r'total\s+payable\s+amount|total\s+payable', re.I)),
    (88, re.compile(r'total\s+b\w{0,3}l+\s+amou?n\w?', re.I)),
    (85, re.compile(r'total\s+(?:bill|bri?ll|bi11|bil1)\s+amount?', re.I)),

    # Lower-priority fallbacks.
    (80, re.compile(r'grand\s+total|total\s+amount|total\s+payable', re.I)),
    (60, re.compile(r'current\s+(?:charges|bill)|bill\s+amount', re.I)),
    (50, re.compile(r'\btotal\b', re.I)),
    (45, re.compile(r'\bamount\b', re.I)),
]

# Lines about these are NOT the amount to pay (unless a strong keyword is also on the line)
AMOUNT_NEGATIVE = re.compile(
    r'\b(previous|prev|subtotal|sub-total|vat|tax|discount|change|cash|tendered|late|penalt\w*|'
    r'surcharge|reading|kwh|rate|arrears|deposit|adjustment|credit|after|paid|received)\b',
    re.I,
)

# Never the amount to pay now, even if the line also says "amount due"
# (e.g. "Amount due after due date", "Late payment charge")
AMOUNT_HARD_NEGATIVE = re.compile(r'\b(after|late|penalt\w*|surcharge|overdue)\b', re.I)

STRONG_AMOUNT_SCORE = 85

# Something that looks like an amount but has junk stuck to it, e.g. "?,165.56"
GARBLED_AMOUNT = re.compile(r'[^\s\d]*\d[\d,]*\.\d{2}(?!\d)')

# A fragment smaller than this is never the total in the last-resort fallback.
MIN_PLAUSIBLE_TOTAL = 100.0


def _amounts_in(line, loose=False):
    regex = AMOUNT_LOOSE_RE if loose else AMOUNT_STRICT_RE
    results = []
    for m in regex.finditer(line):
        raw = m.group(1).replace(',', '').replace(' ', '')
        try:
            results.append((m.start(), float(raw)))
        except ValueError:
            continue
    return results


def _best_keyword(low_line):
    best = (0, 0)
    for score, pattern in AMOUNT_KEYWORDS:
        match = pattern.search(low_line)
        if match and score > best[0]:
            best = (score, match.end())
    return best


def _labelled_amount(lines):
    """
    The amount next to the strongest label (same line, else the next two lines).
    Returns ((score, value) or None, unreadable_score). unreadable_score is > 0 when a strong
    label was found whose number was too garbled to read.
    """
    best = None
    unreadable_score = 0

    for i, line in enumerate(lines):
        low = line.lower()
        score, kw_end = _best_keyword(low)
        if score == 0 or AMOUNT_HARD_NEGATIVE.search(low):
            continue
        if score < STRONG_AMOUNT_SCORE and AMOUNT_NEGATIVE.search(low):
            continue

        value = None
        same_line = [(pos, v) for pos, v in _amounts_in(line, loose=True) if v > 0]
        after_label = [v for pos, v in same_line if pos >= kw_end]
        if after_label:
            value = after_label[0]
        elif same_line:
            value = same_line[-1][1]
        else:
            if score >= STRONG_AMOUNT_SCORE and GARBLED_AMOUNT.search(line[kw_end:]):
                unreadable_score = max(unreadable_score, score)
            for nxt in lines[i + 1:i + 3]:
                low_next = nxt.lower()
                # stop at another label, or at a column heading like "AFTER DUE DATE"
                if _best_keyword(low_next)[0] or AMOUNT_HARD_NEGATIVE.search(low_next):
                    break
                found = [v for _, v in _amounts_in(nxt, loose=True) if v > 0]
                if found:
                    value = found[0]
                    break

        if value is None:
            continue
        if best is None or score > best[0] or (score == best[0] and value > best[1]):
            best = (score, value)

    return best, unreadable_score


# ------------------------------------------------------------
# Bills with arrears (utility bills): this month's charge
# ------------------------------------------------------------

# OCR-tolerant: "ARREARS", "APREARS", "ARREAR"
ARREARS_RE = re.compile(r'\ba[pr]{1,2}ea?rs?\b', re.I)

# Accepts a comma used as the decimal point ("255,86"), which OCR produces on some bills
_MONEY_LOOSE = re.compile(r'(?<![\d.,])(\d{1,3}(?:,\d{3})*|\d+)[.,](\d{2})(?!\d)')


def _arrears_amount(lines):
    for line in lines:
        m = ARREARS_RE.search(line.lower())
        if not m:
            continue
        found = _MONEY_LOOSE.findall(line[m.end():])
        if found:
            whole, cents = found[-1]
            value = float(f"{whole.replace(',', '')}.{cents}")
            if value > 0:
                return value
    return None


# The readings table's header row, e.g. "READINGS | Cubic Meter | AMOUNT (PESOS)"
CURRENT_ROW_HEADER_RE = re.compile(r'\b(?:readings?|cubic\s*meters?|consumed)\b', re.I)

CURRENT_ROW_EXCLUDE_RE = re.compile(
    r'\b(?:arrears?|previous|prev|total|amount\s+due|due\s+date|'
    r'delinquent|penalt\w*|surcharge|adjustment|deposit|'
    r'unpaid|balance\s+forward|month\s+of)\b',
    re.I,
)


def extract_current_charge(text):
    """
    The AMOUNT cell of a utility bill's readings table (this month's charge), or None.

    Finds a "readings / cubic meter / consumed" header, then takes the first amount on the
    next 1-3 lines that aren't arrears/total/penalty lines.
    """
    lines = _lines(text)

    for i, line in enumerate(lines):
        low = line.lower()
        if not CURRENT_ROW_HEADER_RE.search(low) or CURRENT_ROW_EXCLUDE_RE.search(low):
            continue

        for j in range(i + 1, min(i + 4, len(lines))):
            if CURRENT_ROW_EXCLUDE_RE.search(lines[j].lower()):
                continue
            values = [v for _, v in _amounts_in(lines[j]) if v > 0]
            if values:
                return values[0]  # leftmost: the table is the left block of the bill

    return None


def amount_parts(text):
    """The raw ingredients of the amount from ONE OCR text (used to vote across passes)."""
    lines = _lines(text)
    labelled, unreadable_score = _labelled_amount(lines)
    return {
        'labelled': labelled[1] if labelled else None,
        'label_score': labelled[0] if labelled else 0,
        'unreadable': unreadable_score,
        'arrears': _arrears_amount(lines),
        'table': extract_current_charge(text),
    }


def _consistent_current_charge(parts_list):
    """
    Bills with arrears print three numbers that must satisfy  total - arrears = this month.
    Every OCR pass reads some of them right and some wrong, so look across ALL passes for a
    combination that agrees. A misread digit almost never produces a combination that adds up.
    Returns (value, support, confirmed) or None. `confirmed` means the readings-table cell agrees.
    """
    totals = Counter(p['labelled'] for p in parts_list if p['labelled'])
    arrears = Counter(p['arrears'] for p in parts_list if p['arrears'])
    tables = Counter(p['table'] for p in parts_list if p['table'])
    if not arrears:
        return None

    best = None  # (support, value)
    for total, t_count in totals.items():
        for arr, a_count in arrears.items():
            if total <= arr:
                continue
            derived = round(total - arr, 2)
            t_support = sum(c for v, c in tables.items() if abs(v - derived) < 0.01)
            support = t_count + a_count + 2 * t_support
            # a derived value confirmed by the readings-table cell is strong evidence
            if t_support and (best is None or support > best[0]):
                best = (support, derived)
    if best:
        return best[1], best[0], True  # confirmed by the readings-table cell

    # No table cell agrees. Fall back to total - arrears from the most common readings.
    if totals and arrears:
        total = totals.most_common(1)[0][0]
        arr = arrears.most_common(1)[0][0]
        if total > arr:
            return round(total - arr, 2), 1, False
    # Only the table cell, if every pass agrees on it.
    if tables and len(tables) == 1:
        return next(iter(tables)), 1, False
    return None


def decide_amount(parts_list, with_confirmation=False):
    """
    One amount from the per-pass parts (see amount_parts); None when the passes can't agree.
    With with_confirmation=True returns (amount, confirmed): confirmed is False for a bill with
    arrears whose three numbers haven't been cross-checked yet (so more passes are worthwhile).
    """
    def out(value, confirmed=True):
        return (value, confirmed) if with_confirmation else value

    has_arrears = any(p['arrears'] for p in parts_list)
    consistent = _consistent_current_charge(parts_list)
    if consistent:
        return out(consistent[0], consistent[2])
    if has_arrears:
        return out(None, False)

    # No arrears on this bill: most common labelled total, strongest label first.
    labelled = [(p['label_score'], p['labelled']) for p in parts_list if p['labelled']]
    if labelled:
        votes = Counter(v for _, v in labelled)
        top_votes = max(votes.values())
        candidates = [v for v, c in votes.items() if c == top_votes]
        # ties: prefer the value found under the strongest label
        return out(max(candidates, key=lambda v: max(sc for sc, val in labelled if val == v)))

    return out(None, False)  # nothing readable (or the bill's own label was there but garbled)


def extract_amount(text):
    lines = _lines(text)
    labelled, unreadable_score = _labelled_amount(lines)

    # 1. Bills with arrears: this month's charge, double-checked two ways.
    arrears = _arrears_amount(lines)
    if arrears:
        total = labelled[1] if labelled else None
        derived = round(total - arrears, 2) if total and total > arrears else None
        table = extract_current_charge(text)

        if derived is not None and table is not None:
            # Both readings exist: trust them only if they agree, else something was misread.
            return derived if abs(derived - table) < 0.01 else None
        if derived is not None:
            return derived
        if table is not None:
            return table

    # 2/3. Labelled total; or the label was there but unreadable -> leave empty.
    if unreadable_score and (labelled is None or unreadable_score > labelled[0]):
        return None
    if labelled:
        return labelled[1]

    # 4. Last resort: the largest plausible amount not on a subtotal/VAT/penalty line.
    everything, clean = [], []
    for line in lines:
        values = [v for _, v in _amounts_in(line) if v > 0]
        everything.extend(values)
        if not AMOUNT_NEGATIVE.search(line.lower()):
            clean.extend(values)

    pool = [v for v in (clean or everything) if v >= MIN_PLAUSIBLE_TOTAL]
    if not pool:
        pool = clean or everything
    return max(pool) if pool else None


# ============================================================
# DUE DATE EXTRACTION
# ============================================================

MONTH_MAP = {
    'jan': 1, 'feb': 2, 'mar': 3, 'apr': 4, 'may': 5, 'jun': 6,
    'jul': 7, 'aug': 8, 'sep': 9, 'oct': 10, 'nov': 11, 'dec': 12,
}

_MON = (r'(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|'
        r'aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)')

DATE_MDY_TEXT = re.compile(rf'\b{_MON}\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?,?\s+(\d{{4}})\b', re.I)
DATE_DMY_TEXT = re.compile(rf'\b(\d{{1,2}})(?:st|nd|rd|th)?\s+{_MON}\.?,?\s+(\d{{4}})\b', re.I)
DATE_ISO = re.compile(r'\b(\d{4})-(\d{1,2})-(\d{1,2})\b')
DATE_NUMERIC = re.compile(r'\b(\d{1,2})[/\-.](\d{1,2})[/\-.](\d{4}|\d{2})\b')

# Year-less dates such as "9-23". Only "-" or "/" separators: a "." would turn amounts like
# "9.25" (a water rate) into September 25. One stray trailing OCR digit ("9-235") is tolerated.
SHORT_DATE = re.compile(r'(?<![\d/.\-:,])(\d{1,2})[-/](\d{1,2})\d?(?![\d/.\-])')
MONTH_YEAR = re.compile(rf'\b{_MON}\.?\s+(20\d{{2}})\b', re.I)

# "amount due until/after due date" are amount columns, not date labels
_AMOUNT_PHRASE = re.compile(r'(?:after|until)\s+due\s+date')

DUE_STRONG = re.compile(
    r'due\s*date|date\s+due|'
    r'pay(?:ment)?\s+(?:on\s+or\s+)?before|on\s+or\s+before|'
    r'payment\s+due|due\s+on|due\s+by|pay\s+by|settle\s+before',
    re.I,
)
DUE_WEAK = re.compile(r'\bdue\b', re.I)

DATE_NEGATIVE = re.compile(
    r'bill(?:ing)?\s+date|statement\s+date|invoice\s+date|date\s+(?:of\s+)?issue|issued|'
    r'reading|period|cut-?off|disconnect\w*|date\s+paid|paid\s+on|transaction\s+date|receipt\s+date',
    re.I,
)

LOOSE_DATE_RE = re.compile(
    r'('
    r'(?<!\d)\d{1,2}\s*[/\-.]\s*\d{1,2}\s*[/\-.]\s*\d{2,4}(?!\d)'
    r'|(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+\d{1,2},?\s+\d{2,4}'
    r'|\d{1,2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?,?\s+\d{2,4}'
    r')',
    re.IGNORECASE,
)


def _make_date(year, month, day):
    if year < 100:
        year += 2000
    if not (2000 <= year <= 2100):
        return None
    try:
        return datetime(year, month, day)
    except ValueError:
        return None


def _dates_in(line):
    """All full dates (with a year) on a line as [(position, datetime)]."""
    found = []

    for m in DATE_MDY_TEXT.finditer(line):
        d = _make_date(int(m.group(3)), MONTH_MAP[m.group(1).lower()[:3]], int(m.group(2)))
        if d:
            found.append((m.start(), d))

    for m in DATE_DMY_TEXT.finditer(line):
        d = _make_date(int(m.group(3)), MONTH_MAP[m.group(2).lower()[:3]], int(m.group(1)))
        if d:
            found.append((m.start(), d))

    for m in DATE_ISO.finditer(line):
        d = _make_date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        if d:
            found.append((m.start(), d))

    for m in DATE_NUMERIC.finditer(line):
        a, b, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        # Philippine bills are usually MM/DD/YYYY; flip only when that's impossible.
        month, day = (b, a) if a > 12 and b <= 12 else (a, b)
        d = _make_date(y, month, day)
        if d:
            found.append((m.start(), d))

    return sorted(found, key=lambda item: item[0])


def _infer_year(text):
    """(year, billing month) from a 'Sep 2026' style mention on the bill, else (this year, None)."""
    now_year = datetime.now().year
    for m in MONTH_YEAR.finditer(text):
        year = int(m.group(2))
        if abs(year - now_year) <= 1:  # "Sep 2036" is an OCR misread, not a year
            return year, MONTH_MAP[m.group(1).lower()[:3]]
    return now_year, None


def _short_dates_in(line, year, bill_month=None):
    """Year-less dates like '9-23' on a line, using the given year."""
    found = []
    for m in SHORT_DATE.finditer(line):
        month, day_text = int(m.group(1)), m.group(2)
        day = int(day_text)
        if day > 31 and len(day_text) == 2:  # "35" was really "3" + a stray digit
            day = int(day_text[0])
        if not (1 <= month <= 12 and 1 <= day <= 31):
            continue
        y = year + 1 if bill_month and month < bill_month - 6 else year  # due early next year
        d = _make_date(y, month, day)
        if d:
            found.append((m.start(), d))
    return found


def _mask_amount_phrases(low):
    return _AMOUNT_PHRASE.sub(lambda m: ' ' * len(m.group(0)), low)


def _date_after_label(lines, pattern, short=None):
    """
    First date belonging to a line that matches `pattern` (same line, else the next two).
    With `short=(year, bill_month)` it reads year-less dates like '9-23' instead.
    """
    def find(s):
        return _dates_in(s) if short is None else _short_dates_in(s, *short)

    for i, line in enumerate(lines):
        low = _mask_amount_phrases(line.lower())
        match = pattern.search(low)
        if not match:
            continue

        same = find(line)
        after = [d for pos, d in same if pos >= match.end()]
        if after:
            return after[0]
        if same:
            return same[-1][1]

        for nxt in lines[i + 1:i + 3]:
            dates = [d for _, d in find(nxt)]
            if not dates:
                continue
            # Header row like "Billing Date   Due Date" followed by a row of values:
            # pick the value in the same column position as the due-date label.
            labels = sorted(
                [(m.start(), 'due') for m in DUE_STRONG.finditer(low)]
                + [(m.start(), 'other') for m in DATE_NEGATIVE.finditer(low)]
            )
            if len(labels) > 1 and len(labels) == len(dates):
                due_index = next(idx for idx, (_, kind) in enumerate(labels) if kind == 'due')
                return dates[due_index]
            # A row of values under the headings holds the reading/billing date AND the due
            # date; the due date is always the latest of them.
            return max(dates)
    return None


_MONTH_WORD = r'(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?'

# A utility-bill stub row: "<reading date> <due date> <amount after due date> <Mon YYYY>",
# e.g. "9-3 | 9-23 | 7,256.53 | Sep 2026". The due date is the one right before the amount.
_STUB_ROW = re.compile(
    rf'(\d{{1,2}}\s*[-/]\s*\d{{1,2}})\d?\W{{0,4}}\d[\d,]*\.\d{{2}}\W{{0,4}}{_MONTH_WORD}\s*\d{{4}}',
    re.I,
)
_DATE_LIKE_LEFTOVER = re.compile(rf'[\d,.\s|/\-:]+|{_MONTH_WORD}', re.I)


def _date_from_value_row(lines, short):
    """
    Fallback for when the small "DATE DUE" heading wasn't read (common on compressed photos),
    using the shape of the row of values under the headings:
      1. one line holding "<date> <amount> <Mon YYYY>": the date is the due date;
      2. a run of date-like lines holding two dates 1-45 days apart ("9-3" then "9-23"):
         the due date is the later one.
    """
    year, bill_month = short

    for line in lines:
        m = _STUB_ROW.search(line)
        if m:
            found = _short_dates_in(m.group(1), year, bill_month)
            if found:
                return found[0][1]

    for i in range(len(lines)):
        cluster = []
        for line in lines[i:i + 6]:
            leftover = _DATE_LIKE_LEFTOVER.sub('', line)
            if len(leftover) > 3:  # real words: not part of a row of values
                break
            cluster.extend(d for _, d in _short_dates_in(line, year, bill_month))
        for a, b in zip(cluster, cluster[1:]):
            if 1 <= (b - a).days <= 45:
                return b
    return None


def _parse_with_dateutil(lines):
    """Spaced or odd separators the regexes miss ("09 / 15 / 2026"); skips billing/reading lines."""
    if date_parser is None:
        return None

    priority, other = [], []
    for line in lines:
        low = line.lower()
        if DATE_NEGATIVE.search(low):
            continue
        for m in LOOSE_DATE_RE.finditer(line):
            try:
                parsed = date_parser.parse(m.group(1), fuzzy=False)
            except (ValueError, OverflowError):
                continue
            (priority if DUE_WEAK.search(low) else other).append(parsed)

    pool = priority or other
    if not pool:
        return None

    now = datetime.now()
    future = [d for d in pool if d >= now]
    return min(future) if future else max(pool)


def extract_due_date(text):
    """
    Due date as YYYY-MM-DD.

    1. Date next to "Due Date" / "Pay on or before" etc., then next to any "due" line.
       Year-less dates ("9-23") are read too, with the year taken from e.g. "Sep 2026".
    2. dateutil on dates the regexes missed (spaced separators).
    3. Among dates not on billing/statement/reading lines: earliest future, else latest.
    """
    lines = _lines(text)
    short = _infer_year(text)

    chosen = (
        _date_after_label(lines, DUE_STRONG)
        or _date_after_label(lines, DUE_STRONG, short)
        or _date_after_label(lines, DUE_WEAK)
        or _date_after_label(lines, DUE_WEAK, short)
        or _date_from_value_row(lines, short)
    )
    if chosen:
        return chosen.strftime('%Y-%m-%d')

    fuzzy = _parse_with_dateutil(lines)
    if fuzzy:
        return fuzzy.strftime('%Y-%m-%d')

    clean, everything = [], []
    for line in lines:
        dates = [d for _, d in _dates_in(line)]
        everything.extend(dates)
        if not DATE_NEGATIVE.search(line.lower()):
            clean.extend(dates)

    pool = clean or everything
    if not pool:
        return None

    now = datetime.now()
    future = [d for d in pool if d >= now]
    chosen = min(future) if future else max(pool)
    return chosen.strftime('%Y-%m-%d')


# ============================================================
# MERCHANT EXTRACTION
# ============================================================

GENERIC_HEADERS = {
    'receipt', 'invoice', 'statement', 'bill', 'official', 'summary', 'notice',
    'tax', 'billing', 'page', 'original', 'duplicate', 'copy', 'customer', 'account',
}

COMPANY_HINT_RE = re.compile(
    r'\b(?:corporation|corp|inc|incorporated|company|co|ltd|llc|pbc|enterprises?|holdings|'
    r'group|cooperative|coop|'
    r'electric\w*|telecom\w*|communication\w*|utilit\w*|water\s+d\w*|waterworks|'
    r'meralco|maynilad|pldt|globe|converge|cepalco|moresco|smart|manila\s+water|sky|dito|'
    r'cagayan|oro\s+city)\b',
    re.I,
)

MERCHANT_SKIP_RE = re.compile(
    r'bill\s+to|customer|account\s*(?:no|number|#)|invoice\s*(?:no|number|#)|receipt\s*(?:no|number|#)|'
    r'tel\b|phone|www\.|http|@|address|attn|total|amount|date|powered\s+by|thank\s+you',
    re.I,
)

# "Receipt from Juan's Cafe", "Invoice from Acme Hosting"
FROM_RE = re.compile(r'^(?:receipt|invoice|statement|payment)\s+from\s+(.+)$', re.I)

REAL_WORD_RE = re.compile(r'[A-Za-z]{4,}')

# "GOLEZ, ALONA IROC": an ALL-CAPS surname then a comma is how bills print the customer
PERSON_NAME_RE = re.compile(r'^[A-Z]{3,},\s+\S+')

MANY_SINGLE_LETTERS_RE = re.compile(
    r'(?<![A-Za-z])(?:[A-Za-z](?![A-Za-z]))(?:\s+(?:[A-Za-z](?![A-Za-z]))){2,}'
)

ADDRESS_HINT_RE = re.compile(
    r'\b(?:villa|street|st\.|ave|avenue|blk|blk\.|lot|brgy|barangay|zone|purok|subdivision|'
    r'subd\.?|phase|north|south|east|west|purok)\b',
    re.I,
)

# Short and specific on purpose: generic words like "power" or "internet" also appear in
# "Powered by ..." and "Internet banking" lines.
FUZZY_BILLER_HINTS = (
    'water district', 'water districts', 'waterworks',
    'electric', 'telecom', 'communications',
    'maynilad', 'meralco', 'pldt', 'globe', 'converge', 'cepalco', 'moresco',
    'manila water', 'cagayan',
)


def _looks_like_biller(line):
    low = line.lower()
    return any(h in low for h in FUZZY_BILLER_HINTS)


def _clean_merchant(line):
    """Strip OCR junk: stray symbols, and a mis-read mixed-case first word on an ALL CAPS name."""
    line = re.sub(r"[^\w&.,'()\- ]", ' ', line)
    tokens = line.split()
    letters = [c for c in line if c.isalpha()]
    mostly_upper = letters and sum(c.isupper() for c in letters) / len(letters) > 0.6
    if mostly_upper:
        # "Sgas CAG ORO CITY WATER DISTRICT": drop leading tokens that aren't ALL CAPS
        while len(tokens) > 2 and not tokens[0].isupper():
            tokens.pop(0)
    cleaned = ' '.join(tokens).strip(" ,-").lstrip(". ")  # keep a trailing period ("Inc.")
    return cleaned or line.strip()


_HEADER_WORDS = sorted(GENERIC_HEADERS | {'official', 'receipt', 'acknowledgment', 'payment'})


def _is_generic_header(line):
    """True for 'Official Receipt' even when OCR garbles it ('OFFICTAL RECTIP')."""
    words = re.findall(r'[A-Za-z]{3,}', line.lower())
    if not words or len(words) > 4:
        return False
    return all(
        any(SequenceMatcher(None, w, h).ratio() >= 0.75 for h in _HEADER_WORDS)
        for w in words
    )


_CUSTOMER_BLOCK = re.compile(r'name\s*(?:and|&)\s*address|account\s*name|customer\s*name|bill\s+to|sold\s+to', re.I)


def _without_customer_block(lines):
    """Drop the lines right after "NAME AND ADDRESS" etc.: that's the customer, not the merchant."""
    out, skip = [], 0
    for ln in lines:
        if skip:
            skip -= 1
            continue
        out.append(ln)
        if _CUSTOMER_BLOCK.search(ln):
            skip = 2
    return out


def extract_merchant(text):
    lines = _without_customer_block([ln for ln in _lines(text) if not _is_generic_header(ln)])
    if not lines:
        return None

    for line in lines[:8]:
        m = FROM_RE.match(line.strip())
        if m and len(m.group(1).strip()) >= 3:
            return _clean_merchant(m.group(1))

    for line in lines[:15]:
        if len(line) > 80 or MERCHANT_SKIP_RE.search(line):
            continue
        if COMPANY_HINT_RE.search(line):
            return _clean_merchant(line)

    for line in lines[:15]:
        if len(line) > 90 or len(line) < 6:
            continue
        if MERCHANT_SKIP_RE.search(line):
            continue
        if not REAL_WORD_RE.search(line):
            continue
        if _looks_like_biller(line):
            m = REAL_WORD_RE.search(line)
            if m and m.start() > 0:
                return _clean_merchant(line[m.start():])
            return _clean_merchant(line)

    for line in lines[:10]:
        clean = line.strip(" :.-_")
        lower = clean.lower()

        if lower in GENERIC_HEADERS:
            continue
        if any(lower.startswith(h) for h in GENERIC_HEADERS):
            continue
        if MERCHANT_SKIP_RE.search(clean):
            continue
        if ADDRESS_HINT_RE.search(clean):
            continue
        if MANY_SINGLE_LETTERS_RE.search(clean):
            continue
        if PERSON_NAME_RE.match(clean):
            continue

        letters = sum(c.isalpha() for c in clean)
        digits = sum(c.isdigit() for c in clean)
        if letters < 3 or len(clean) > 60:
            continue
        if digits >= letters:
            continue

        nonspace = [c for c in clean if not c.isspace()]
        if not nonspace:
            continue
        if letters / len(nonspace) < 0.6:
            continue
        if not REAL_WORD_RE.search(clean):
            continue

        return _clean_merchant(clean)

    return None


# ============================================================
# PDF HANDLING
# ============================================================

def _pdf_text(file_obj, max_pages=2):
    if fitz is None:
        return ''
    file_obj.seek(0)
    doc = fitz.open(stream=file_obj.read(), filetype='pdf')
    try:
        pages = min(doc.page_count, max_pages)
        return '\n'.join(doc.load_page(i).get_text('text', sort=True) for i in range(pages))
    finally:
        doc.close()


def pdf_first_page_to_image(file_obj, dpi=300):
    if fitz is None:
        raise RuntimeError(
            "PyMuPDF is required to scan PDF receipts. Install it with `pip install pymupdf`."
        )

    file_obj.seek(0)
    doc = fitz.open(stream=file_obj.read(), filetype='pdf')
    try:
        if doc.page_count == 0:
            raise ValueError('The PDF has no pages.')

        page = doc.load_page(0)
        zoom = dpi / 72
        pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom))
        return Image.open(io.BytesIO(pix.tobytes('png')))
    finally:
        doc.close()


def _has_real_text(text):
    return len(re.sub(r'\s', '', text or '')) >= 40


# ============================================================
# PIPELINE
# ============================================================

def _extract_fields(text):
    return {
        'amount': extract_amount(text),
        'due_date': extract_due_date(text),
        'merchant': extract_merchant(text),
    }


def _scale_and_contrast(gray):
    width, height = gray.size
    if width < TARGET_WIDTH:
        scale = TARGET_WIDTH / width
        gray = gray.resize((int(width * scale), int(height * scale)), Image.LANCZOS)
    elif width > MAX_WIDTH:
        scale = MAX_WIDTH / width
        gray = gray.resize((int(width * scale), int(height * scale)), Image.LANCZOS)
    return ImageOps.autocontrast(gray, cutoff=1)


def _adaptive(gray):
    """
    Local binarization: each pixel is compared with its own neighbourhood.

    This is the single biggest accuracy win on phone photos. Tesseract's built-in thresholding
    is global, so a dark background, a shadow or a finger in the frame makes it drop text
    elsewhere in the picture. On a real COWD bill photo it read none of the amounts without
    this step and every one of them with it.
    """
    block = max(31, int(gray.width / 48)) | 1  # odd; ~50px at 2400px wide
    if cv2 is not None and np is not None:
        arr = cv2.medianBlur(np.array(gray), 3)
        out = cv2.adaptiveThreshold(
            arr, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, block, 15
        )
        return Image.fromarray(out)

    # Pure-Pillow fallback: pixel vs. the local mean (box blur)
    smooth = gray.filter(ImageFilter.MedianFilter(3))
    local_mean = smooth.filter(ImageFilter.BoxBlur(block // 2))
    diff = ImageChops.subtract(smooth, local_mean, 1.0, 128)  # pixel - mean + 128
    return diff.point(lambda p: 255 if p > 128 - 12 else 0)


def _views(image):
    """
    Three adaptive-thresholded views of the same photo. On a dot-matrix print over a blue form
    each view reads different digits correctly (measured on a real COWD bill), so the passes
    vote:
      min:  darkest of the colour channels, a high-contrast "ink" view
      blue: blue form lines and labels turn pale, black print stays dark
      gray: the normal picture
    """
    image = ImageOps.exif_transpose(image)
    if image.mode in ('RGBA', 'LA', 'P'):
        rgba = image.convert('RGBA')
        background = Image.new('RGB', rgba.size, 'white')
        background.paste(rgba, mask=rgba.split()[-1])
        image = background
    rgb = image.convert('RGB')
    red, green, blue = rgb.split()
    return {
        'min': _adaptive(_scale_and_contrast(ImageChops.darker(ImageChops.darker(red, green), blue))),
        'blue': _adaptive(_scale_and_contrast(blue)),
        'gray': _adaptive(_scale_and_contrast(rgb.convert('L'))),
    }


# psm 6: one uniform block. psm 11: sparse text (good for form fields and table cells).
# Stage 1 is the pair that did best on real bills; stage 2 only runs when stage 1 isn't conclusive.
STAGE_1 = (('min', 6), ('blue', 11))
STAGE_2 = (('gray', 6), ('gray', 11), ('blue', 6), ('min', 11))


def _run_passes(views, jobs):
    def run(job):
        name, psm = job
        text, confidence = _ocr(views[name], psm)
        return {
            'name': name, 'psm': psm, 'text': text, 'confidence': confidence,
            'parts': amount_parts(text),
            'due_date': extract_due_date(text),
            'merchant': extract_merchant(text),
        }

    workers = max(1, min(len(jobs), os.cpu_count() or 2))
    with ThreadPoolExecutor(max_workers=workers) as pool:  # tesseract runs as a subprocess
        return list(pool.map(run, jobs))


def _decide(results):
    by_confidence = sorted(results, key=lambda r: r['confidence'], reverse=True)

    amount, confirmed = decide_amount([r['parts'] for r in results], with_confirmation=True)

    due_votes = Counter(r['due_date'] for r in results if r['due_date'])
    due_date = None
    if due_votes:
        top = max(due_votes.values())
        tied = [d for d, c in due_votes.items() if c == top]
        due_date = next(r['due_date'] for r in by_confidence if r['due_date'] in tied)

    merchant = next((r['merchant'] for r in by_confidence if r['merchant']), None)
    return by_confidence, {'amount': amount, 'due_date': due_date, 'merchant': merchant}, confirmed


def _ocr_pooled(image):
    """
    OCR the photo several ways and combine the passes:
      amount   - voting, plus the total - arrears = this month consistency check
      due date - most common across passes
      merchant - from the most confident pass that found one
    Two stages keep it fast: the second only runs if the first couldn't settle the answer.
    Returns (combined text, best confidence, fields).
    """
    views = _views(image)

    results = _run_passes(views, STAGE_1)
    by_confidence, fields, confirmed = _decide(results)

    if fields['amount'] is None or fields['due_date'] is None or not confirmed:
        results += _run_passes(views, STAGE_2)
        by_confidence, fields, confirmed = _decide(results)

    if DEBUG_DIR:
        os.makedirs(DEBUG_DIR, exist_ok=True)
        for r in results:
            stem = os.path.join(DEBUG_DIR, f"{r['name']}_psm{r['psm']}")
            views[r['name']].save(stem + '.png')
            with open(stem + '.txt', 'w', encoding='utf-8') as fh:
                fh.write(f"confidence={r['confidence']:.1f}\nparts={r['parts']}\n"
                         f"due_date={r['due_date']}\nmerchant={r['merchant']}\n\n{r['text']}")

    # Best pass first, so line positions (used for biller detection) come from the cleanest text
    combined = '\n\n'.join(r['text'] for r in by_confidence)
    return combined, by_confidence[0]['confidence'], fields


def scan_bill_image(file_obj, is_pdf=False):
    if is_pdf:
        text = _pdf_text(file_obj)
        if _has_real_text(text):
            fields = _extract_fields(text)
            if fields['amount'] is not None:
                return {**fields, 'raw_text': text, 'method': 'pdf_text', 'confidence': 100.0}

            ocr_text, confidence, ocr_fields = _ocr_pooled(pdf_first_page_to_image(file_obj))
            merged = {k: fields[k] if fields[k] is not None else ocr_fields[k] for k in fields}
            return {**merged, 'raw_text': text + '\n' + ocr_text, 'method': 'pdf_text+ocr',
                    'confidence': confidence}

        image = pdf_first_page_to_image(file_obj)
    else:
        file_obj.seek(0)
        image = Image.open(file_obj)

    text, confidence, fields = _ocr_pooled(image)
    return {**fields, 'raw_text': text, 'method': 'ocr', 'confidence': round(confidence, 1)}