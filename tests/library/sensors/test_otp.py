"""Detection tests for library/sensors/otp.py.

The negative cases matter more than the positive ones. Anything here that is
marked "must not fire" is a real shape of email that a naive six-digit regex
gets wrong, and each one is why the scorer has the term it has.

    venv/Scripts/python.exe -m pytest tests/library/sensors/test_otp.py -q
"""
import pytest

from library.sensors.otp import find_code, strip_html

# (subject, body, sender, expected code)
POSITIVE = [
    ("Your verification code is 419022", "", "no-reply@github.com", "419022"),
    ("Sign in to Acme", "Your one-time code is 823 114. It expires in 10 minutes.",
     "accounts@acme.io", "823114"),
    ("482913 is your Instagram code", "", "security@mail.instagram.com", "482913"),
    ("Acme security", "Use passcode 5521 to finish signing in.", "x@acme.io", "5521"),
    ("Verify your email", "Your confirmation code: 90210-", "no-reply@shop.com", "90210"),
    ("", "Your two-factor authentication code is 77213908.", "auth@bank.com", "77213908"),
    ("Your code is G4X9A2", "", "no-reply@svc.com", "G4X9A2"),
    # Real miss from the field: Greenhouse issues MIXED-case codes, and an
    # uppercase-only token pattern never produced a candidate - the best
    # scorer output for this email was Greenhouse's zip code, at 3.
    ("Security code for your application to Lyft",
     "Hi Abdul,\nCopy and paste this code into the security code field on "
     "your application:\nN6Ek26wS\nAfter you enter the code, resubmit your "
     "application.\n(c) 2026 Greenhouse\n18 West 18th Street, 11th Floor, "
     "New York, NY 10011, USA",
     "Greenhouse <no-reply@us.greenhouse-mail.io>", "N6Ek26wS"),
]

# These must return None. Each is a shape that fooled an earlier iteration.
NEGATIVE = [
    ("Your order 4190220 has shipped",
     "Tracking number 992481. Estimated delivery Tuesday.", "ship@amazon.com"),
    ("Receipt from Acme",
     "Order 583014 total $129.99. Thanks for your purchase!", "billing@acme.io"),
    ("Newsletter March 2024",
     "Since 1998 we have served 250000 customers. Unsubscribe here.", "news@x.com"),
    ("50% off everything",
     "Use promo code at checkout. Sale ends 2026. Save 4500 today.", "deals@shop.com"),
    ("Your invoice 771234 is ready",
     "Amount due 4500.00, account number 88213004.", "billing@utility.com"),
    ("Meeting notes",
     "Call me on 5551234 when you get a chance.", "colleague@work.com"),
    # Digits-on-the-end tokens are product names and promo codes, not
    # generated secrets; the mixed-case bonus must not resurrect them.
    ("Security update available",
     "Verify you are ready: iPhone17 ships with the update built in.",
     "news@apple.com"),
]


@pytest.mark.parametrize("subject,body,sender,expected", POSITIVE)
def test_finds_real_codes(subject, body, sender, expected):
    code, score, why = find_code(subject, body, sender)
    assert code == expected, "got %r (score %d, %s)" % (code, score, why)


@pytest.mark.parametrize("subject,body,sender", NEGATIVE)
def test_ignores_non_codes(subject, body, sender):
    code, score, why = find_code(subject, body, sender)
    assert code is None, "false positive %r (score %d, %s)" % (code, score, why)


def test_ambiguous_pair_returns_nothing():
    """Two equally-plausible codes must show nothing, not a coin flip."""
    code, _score, why = find_code(
        "Your verification code",
        "Your verification code is 111111. Your verification code is 222222.",
        "no-reply@x.com")
    assert code is None
    assert any("ambiguous" in w for w in why)


def test_html_noise_does_not_become_a_code():
    """Hex colours and tracking URLs are the classic false-positive source."""
    body = ('<html><head><style>.a{color:#4419af;background:#221100}</style></head>'
            '<body><p>Your verification code is 662451</p>'
            '<img src="https://t.acme.io/p?id=903221&u=558711"></body></html>')
    code, _score, _why = find_code("Acme", body, "no-reply@acme.io")
    assert code == "662451"


def test_strip_html_drops_style_and_urls():
    out = strip_html('<style>.x{color:#abc123}</style>hi <a href="http://a.b/c?d=1">x</a>')
    assert "abc123" not in out
    assert "http" not in out
    assert "hi" in out
