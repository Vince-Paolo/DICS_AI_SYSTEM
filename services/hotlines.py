"""Emergency hotline configuration for the citizen Emergency Assistance page.

The numbers a citizen dials are safety-critical, so nothing in this module
invents one. Each number comes from an environment variable that the
deployment fills in with the verified official number from the LGU / CDRRMO /
EOC:

    HOTLINE_GENERAL   national emergency number (defaults to 911)
    HOTLINE_MEDICAL   medical / ambulance      (falls back to HOTLINE_GENERAL)
    HOTLINE_POLICE    police / security        (falls back to HOTLINE_GENERAL)
    HOTLINE_FIRE      fire and rescue          (falls back to HOTLINE_GENERAL)
    HOTLINE_CDRRMO    CDRRMO / EOC office line (NO fallback: the office number
                      is specific to the deployment, so an unset value shows
                      the card as "not available" instead of a wrong number)

A value that is not a plausible phone number is treated as unset.
"""
import os
import re

from flask_babel import gettext as _

DEFAULT_GENERAL_HOTLINE = '911'

# Digits plus the punctuation people normally type in a phone number.
_ALLOWED_CHARACTERS = re.compile(r'^[0-9+()\-.\s/]+$')
_MIN_DIGITS = 3    # "911"
_MAX_DIGITS = 15   # E.164 upper bound


def normalize_number(raw):
    """Return ``(display, dial)`` for a phone number string, or ``None``.

    ``display`` is the number as the operator typed it (shown on screen);
    ``dial`` is the sanitized value that goes after ``tel:`` (digits, with a
    leading ``+`` kept for international format).
    """
    if raw is None:
        return None
    display = str(raw).strip()
    if not display or not _ALLOWED_CHARACTERS.match(display):
        return None
    digits = re.sub(r'\D', '', display)
    if not (_MIN_DIGITS <= len(digits) <= _MAX_DIGITS):
        return None
    dial = ('+' if display.startswith('+') else '') + digits
    return display, dial


def _from_env(name):
    return normalize_number(os.environ.get(name))


def get_general_hotline():
    """The national emergency number as a ``{'display', 'dial'}`` dict."""
    number = _from_env('HOTLINE_GENERAL') or normalize_number(DEFAULT_GENERAL_HOTLINE)
    return {'display': number[0], 'dial': number[1]}


def build_hotline_services():
    """The four help categories shown on the Emergency Assistance page.

    Labels are translated for the current request locale, so this must be
    called inside a request. Each item has ``key``, ``emoji``, ``label``,
    ``description``, ``display`` and ``dial``; ``dial`` is ``None`` when the
    service has no usable number (only possible for the CDRRMO card).
    """
    general = get_general_hotline()

    def resolve(env_name, fall_back_to_general):
        number = _from_env(env_name)
        if number:
            return number
        if fall_back_to_general:
            return general['display'], general['dial']
        return None, None

    definitions = (
        ('medical', '\U0001F691', _('Medical Emergency'),
         _('Ambulance and medical assistance'), 'HOTLINE_MEDICAL', True),
        ('police', '\U0001F693', _('Police / Security'),
         _('Police emergency assistance'), 'HOTLINE_POLICE', True),
        ('fire', '\U0001F525', _('Fire and Rescue'),
         _('Fire and rescue assistance'), 'HOTLINE_FIRE', True),
        ('disaster', '\U0001F30A', _('Disaster Response'),
         _('CDRRMO / Emergency Operations Center'), 'HOTLINE_CDRRMO', False),
    )

    services = []
    for key, emoji, label, description, env_name, fall_back in definitions:
        display, dial = resolve(env_name, fall_back)
        services.append({
            'key': key,
            'emoji': emoji,
            'label': label,
            'description': description,
            'display': display,
            'dial': dial,
        })
    return services


def cdrrmo_hotline_configured():
    return _from_env('HOTLINE_CDRRMO') is not None
