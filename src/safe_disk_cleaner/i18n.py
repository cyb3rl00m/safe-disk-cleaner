"""Translation support (gettext).

The compiled catalogues live next to this package, in ``locale/<lang>/LC_MESSAGES/``,
so the same code works from the source folder, the .deb, install.sh and the AppImage.
"""
from __future__ import annotations

import gettext as _gettext
import os

DOMAIN = "safe-disk-cleaner"

_translation: _gettext.NullTranslations | None = None


def localedir() -> str:
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "locale")


def install(languages: list[str] | None = None) -> None:
    """Load the catalogue for the current (or given) language."""
    global _translation
    try:
        _translation = _gettext.translation(
            DOMAIN, localedir(), languages=languages, fallback=True)
    except Exception:  # pragma: no cover - a broken catalogue must never stop the app
        _translation = None


def _(message: str) -> str:
    return _translation.gettext(message) if _translation is not None else message


def ngettext(singular: str, plural: str, n: int) -> str:
    if _translation is not None:
        return _translation.ngettext(singular, plural, n)
    return singular if n == 1 else plural


# Marker for strings translated at use time (e.g. Enum values); xgettext picks
# these up with --keyword=N_.
def N_(message: str) -> str:
    return message
