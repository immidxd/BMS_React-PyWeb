"""
Identity normalizer for client strong signals.

Goal: collapse equivalent forms of the same identity into a single canonical
string so that two ways of writing the same phone/facebook/instagram/telegram
match exactly. Used for dedup and partial UNIQUE indexes.

Examples:
  +38 (095) 123-45-67  →  '0951234567'
  095-123-45-67        →  '0951234567'
  380951234567         →  '0951234567'
  https://www.facebook.com/john.doe/   →  'facebook.com/john.doe'
  fb.com/john.doe?ref=x                →  'facebook.com/john.doe'
  @john_doe (instagram) →  'john_doe'
  https://t.me/john_doe →  'john_doe'
"""

import re
from typing import Optional


_DIGITS = re.compile(r'\D+')


# ── BLACKLISTS: системні / групові шляхи, які НЕ ідентифікують особу ────
# Будь-який facebook.com/groups/<id>, facebook.com/marketplace тощо НЕ є
# особистою сторінкою. Якщо нормалізатор поверне такий шлях як ідентифікатор
# — усі замовлення з посиланням на групу/маркетплейс зіллються в одного
# випадкового клієнта (bucket-катастрофа: див. клієнт #7924, 148 aliases).
_FB_SYSTEM_PATHS = frozenset({
    "groups", "marketplace", "pages", "watch", "events", "gaming",
    "login", "help", "share", "messages", "stories", "reel",
    "people", "search", "photo", "bookmarks", "notifications",
    "friends", "settings", "ads", "business", "fundraisers",
    "memories", "saved", "checkpoint", "policies", "terms",
    "privacy", "support", "developers", "video", "watchparty",
    "home", "feed", "lite", "dating",
})

_IG_SYSTEM_PATHS = frozenset({
    "explore", "reels", "reel", "p", "tv", "stories", "direct",
    "accounts", "web", "ads", "creator", "developer", "about",
    "press", "api", "blog", "jobs", "privacy", "terms", "locations",
    "directory", "challenge", "session", "legal", "fragment",
})

_TG_SYSTEM_PATHS = frozenset({
    "joinchat", "c", "share", "login", "addstickers", "addtheme",
    "proxy", "socks", "iv", "msg", "passport", "confirmphone",
    "setlanguage", "addlist", "boost", "contact", "support", "faq",
    "press", "blog", "apps", "android", "macos", "ios", "tdesktop",
    "tdwhitepaper", "privacy", "tos", "terms",
})


def normalize_phone(raw: Optional[str]) -> Optional[str]:
    """Normalize phone to last 10 digits in UA format (e.g. 0951234567).

    Handles: +380, 380, 0, country codes, separators, parens, dots.
    Returns None for empty / clearly invalid (< 7 digits).
    """
    if not raw:
        return None
    s = (raw or "").strip()
    if not s or s.startswith(("http://", "https://")):
        return None
    digits = _DIGITS.sub('', s)
    if len(digits) < 7:
        return None
    # Strip UA country prefixes
    if digits.startswith('380') and len(digits) >= 12:
        digits = digits[2:]  # drop '38' → '0951234567'
    elif digits.startswith('80') and len(digits) >= 11:
        digits = digits[1:]
    # Take last 10 (handles long international forms)
    if len(digits) > 10:
        digits = digits[-10:]
    return digits if len(digits) >= 9 else None


def _strip_url_prefix(s: str) -> str:
    s = s.strip().lower()
    s = re.sub(r'^https?://', '', s)
    s = re.sub(r'^www\.', '', s)
    s = s.split('?', 1)[0]  # drop query
    s = s.split('#', 1)[0]  # drop fragment
    s = s.rstrip('/')
    return s


def normalize_facebook(raw: Optional[str]) -> Optional[str]:
    """Normalize Facebook URL/handle → 'facebook.com/{handle}' lowercase.

    Accepts: full URL, fb.com/x, m.facebook.com/x, bare handle '@john.doe'.
    Drops profile.php?id= → 'facebook.com/profile.php?id={N}' kept (numeric IDs unique).
    """
    if not raw:
        return None
    s = _strip_url_prefix(raw)
    if not s:
        return None
    # Variants of the domain
    s = re.sub(r'^(m\.|web\.|business\.|mbasic\.|free\.|0\.)?(facebook|fb)\.com/', 'facebook.com/', s)
    if s.startswith('@'):
        s = 'facebook.com/' + s[1:]
    elif '/' not in s and '.' not in s:
        # bare handle
        s = 'facebook.com/' + s
    elif not s.startswith('facebook.com/'):
        # Some other URL — not facebook
        if 'facebook.com' not in s and 'fb.com' not in s:
            return None
    # profile.php?id=N — kept as 'facebook.com/profile.php?id=N' BUT we already
    # dropped query string above. Re-extract numeric id if present in original.
    pid_match = re.search(r'profile\.php\?id=(\d+)', raw, re.IGNORECASE)
    if pid_match:
        return f'facebook.com/profile.php?id={pid_match.group(1)}'
    # Strip path suffixes like /about, /posts, /posts/123
    parts = s.split('/', 2)
    if len(parts) >= 2:
        s = f'{parts[0]}/{parts[1]}'
    # ── Sanity guards: ВІДКИНУТИ системні / групові / надто короткі handle ─
    if not s.startswith('facebook.com/'):
        return None
    handle = s[len('facebook.com/'):]
    if not handle or len(handle) < 4:
        return None  # facebook.com/x не валідний user handle
    # bare 'profile.php' без id — це сторінка-заглушка, не особа
    if handle == 'profile.php' or handle.startswith('profile.php?'):
        return None
    if handle.lower() in _FB_SYSTEM_PATHS:
        return None  # /groups, /marketplace, /pages, /watch, ...
    return s


def normalize_instagram(raw: Optional[str]) -> Optional[str]:
    """Normalize Instagram → bare handle (lowercase, no @, no URL)."""
    if not raw:
        return None
    s = _strip_url_prefix(raw)
    if not s:
        return None
    if s.startswith('@'):
        s = s[1:]
    s = re.sub(r'^(m\.|www\.)?instagram\.com/', '', s)
    s = re.sub(r'^(m\.|www\.)?ig\.com/', '', s)
    s = s.split('/', 1)[0]
    s = s.strip()
    # Valid IG handle: alnum + . _ , 3-30 chars (Instagram min handle = 3 chars).
    # Reject system paths like /p/<post>, /reels/<id>, /explore тощо.
    if not s or not re.match(r'^[a-z0-9._]{3,30}$', s):
        return None
    if s in _IG_SYSTEM_PATHS:
        return None
    return s


def normalize_telegram(raw: Optional[str]) -> Optional[str]:
    """Normalize Telegram → bare handle (lowercase, no @, no URL)."""
    if not raw:
        return None
    s = _strip_url_prefix(raw)
    if not s:
        return None
    if s.startswith('@'):
        s = s[1:]
    s = re.sub(r'^t\.me/', '', s)
    s = re.sub(r'^telegram\.me/', '', s)
    s = s.split('/', 1)[0]
    s = s.split('?', 1)[0]
    s = s.strip()
    if not s or not re.match(r'^[a-z0-9_]{4,32}$', s):
        return None
    # Telegram public usernames cannot start with a digit, can't be all-digit,
    # та не повинні бути системними шляхами (/joinchat, /c, /share тощо).
    if s in _TG_SYSTEM_PATHS:
        return None
    if s.startswith('+'):  # invite-link форма t.me/+abc — це не handle
        return None
    return s


def normalize_email(raw: Optional[str]) -> Optional[str]:
    """Normalize email — lowercase + strip + drop trailing dots."""
    if not raw:
        return None
    s = raw.strip().lower()
    if not s or '@' not in s or s.startswith('@'):
        return None
    return s


def normalize_all(client_dict: dict) -> dict:
    """Compute all *_normalized fields from a client-like dict. Returns NEW dict."""
    out = dict(client_dict)
    out['phone_normalized'] = normalize_phone(client_dict.get('phone_number'))
    out['facebook_normalized'] = normalize_facebook(client_dict.get('facebook'))
    out['instagram_normalized'] = normalize_instagram(client_dict.get('instagram'))
    out['telegram_normalized'] = normalize_telegram(client_dict.get('telegram'))
    return out
