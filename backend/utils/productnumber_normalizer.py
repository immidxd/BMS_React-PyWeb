"""
Canonical normalization for ``products.productnumber``.

Why this exists
───────────────
Historically the parser and various manual edits produced inconsistent forms
of the same product number:

  • ``3009``     ← bare digits
  • ``#3009``    ← hash-prefixed
  • ``Ф3009``    ← letter-prefixed (Ф/Р/Т/etc — manufacturer/series marker)
  • ``#Ф3009``   ← canonical: hash + letter
  • ``ф3009``    ← lowercase letter
  • ``# Ф3009``  ← stray whitespace

This drove a chain of bugs: the publications relink couldn't pick one
canonical product when two rows existed with different forms, sold-status
checks missed counterparts, and the products table accumulated 294 true
duplicates that we just merged.

Going forward, EVERY write path (parser, /products POST/PUT, manual import
scripts) MUST pass the productnumber through :func:`normalize` before
persisting. Lookups should canonicalize the search term the same way, then
rely on equality — no more ``LIKE '%X%'`` and no more "try with and without
the hash" branching.

Reading rule
────────────
When matching telegram_posts.product_number_raw against products, also
normalize on the fly: ``normalize(raw)`` for the post side, exact match
against ``products.productnumber`` for the product side. The two forms
collapse to the same key.
"""

from __future__ import annotations
import re

# Letter prefixes used in this catalogue. Add new ones here as needed.
# Case-folded comparison; canonical output preserves the uppercase form
# the source data overwhelmingly uses (Ф, Р, etc.).
KNOWN_LETTER_PREFIXES = ("Ф", "Р", "Т", "У", "Ш", "Н", "З", "Л", "А", "К")

# Cyrillic↔Latin homoglyph mapping. Catalogue prefixes are all Cyrillic, but
# manual entry sometimes types a Latin look-alike ("T642" замість "Т642").
# We canonicalize ALL homoglyphs to their Cyrillic equivalent so the two
# forms collapse to one key everywhere.
_HOMOGLYPH_TO_CYR = str.maketrans({
    # uppercase Latin → Cyrillic
    "A": "А", "B": "В", "C": "С", "E": "Е", "H": "Н", "I": "І",
    "K": "К", "M": "М", "O": "О", "P": "Р", "T": "Т", "X": "Х", "Y": "У",
    # lowercase Latin → Cyrillic
    "a": "а", "b": "в", "c": "с", "e": "е", "h": "н", "i": "і",
    "k": "к", "m": "м", "o": "о", "p": "р", "t": "т", "x": "х", "y": "у",
})

_WHITESPACE_RE = re.compile(r"\s+")


def normalize(raw: str | None) -> str | None:
    """Return the canonical form of a product number.

    Rules:
      1. ``None`` / empty / whitespace-only → ``None``.
      2. Strip whitespace anywhere inside the string.
      3. Strip leading ``#`` (we'll re-add exactly one).
      4. Upper-case the leading letter prefix if it matches
         :data:`KNOWN_LETTER_PREFIXES` (case-insensitive). Digits and any
         trailing modifiers (e.g. ``-2``) are preserved as-is.
      5. Re-attach a single ``#`` prefix.

    Examples
    ────────
        >>> normalize("3009")
        '#3009'
        >>> normalize("#3009")
        '#3009'
        >>> normalize("Ф3009")
        '#Ф3009'
        >>> normalize("ф3009")
        '#Ф3009'
        >>> normalize("# Ф3009 ")
        '#Ф3009'
        >>> normalize("#Ф3009-2")
        '#Ф3009-2'
        >>> normalize(None)
        >>> normalize("")
    """
    if raw is None:
        return None
    s = _WHITESPACE_RE.sub("", raw)
    if not s:
        return None
    # Drop any number of leading hashes — we re-add exactly one.
    s = s.lstrip("#")
    if not s:
        return None
    # Fold Latin homoglyphs → Cyrillic so "T642" and "Т642" collapse.
    s = s.translate(_HOMOGLYPH_TO_CYR)
    # Upper-case the leading letter if it's one of the known prefixes.
    first = s[0]
    if first.upper() in KNOWN_LETTER_PREFIXES:
        s = first.upper() + s[1:]
    return "#" + s


def lookup_variants(raw: str | None) -> list[str]:
    """Return every plausible stored form of ``raw`` for backward-compatible
    SELECT queries that still need to match legacy rows. Once all writes go
    through :func:`normalize` and a one-shot backfill runs, callers can drop
    this and use ``productnumber = normalize(raw)`` directly.
    """
    canon = normalize(raw)
    if canon is None:
        return []
    bare = canon[1:]  # strip the canonical '#'
    variants = {canon, bare}
    # Lowercase letter prefix variant (legacy data)
    if bare and bare[0].upper() in KNOWN_LETTER_PREFIXES:
        lower = bare[0].lower() + bare[1:]
        variants.add("#" + lower)
        variants.add(lower)
    return sorted(variants)
