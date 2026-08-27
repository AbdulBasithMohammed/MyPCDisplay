#!/usr/bin/env python
"""Watch Windows toast notifications, reporting Phone Link ones in full.

Diagnostic for the OTP screen. Phone Link with an iPhone mirrors notifications
over Bluetooth (ANCS), which is a much thinner channel than the Android path -
whether SMS *body text* actually arrives is the thing this measures.

Other apps' toasts are counted but not printed, so a diagnostic run does not
dump unrelated notification content into a log.

    venv/Scripts/python.exe tools/watch_notifications.py [seconds]
"""
import asyncio
import sys
import time

PHONE_LINK = "Microsoft.YourPhone_8wekyb3d8bbwe"


async def main(duration):
    from winrt.windows.ui.notifications.management import (
        UserNotificationListener, UserNotificationListenerAccessStatus)
    from winrt.windows.ui.notifications import NotificationKinds

    listener = UserNotificationListener.current
    if await listener.request_access_async() != UserNotificationListenerAccessStatus.ALLOWED:
        print("DENIED: this process may not read notifications")
        return

    # Toasts already in the Action Center are recorded but not reported: only
    # what arrives during the run is evidence about the phone link.
    seen = {n.id for n in
            await listener.get_notifications_async(NotificationKinds.TOAST)}
    print("Watching for %ds (%d existing toasts ignored). "
          "Send yourself a text now." % (duration, len(seen)))
    baseline = len(seen)
    others = 0
    deadline = time.time() + duration

    while time.time() < deadline:
        for n in await listener.get_notifications_async(NotificationKinds.TOAST):
            if n.id in seen:
                continue
            seen.add(n.id)
            try:
                fam = n.app_info.package_family_name
                name = n.app_info.display_info.display_name
            except Exception:
                fam, name = "", "?"
            if fam != PHONE_LINK:
                others += 1
                continue
            texts = [el.text for el in
                     n.notification.visual.bindings[0].get_text_elements()]
            print("[%s] %s" % (time.strftime("%H:%M:%S"), name))
            for i, t in enumerate(texts):
                print("    text[%d]: %r" % (i, t))
        await asyncio.sleep(1)

    phone = len(seen) - baseline - others
    print("done: %d new Phone Link toast(s), %d new from other apps"
          % (phone, others))
    if not phone:
        print("No Phone Link toast arrived. If you did send a text, iPhone "
              "notification mirroring is not delivering it to Windows.")


asyncio.run(main(int(sys.argv[1]) if len(sys.argv) > 1 else 120))
