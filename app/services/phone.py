"""Phone number normalisation — one source of truth used everywhere.

Different parts of the stack hand us numbers in different shapes:
  '+919385763994', 'whatsapp:+919385763994', '919385763994', '09385763994',
  ' 9385763994 ', '93857 63994', etc.

If we don't normalise on every read and write, the same person creates multiple
customer rows. So: one function, used at every entry point.
"""

import re
from typing import Optional

_WHATSAPP_PREFIX = "whatsapp:"


def normalize_phone(raw: Optional[str], default_country: str = "+91") -> str:
    """Return a canonical '+<digits>' form, or '' if nothing usable was given.

    - Strips 'whatsapp:' prefix, spaces, dashes, parens.
    - If the result has no leading '+' and no country code, prepends `default_country`.
    - Drops a leading '0' on Indian numbers (10-digit mobile starting with 0).
    """
    if not raw:
        return ""
    s = str(raw).strip()
    if s.startswith(_WHATSAPP_PREFIX):
        s = s[len(_WHATSAPP_PREFIX):]

    has_plus = s.startswith("+")
    digits = re.sub(r"\D", "", s)
    if not digits:
        return ""

    if has_plus:
        return f"+{digits}"

    # No '+' — guess the country. India is our home market: a 10-digit number, or
    # an 11-digit number starting with '0', maps to +91. A 12-digit starting with
    # '91' already includes the country code.
    if default_country == "+91":
        if len(digits) == 10:
            return f"+91{digits}"
        if len(digits) == 11 and digits.startswith("0"):
            return f"+91{digits[1:]}"
        if len(digits) == 12 and digits.startswith("91"):
            return f"+{digits}"
    # Fallback: assume the digits already include the country code.
    return f"+{digits}"
