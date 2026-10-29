"""
Checks why the Claude reader isn't being used. Run it from the SAME folder as ocr.py and
llm_reader.py, in the SAME terminal window where you start the Django server (so it sees the
same environment variables):

    python llm_check.py                      # checks settings + one tiny live API call
    python llm_check.py "C:\\path\\bill.jpg"   # ...and then reads that photo with Claude

The live call asks the model to say "OK" with max_tokens=20. It costs a tiny fraction of a cent.
"""
import os
import sys

OK, BAD, WARN = '[ok]  ', '[FAIL]', '[warn]'
problems = []


def say(tag, text):
    print(f'{tag} {text}')
    if tag == BAD:
        problems.append(text)


print('=== 1. Settings in this terminal ===')
key = os.environ.get('ANTHROPIC_API_KEY')
if key:
    say(OK, f'ANTHROPIC_API_KEY is set ({key[:7]}..., {len(key)} characters)')
    if not key.startswith('sk-ant-'):
        say(WARN, 'a real key normally starts with "sk-ant-". Check you copied the whole key, with no quotes or spaces.')
else:
    say(BAD, 'ANTHROPIC_API_KEY is NOT set in this terminal. PowerShell: $env:ANTHROPIC_API_KEY = "your-key"')

mode = (os.environ.get('BILLWISE_LLM_MODE') or '').strip().lower()
if mode in ('always', 'fallback'):
    say(OK, f'BILLWISE_LLM_MODE = {mode}')
else:
    say(BAD, f'BILLWISE_LLM_MODE is {mode or "not set"}. It must be "always" or "fallback". '
             'PowerShell: $env:BILLWISE_LLM_MODE = "always"')
print('       (variables only last for the terminal window they were set in: set them again in the one '
      'that runs "python manage.py runserver", then restart the server)')

print('\n=== 2. The anthropic package ===')
try:
    import anthropic
    say(OK, f'anthropic {getattr(anthropic, "__version__", "?")} is installed')
except ImportError:
    anthropic = None
    say(BAD, 'the anthropic package is not installed in this Python. Run: pip install anthropic '
             '(inside your backend virtual environment)')

print('\n=== 3. The code files ===')
here = os.path.dirname(os.path.abspath(__file__))
for name in ('ocr.py', 'llm_reader.py'):
    if os.path.exists(os.path.join(here, name)):
        say(OK, f'{name} found in {here}')
    else:
        say(BAD, f'{name} is not in this folder. Both files must sit next to views.py.')

sys.path.insert(0, here)
llm_reader = None
try:
    import llm_reader
    say(OK, f'llm_reader imports ({llm_reader.__file__})')
    say(OK if llm_reader.mode() != 'off' else BAD,
        f'llm_reader.mode() = {llm_reader.mode()!r}'
        + ('' if llm_reader.mode() != 'off' else '  <- the reader is OFF, so Tesseract is used for every scan'))
except Exception as exc:
    say(BAD, f'llm_reader.py could not be imported: {type(exc).__name__}: {exc}')

try:
    import ocr
    if getattr(ocr, 'llm_reader', None) is None:
        say(BAD, 'ocr.py cannot see llm_reader.py (it is None inside ocr.py), so scans never call Claude')
    else:
        say(OK, 'ocr.py is the new version and can see llm_reader.py')
    if not hasattr(ocr, '_llm_mode'):
        say(BAD, 'this ocr.py is an OLD version (no Claude hook). Replace it with the latest ocr.py.')
except Exception as exc:
    say(WARN, f'could not import ocr.py here ({type(exc).__name__}: {exc}). Not fatal for this check.')

print('\n=== 4. One tiny live call to the API ===')
if not (key and anthropic and llm_reader):
    say(WARN, 'skipped: fix the failures above first')
else:
    working = None
    for model in llm_reader._models():
        print(f'       trying model: {model}')
        try:
            client = llm_reader._client()
            reply = client.messages.create(model=model, max_tokens=20,
                                           messages=[{'role': 'user', 'content': 'Reply with the single word OK.'}])
            text = ''.join(b.text for b in reply.content if getattr(b, 'type', '') == 'text')
            say(OK, f'{model} answered: {text.strip()!r}. Your key, model and credits all work.')
            print('       Check the Console Billing/usage page now: this call should show up there.')
            working = model
            break
        except Exception as exc:
            status = getattr(exc, 'status_code', None)
            if status == 404:
                print(f'       "{model}" was not found for your account; trying the next one.')
                continue
            say(BAD, f'the API call failed: {type(exc).__name__}' + (f' (HTTP {status})' if status else '') + f': {exc}')
            hint = {
                401: 'The key is wrong, revoked, or copied incompletely. Create a new one in the Console.',
                403: 'The key is not allowed to use this model or feature.',
                429: 'Rate limit or spend limit reached. Wait a minute, or check your limits on the Billing page.',
            }.get(status)
            if status == 400 and any(w in str(exc).lower() for w in ('credit', 'balance', 'billing')):
                hint = 'No usable credit. Open Settings > Billing and buy credits (a few dollars is enough).'
            elif status == 400:
                hint = 'The request was rejected. Read the message above; it names the problem.'
            if type(exc).__name__ in ('APIConnectionError', 'APITimeoutError'):
                hint = 'The server cannot reach api.anthropic.com. Check the internet connection, VPN, proxy or firewall.'
            if hint:
                print(f'       -> {hint}')
            break
    else:
        say(BAD, 'none of the models was found. Set BILLWISE_LLM_MODEL to a current ID from '
                 'https://platform.claude.com/docs/en/models/overview')

if len(sys.argv) > 1:
    print('\n=== 5. Read your photo with Claude ===')
    if not (key and llm_reader and llm_reader.mode() != 'off'):
        say(WARN, 'skipped: the reader is off (see above)')
    else:
        from PIL import Image
        result = llm_reader.read_bill(Image.open(sys.argv[1]))
        if result is None:
            say(BAD, 'read_bill returned None. Scroll up for a line starting "LLM reader failed" or "LLM reader:" with the reason.')
        else:
            for k in ('amount', 'amount_candidates', 'due_date', 'merchant', 'arrears', 'months_delinquent',
                      'disconnection_notice', 'amount_after_due_date', 'vat', 'legible', 'method'):
                print(f'       {k}: {result.get(k)!r}')

print('\n=== Result ===')
if problems:
    print(f'{len(problems)} problem(s) found. Fix the first [FAIL] line above, then run this again.')
else:
    print('Everything checks out. If the app still shows method "ocr", the server was started without these')
    print('settings, or an old ocr.py is running: restart it from THIS terminal.')
    print('(Digital PDFs with readable text never use Claude: their text is read directly.)')