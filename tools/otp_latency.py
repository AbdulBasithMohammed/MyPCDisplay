#!/usr/bin/env python
"""Measure where the delay actually is between a mail arriving and the code.

Everything after IDLE wakes is ours to optimise. Everything before it is
Gmail deciding when to tell us, which no change on this side can touch - so
measure that first, before spending effort on the rest.

    venv/Scripts/python.exe tools/otp_latency.py            # first account
    venv/Scripts/python.exe tools/otp_latency.py --account reach

Then send yourself a mail. Reports, for the next message to arrive:

    server Date -> IDLE wake     how long Gmail sat on it (not ours)
    IDLE wake   -> SEARCH done   our first round trip
    SEARCH      -> FETCH done    our second
    FETCH       -> detected      scoring, which is local and ~free

The panel switch is a further ~1.8s on top and is measured separately; see
the switch-cost table in DECK.md.
"""
import argparse
import email
import email.utils
import imaplib
import os
import sys
import time

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from library.sensors import otp, otp_sources  # noqa: E402


def measure(acc, timeout):
    host, port = acc.get("host") or "imap.gmail.com", int(acc.get("port") or 993)
    folder = acc.get("folder") or "INBOX"

    m = imaplib.IMAP4_SSL(host, port)
    m.login(acc["user"], acc["app_password"])
    m.select(folder)
    typ, data = m.uid("SEARCH", None, "ALL")
    last = int(data[0].split()[-1]) if (typ == "OK" and data and data[0]) else 0

    print("[%s] watching %s from uid %d - send yourself a mail now "
          "(%ds window)" % (acc.get("label") or acc["user"], folder, last, timeout))

    # Same IDLE implementation the watcher uses, so this measures the real
    # thing rather than a second copy that can drift from it.
    try:
        got_mail = otp_sources.idle_wait(m, timeout)
    except Exception as e:
        print("IDLE failed: %s" % e)
        return 1
    woke = time.time()
    if not got_mail:
        print("nothing arrived within %ds" % timeout)
        return 1

    t_search = time.time()
    typ, data = m.uid("SEARCH", None, "UID %d:*" % (last + 1))
    uids = [u for u in (data[0].split() if data and data[0] else []) if int(u) > last]
    t_search_done = time.time()
    if not uids:
        print("woke, but no new uid - probably a flag change, not a new message")
        return 1

    typ, data = m.uid("FETCH", uids[-1], "(BODY.PEEK[]<0.16384>)")
    t_fetch_done = time.time()
    msg = email.message_from_bytes(data[0][1])

    subject = otp_sources._decode_header(msg.get("Subject"))
    sender = otp_sources._decode_header(msg.get("From"))
    code, score, why = otp.find_code(subject, otp_sources._body_text(msg), sender)
    t_detected = time.time()

    sent = None
    try:
        sent = email.utils.parsedate_to_datetime(msg.get("Date")).timestamp()
    except Exception:
        pass

    print()
    print("subject: %r" % subject[:70])
    print("code   : %s (score %d: %s)" % (code or "(none)", score, ", ".join(why)))
    print()
    if sent:
        print("  server Date -> IDLE wake   %6.0f ms   <- Gmail's push delay, not ours" %
              ((woke - sent) * 1000))
    print("  IDLE wake   -> SEARCH done %6.0f ms" % ((t_search_done - t_search) * 1000))
    print("  SEARCH      -> FETCH done  %6.0f ms" % ((t_fetch_done - t_search_done) * 1000))
    print("  FETCH       -> detected    %6.0f ms" % ((t_detected - t_fetch_done) * 1000))
    print("  ------------------------------------")
    print("  ours, total                %6.0f ms" % ((t_detected - woke) * 1000))
    if sent:
        print("  wall clock, Date to code   %6.0f ms" % ((t_detected - sent) * 1000))
    try:
        m.logout()
    except Exception:
        pass
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--account", help="label or address; defaults to the first")
    ap.add_argument("--timeout", type=int, default=180, help="seconds to wait")
    args = ap.parse_args()

    mail_cfg = (otp.load_services().get("otp") or {}).get("email") or {}
    configured = list(otp_sources.accounts(mail_cfg))
    if not configured:
        print("No otp.email account configured.")
        return 1
    if args.account:
        wanted = args.account.strip().lower()
        configured = [a for a in configured
                      if wanted in ((a.get("label") or "").lower(),
                                    (a.get("user") or "").lower())] or configured
    return measure(configured[0], args.timeout)


if __name__ == "__main__":
    sys.exit(main())
