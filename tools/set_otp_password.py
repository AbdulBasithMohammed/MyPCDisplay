#!/usr/bin/env python
"""Set an OTP mailbox app password without it passing through anything else.

    venv/Scripts/python.exe tools/set_otp_password.py
    venv/Scripts/python.exe tools/set_otp_password.py --account reach

The value is read with getpass, so it is never echoed to the terminal, never
lands in shell history, and never appears in a transcript. It is written
straight into services.yaml and nothing here prints it back.

services.yaml is edited line by line rather than round-tripped through a YAML
parser, because a parser would discard every comment in the file.
"""
import argparse
import getpass
import io
import os
import re
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from library.sensors import otp  # noqa: E402

ACCOUNT_START = re.compile(r"^(\s*)-\s+(label|user):", re.M)


def find_accounts(lines):
    """Locate each account block: (label, user, index_of_app_password_line)."""
    found = []
    start = None
    for i, line in enumerate(lines):
        if ACCOUNT_START.match(line):
            start = i
            found.append({"start": i, "label": None, "user": None, "pw_line": None})
        if start is None or not found:
            continue
        current = found[-1]
        m = re.match(r"^\s*-?\s*label:\s*(.+?)\s*$", line)
        if m and current["label"] is None:
            current["label"] = m.group(1).strip().strip('"\'')
        m = re.match(r"^\s*-?\s*user:\s*(.+?)\s*$", line)
        if m and current["user"] is None:
            current["user"] = m.group(1).strip().strip('"\'')
        if re.match(r"^\s*app_password:", line) and current["pw_line"] is None:
            current["pw_line"] = i
    return [a for a in found if a["pw_line"] is not None]


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--account", help="label or address to set; prompts if omitted")
    args = ap.parse_args()

    path = otp.SERVICES_FILE
    if not os.path.exists(path):
        print("No services.yaml at %s" % path)
        return 1

    text = io.open(path, encoding="utf-8").read()
    lines = text.splitlines(keepends=True)
    accounts = find_accounts(lines)
    if not accounts:
        print("No otp.email accounts found in %s" % path)
        return 1

    if args.account:
        wanted = args.account.strip().lower()
        matches = [a for a in accounts
                   if wanted in ((a["label"] or "").lower(), (a["user"] or "").lower())]
        if not matches:
            print("No account matching %r. Known: %s" % (
                args.account, ", ".join(a["label"] or a["user"] or "?" for a in accounts)))
            return 1
        target = matches[0]
    else:
        print("Accounts in services.yaml:")
        for i, a in enumerate(accounts, 1):
            filled = lines[a["pw_line"]].split(":", 1)[1].strip().strip('"\'')
            print("  %d) %-10s %-32s %s" % (
                i, a["label"] or "-", a["user"] or "-",
                "(password set)" if filled else "(empty)"))
        try:
            choice = int(input("Which account? ").strip())
        except (ValueError, EOFError, KeyboardInterrupt):
            print("\nCancelled.")
            return 1
        if not 1 <= choice <= len(accounts):
            print("Out of range.")
            return 1
        target = accounts[choice - 1]

    print("Setting the app password for %s." % (target["user"] or target["label"]))
    print("It will not be shown as you type.")
    try:
        secret = getpass.getpass("App password: ")
        confirm = getpass.getpass("Again to confirm: ")
    except (EOFError, KeyboardInterrupt):
        print("\nCancelled.")
        return 1

    if secret != confirm:
        print("They do not match. Nothing was changed.")
        return 1
    if not secret.strip():
        print("Empty. Nothing was changed.")
        return 1
    if '"' in secret:
        print("That contains a double quote, which this writer cannot quote "
              "safely. Set it by hand instead.")
        return 1

    indent = re.match(r"^(\s*)", lines[target["pw_line"]]).group(1)
    lines[target["pw_line"]] = '%sapp_password: "%s"\n' % (indent, secret)
    io.open(path, "w", encoding="utf-8", newline="").write("".join(lines))

    # Length only - never the value itself.
    print("Wrote %d characters to %s for %s."
          % (len(secret), os.path.basename(path), target["label"] or target["user"]))
    print("Verify with: venv/Scripts/python.exe tools/otp_selftest.py --imap")
    return 0


if __name__ == "__main__":
    sys.exit(main())
