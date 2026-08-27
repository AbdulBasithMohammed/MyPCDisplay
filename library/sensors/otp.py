#!/usr/bin/env python
"""OTP detection, and the shared state the display child reads it from.

The hard part is not fetching mail, it is deciding that a number is a one-time
code rather than an order number, a price or a year. Two properties of the
problem make a scoring approach work where a bare six-digit regex does not:

  * We only ever score *arrivals*. Nothing scans a mailbox, so the corpus is
    "messages that landed in the last few minutes", which is almost entirely
    free of the archive noise that would swamp a naive match.
  * A code is worthless without intent words near it. "Your verification code
    is 419 022" and "Order 4190220 has shipped" are trivially separable by
    proximity, and not separable at all by shape.

Tuned for precision over recall on purpose. A missed OTP costs a glance at the
phone; a wrong one displayed confidently costs a failed login attempt and the
trust that makes the screen worth having. When two candidates score close
together this deliberately shows nothing rather than guessing - see
_AMBIGUITY_MARGIN.
"""
import html
import json
import logging
import os
import re
import time

APP_DIR = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
SERVICES_FILE = os.path.join(APP_DIR, "services.yaml")
# Not tmp/: library/lcd/lcd_simulated.py saves the simulated panel to a *file*
# named "tmp" in the repo root (and .gitignore lists it as such), so any run of
# preview-theme.py makes tmp/ un-creatable as a directory. cache/ is already the
# gitignored home for per-machine runtime state.
STATE_FILE = os.path.join(APP_DIR, "cache", "otp_state.json")

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------

# A code token: 4-8 alphanumerics, or the "123-456" form some services use.
# The lookarounds stop us slicing a short run out of a long account number.
# Alphanumeric codes must contain a digit (checked below), or every word of
# 4-8 letters becomes a candidate. The alphanumeric branch accepts BOTH cases:
# Greenhouse sends codes like N6Ek26wS, and an uppercase-only pattern never
# even produced a candidate for it - the miss surfaced on a real application
# email whose best-scoring token was Greenhouse's zip code.
CODE_RE = re.compile(r"(?<![A-Za-z0-9])("
                     r"[0-9]{3}[-\s][0-9]{3}"     # 123-456 / 123 456
                     r"|[0-9]{4,8}"               # 419022
                     r"|[A-Za-z0-9]{4,8}"         # G4X9A2, N6Ek26wS
                     r")(?![A-Za-z0-9])")

# Phrases that mean "the number next to me is a credential".
STRONG_PHRASES = (
    "verification code", "verify code", "security code", "one-time password",
    "one time password", "one-time code", "one time code", "otp", "passcode",
    "pass code", "2fa", "two-factor", "two factor", "2-step", "two-step",
    "authentication code", "auth code", "login code", "log-in code",
    "sign-in code", "sign in code", "confirmation code", "access code",
    "single-use code", "temporary code", "your code", "code is",
    "confirm your email", "verify your email", "verify your identity",
)

# Weaker signals, worth about half a strong phrase. These exist for the very
# common subject form "482913 is your Instagram code", where the code leads and
# no strong phrase ever forms - "your code" and "code is" both miss it.
WEAK_PHRASES = ("code", "verification", "verify", "authenticate", "security")

# Words that mean the number next to me is something else entirely. These are
# what a bare digit regex gets wrong, so they are weighted to actually bite.
NEGATIVE_WORDS = (
    "order", "invoice", "receipt", "tracking", "shipment", "shipped",
    "reference number", "ref no", "account number", "acct", "customer number",
    "membership", "policy number", "ticket number", "case number", "isbn",
    "unsubscribe", "copyright", "version", "phone", "tel:", "zip", "postcode",
    "amount", "total", "balance", "subtotal", "price", "qty",
    # Marketing codes are the one thing that pairs promotional digits with the
    # word "code", so they have to be excluded by name.
    "promo code", "discount code", "coupon", "referral code", "gift code",
)

CURRENCY = "$£€¥₹"

# How close a phrase has to sit to a candidate to count, in characters.
_NEAR = 45
_FAR = 120

# If the two best candidates differ in value but their scores are within this,
# we cannot tell them apart and must not pick one.
_AMBIGUITY_MARGIN = 2

MIN_SCORE = 5


def strip_html(text):
    """HTML to rough plain text, dropping the parts that manufacture digits.

    Style blocks and URLs are removed rather than merely tagged out, because
    hex colours (#4419af) and tracking query strings (?id=419022&u=...) both
    parse as perfectly good six-character codes and would otherwise dominate
    the candidate list of every marketing email.
    """
    if not text:
        return ""
    text = re.sub(r"(?is)<(script|style|head)[^>]*>.*?</\1>", " ", text)
    text = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</tr>", "\n", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"(?i)\bhttps?://\S+", " ", text)
    text = re.sub(r"[ \t]+", " ", text)
    return re.sub(r"\n\s*\n+", "\n", text).strip()


def _looks_like_year(token):
    return bool(re.fullmatch(r"(19|20)[0-9]{2}", token))


def _phrase_distance(low, start, end, phrases=STRONG_PHRASES):
    """Characters from the candidate to the nearest intent phrase, or None."""
    best = None
    for phrase in phrases:
        idx = 0
        while True:
            hit = low.find(phrase, idx)
            if hit < 0:
                break
            if hit + len(phrase) <= start:
                d = start - (hit + len(phrase))
            elif hit >= end:
                d = hit - end
            else:
                d = 0
            best = d if best is None else min(best, d)
            idx = hit + 1
    return best


def _score(token, low, start, end, in_subject, sender):
    """Score one candidate. Returns (score, why); `why` exists for the log."""
    score, why = 0, []

    dist = _phrase_distance(low, start, end)
    if dist is None:
        weak = _phrase_distance(low, start, end, WEAK_PHRASES)
        if weak is not None and weak <= _NEAR:
            score += 2
            why.append("weak@%d" % weak)
        elif not in_subject:
            # No intent word anywhere near it in a body. A subject line is
            # short enough that a bare code still means something; a body is
            # not, and scoring one invites false positives from every receipt.
            return -99, ["no intent phrase"]
        else:
            score -= 1
            why.append("no phrase")
    elif dist <= _NEAR:
        score += 5
        why.append("phrase@%d" % dist)
    elif dist <= _FAR:
        score += 3
        why.append("phrase@%d" % dist)
    else:
        score += 1
        why.append("phrase far")

    if in_subject:
        score += 2
        why.append("subject")

    digits = re.sub(r"[^0-9]", "", token)
    if len(digits) == 6:                  # by far the most common OTP length
        score += 2
        why.append("6d")
    elif len(digits) in (4, 5, 7, 8):
        score += 1
        why.append("%dd" % len(digits))
    elif (re.search(r"[a-z]", token) and re.search(r"[A-Z]", token)
          and re.search(r"[0-9][A-Za-z]", token)):
        # Mixed case with a digit somewhere BEFORE the last letter (N6Ek26wS)
        # is the shape of a generated token. A word with digits stuck on the
        # end - SAVE20, iPhone17 - never has a digit followed by more letters,
        # so product names and promo codes do not collect this bonus.
        score += 2
        why.append("mixed-case")

    if re.search(r"(?i)(no-?reply|do-?not-?reply|verify|security|account|auth)",
                 sender or ""):
        score += 1
        why.append("sender")

    # ---- structural rejects -----------------------------------------------
    if _looks_like_year(token):
        score -= 6
        why.append("year")

    window = low[max(0, start - 30):end + 30]
    for word in NEGATIVE_WORDS:
        if word in window:
            score -= 4
            why.append("neg:" + word)
            break

    before = low[max(0, start - 2):start]
    after = low[end:end + 2]
    if any(c in before for c in CURRENCY):
        score -= 5
        why.append("currency")
    if re.match(r"^[.,][0-9]", after) or re.search(r"[0-9][.,]$", before):
        score -= 5
        why.append("grouped number")
    if after.startswith("%"):
        score -= 3
        why.append("percent")

    return score, why


def find_code(subject, body, sender=""):
    """Best OTP candidate in one message.

    Returns (code, score, why), with code None when nothing clears MIN_SCORE or
    when the top two candidates are too close to tell apart.
    """
    subject = subject or ""
    body = strip_html(body or "")
    # Cap the body: OTP mails put the code near the top, and reading a long
    # newsletter tail only adds chances to be wrong.
    body = body[:4000]

    candidates = []
    for text, in_subject in ((subject, True), (body, False)):
        low = text.lower()
        for m in CODE_RE.finditer(text):
            token = m.group(1)
            if not re.search(r"[0-9]", token):
                continue
            score, why = _score(token, low, m.start(1), m.end(1), in_subject, sender)
            candidates.append((score, re.sub(r"[-\s]", "", token), why))

    if not candidates:
        return None, 0, ["no candidates"]

    candidates.sort(key=lambda c: -c[0])
    best_score, best_code, best_why = candidates[0]
    if best_score < MIN_SCORE:
        return None, best_score, best_why + ["below threshold"]

    for score, code, _why in candidates[1:]:
        if best_score - score > _AMBIGUITY_MARGIN:
            break
        if code != best_code:
            return None, best_score, ["ambiguous: %s vs %s" % (best_code, code)]

    return best_code, best_score, best_why


# Subdomains that carry no information about who sent the mail, so that
# "no-reply@accounts.google.com" reads as "Google" on a 480px panel.
_NOISE_LABELS = ("mail", "email", "smtp", "accounts", "account", "no-reply",
                 "noreply", "notifications", "notify", "auth", "security",
                 "id", "e", "em", "mx", "send", "sendgrid", "mailer")


def friendly_sender(raw):
    """A From header reduced to something that fits on the panel.

    Prefers the display name, falls back to the most meaningful label in the
    domain. Never returns a bare address: "no-reply@accounts.google.com" is
    both too long and less recognisable than "Google".
    """
    import email.utils

    raw = (raw or "").strip()
    if not raw:
        return ""
    name, addr = email.utils.parseaddr(raw)
    name = (name or "").strip().strip('"')
    if name and "@" not in name:
        return name[:28]

    domain = (addr or raw).split("@")[-1].lower()
    labels = [l for l in domain.split(".") if l]
    if len(labels) > 1:
        labels = labels[:-1]          # drop the TLD
        # Drop a country-code second level such as .co.uk
        if len(labels) > 1 and len(labels[-1]) <= 3 and labels[-1] in ("co", "com", "org", "net", "ac", "gov"):
            labels = labels[:-1]
    meaningful = [l for l in labels if l not in _NOISE_LABELS]
    pick = (meaningful or labels or [domain])[-1]
    return pick.replace("-", " ").title()[:28]


# ---------------------------------------------------------------------------
# Shared state: supervisor writes, display child reads
# ---------------------------------------------------------------------------

def publish(code, sender, source, hold_seconds, label=""):
    """Write the current notification where the display child can read it.

    A file rather than an in-memory handoff because the child is killed and
    respawned on every screen switch (see CLAUDE.md); anything held in child
    memory dies with it, and this notification has to survive the very switch
    that puts it on the panel.
    """
    payload = {"code": code, "sender": sender, "source": source,
               "label": label or "",
               "at": time.time(), "expires_at": time.time() + hold_seconds}
    try:
        os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
        tmp = STATE_FILE + ".new"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f)
        os.replace(tmp, STATE_FILE)   # atomic: the child may be reading it
    except Exception:
        # Logged, not swallowed: a silent failure here shows an empty
        # notification screen with no clue why, which cost an hour once when
        # the state directory could not be created.
        log.exception("Could not publish OTP state to %s", STATE_FILE)
    return payload


def read_state():
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def clear():
    try:
        os.remove(STATE_FILE)
    except Exception:
        pass


def load_services():
    try:
        import yaml
        with open(SERVICES_FILE, encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except Exception:
        return {}
