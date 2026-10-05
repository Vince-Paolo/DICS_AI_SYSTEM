"""Emergency hotline configuration for the citizen Emergency Assistance page.

The numbers a citizen dials are safety-critical, so nothing in this module
invents one. Each number comes from an environment variable that the
deployment fills in with the verified official number from the LGU / CDRRMO /
EOC:

    HOTLINE_GENERAL   national emergency number (defaults to 911)
    HOTLINE_MEDICAL   medical / ambulance
    HOTLINE_POLICE    police / security
    HOTLINE_FIRE      fire and rescue
    HOTLINE_CDRRMO    CDRRMO / EOC office line(s), separated by |
    HOTLINE_CITY_HEALTH, HOTLINE_CTMO, HOTLINE_SAN_PABLO_PNP,
    HOTLINE_SAN_PABLO_FIRE, HOTLINE_BARANGAY_RADIO_CONTROL, HOTLINE_MERALCO
                      additional San Pablo City contacts; multiple numbers
                      are separated by |

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


def normalize_numbers(raw):
    """Normalize a pipe-separated list, ignoring invalid and duplicate values."""
    if raw is None:
        return []
    numbers = []
    for candidate in str(raw).split('|'):
        number = normalize_number(candidate)
        if number and number not in numbers:
            numbers.append(number)
    return numbers


def _from_env_numbers(name):
    return normalize_numbers(os.environ.get(name))


def _from_env(name):
    numbers = _from_env_numbers(name)
    return numbers[0] if numbers else None


def get_general_hotline():
    """The national emergency number as a ``{'display', 'dial'}`` dict."""
    number = _from_env('HOTLINE_GENERAL') or normalize_number(DEFAULT_GENERAL_HOTLINE)
    return {'display': number[0], 'dial': number[1]}


def build_hotline_services():
    """Build translated emergency and local contact cards for the assistance page.

    Labels are translated for the current request locale, so this must be
    called inside a request. Each item has ``key``, ``emoji``, ``label``,
    ``description``, ``display`` and ``dial``; ``dial`` is ``None`` when the
    service has no usable number.
    """
    def resolve(env_name):
        return _from_env_numbers(env_name)

    definitions = (
        ('medical', '\U0001F691', _('Medical Emergency'),
         _('Ambulance and medical assistance'), 'HOTLINE_MEDICAL'),
        ('police', '\U0001F693', _('Police / Security'),
         _('Police emergency assistance'), 'HOTLINE_POLICE'),
        ('fire', '\U0001F525', _('Fire and Rescue'),
         _('Fire and rescue assistance'), 'HOTLINE_FIRE'),
        ('disaster', '\U0001F30A', _('Disaster Response'),
         _('CDRRMO / Emergency Operations Center'), 'HOTLINE_CDRRMO'),
        ('city_health', '\U0001F3E5', _('City Health Office (SPC - CHO)'),
         _('City health and ambulance coordination'), 'HOTLINE_CITY_HEALTH'),
        ('city_traffic', '\U0001F6A6', _('City Traffic Management Office (CTMO)'),
         _('Traffic management and assistance'), 'HOTLINE_CTMO'),
        ('san_pablo_pnp', '\U0001F6E1', _('San Pablo PNP'),
         _('Police assistance in San Pablo City'), 'HOTLINE_SAN_PABLO_PNP'),
        ('san_pablo_fire', '\U0001F692', _('San Pablo Fire Station (BFP)'),
         _('Fire and rescue assistance in San Pablo City'), 'HOTLINE_SAN_PABLO_FIRE'),
        ('barangay_radio', '\U0001F4E1', _('Barangay Radio Control'),
         _('Barangay emergency radio coordination'), 'HOTLINE_BARANGAY_RADIO_CONTROL'),
        ('meralco', '\U000026A1', _('Meralco'),
         _('Electric service emergencies and outage reports'), 'HOTLINE_MERALCO'),
    )

    services = []
    for key, emoji, label, description, env_name in definitions:
        contacts = [
            {'display': display, 'dial': dial}
            for display, dial in resolve(env_name)
        ]
        primary = contacts[0] if contacts else {'display': None, 'dial': None}
        services.append({
            'key': key,
            'emoji': emoji,
            'label': label,
            'description': description,
            'display': primary['display'],
            'dial': primary['dial'],
            'contacts': contacts,
        })
    return services


def cdrrmo_hotline_configured():
    return bool(_from_env_numbers('HOTLINE_CDRRMO'))
