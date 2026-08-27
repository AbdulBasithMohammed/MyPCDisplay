#!/usr/bin/env python
"""Exercise OTP detection and delivery without waiting for a real code.

    venv/Scripts/python.exe tools/otp_selftest.py
        Score a corpus of realistic messages and show why each scored as it did.

    venv/Scripts/python.exe tools/otp_selftest.py --text "Your code is 419022"
        Score one message you paste in. Use --subject to set the subject.

    venv/Scripts/python.exe tools/otp_selftest.py --imap
        Check the IMAP host, credentials and IDLE support in services.yaml.
        Reads nothing but the mailbox name and capability list.

    venv/Scripts/python.exe tools/otp_selftest.py --publish 419022 --sender Acme
        Write a fake notification to the state file. If the deck is running it
        will NOT switch to it - only the supervisor does that - but
        preview-theme.py DeckOTP will render it.
"""
import argparse
import os
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from library.sensors import otp  # noqa: E402

# (subject, body, sender, should_fire)
CORPUS = [
    ("Your verification code is 419022", "", "no-reply@github.com", True),
    ("482913 is your Instagram code", "", "security@mail.instagram.com", True),
    ("Sign in to Acme", "Your one-time code is 823 114.", "accounts@acme.io", True),
    ("", "Your two-factor authentication code is 77213908.", "auth@bank.com", True),
    ("Acme security", "Use passcode 5521 to finish signing in.", "x@acme.io", True),
    ("Your order 4190220 has shipped",
     "Tracking number 992481. Delivery Tuesday.", "ship@amazon.com", False),
    ("Receipt from Acme",
     "Order 583014 total $129.99.", "billing@acme.io", False),
    ("50% off everything",
     "Use promo code SAVE20 at checkout. Sale ends 2026.", "deals@shop.com", False),
    ("Your invoice 771234 is ready",
     "Amount due 4500.00, account number 88213004.", "billing@utility.com", False),
    ("Meeting notes", "Call me on 5551234.", "colleague@work.com", False),
]


def run_corpus():
    failures = 0
    print("%-6s %-6s %-38s %s" % ("want", "got", "subject", "why"))
    print("-" * 100)
    for subject, body, sender, should in CORPUS:
        code, score, why = otp.find_code(subject, body, sender)
        ok = bool(code) == should
        failures += not ok
        print("%-6s %-6s %-38s %s" % (
            "FIRE" if should else "quiet",
            (code or "-"),
            (subject or "(no subject)")[:38],
            "%d: %s" % (score, ", ".join(why))))
        if not ok:
            print("       ^^ MISMATCH")
    print("-" * 100)
    print("%d/%d correct" % (len(CORPUS) - failures, len(CORPUS)))
    return failures


def run_text(subject, text, sender):
    code, score, why = otp.find_code(subject, text, sender)
    print("subject : %r" % subject)
    print("sender  : %r" % sender)
    print("code    : %s" % (code or "(none)"))
    print("score   : %d (threshold %d)" % (score, otp.MIN_SCORE))
    print("why     : %s" % ", ".join(why))
    return 0 if code else 1


def check_account(cfg):
    """Connect, log in and select one mailbox. Reads no messages."""
    import imaplib

    label = cfg.get("label") or cfg.get("user")
    host = cfg.get("host") or "imap.gmail.com"
    port = int(cfg.get("port") or 993)
    folder = cfg.get("folder") or "INBOX"

    if not cfg.get("user") or not cfg.get("app_password"):
        print("[%s] no user / app_password set" % label)
        return 1

    print("[%s] connecting to %s:%d ..." % (label, host, port))
    try:
        imap = imaplib.IMAP4_SSL(host, port)
    except Exception as e:
        print("[%s] FAILED to connect: %s" % (label, e))
        return 1
    try:
        imap.login(cfg["user"], cfg["app_password"])
        print("[%s] login OK as %s" % (label, cfg["user"]))
        typ, data = imap.select(folder)
        if typ != "OK":
            print("[%s] could not select %r: %s" % (label, folder, data))
            return 1
        print("[%s] selected %s (%s messages)"
              % (label, folder, (data[0] or b"?").decode()))
        caps = (imap.capability()[1][0] or b"").decode(errors="replace")
        print("[%s] IDLE supported: %s"
              % (label, "yes" if "IDLE" in caps else
                 "NO - will poll every %ds" % int(cfg.get("poll_seconds") or 15)))
        return 0
    except Exception as e:
        print("[%s] FAILED: %s" % (label, e))
        return 1
    finally:
        try:
            imap.logout()
        except Exception:
            pass


def run_imap():
    from library.sensors import otp_sources

    mail_cfg = (otp.load_services().get("otp") or {}).get("email") or {}
    configured = list(otp_sources.accounts(mail_cfg))
    if not configured:
        print("services.yaml has no otp.email account configured.")
        return 1

    # Every mailbox is checked even after one fails, so a single bad password
    # does not hide a second one behind it.
    failures = sum(check_account(a) for a in configured)
    print("-" * 60)
    print("%d/%d mailboxes OK" % (len(configured) - failures, len(configured)))
    return 1 if failures else 0


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--text", help="score this message body")
    ap.add_argument("--subject", default="", help="subject to go with --text")
    ap.add_argument("--sender", default="", help="sender to go with --text/--publish")
    ap.add_argument("--imap", action="store_true", help="verify IMAP settings")
    ap.add_argument("--publish", metavar="CODE",
                    help="write a fake notification to the state file")
    args = ap.parse_args()

    if args.publish:
        state = otp.publish(args.publish, args.sender or "Self test", "email", 30)
        print("wrote %s" % otp.STATE_FILE)
        print(state)
        return 0
    if args.imap:
        return run_imap()
    if args.text:
        return run_text(args.subject, args.text, args.sender)
    return 1 if run_corpus() else 0


if __name__ == "__main__":
    sys.exit(main())
