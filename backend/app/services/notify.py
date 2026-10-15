"""Outbound notifications used by auth. Swap the bodies for real providers.

- OTP: send via SMS provider or a WhatsApp authentication template (M4).
- Password reset: send via email provider.
In development both are written to the log so you can copy the code/link.
"""

import logging

logger = logging.getLogger("pinesphere.notify")


def send_otp(mobile: str, code: str) -> None:
    logger.warning("OTP for %s: %s (dev only - connect SMS/WhatsApp provider)", mobile, code)


def send_password_reset(email: str, token: str) -> None:
    logger.warning("Password reset token for %s: %s (dev only - connect email provider)", email, token)
