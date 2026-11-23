"""Shared conservative validation for low-value color and size terms."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable


_WORD_RE = re.compile(r"[^\W_]+", re.UNICODE)

ENGLISH_COLOR_WORDS = frozenset({
    "aqua", "beige", "black", "blue", "brown", "burgundy", "champagne", "coral", "cream",
    "cyan", "fuchsia", "gold", "gray", "green", "grey", "indigo", "ivory",
    "khaki", "lavender", "lime", "magenta", "maroon", "mustard", "navy",
    "olive", "orange", "pink", "purple", "red", "silver", "tan", "teal",
    "turquoise", "violet", "white", "yellow",
})

MEXICAN_SPANISH_COLOR_WORDS = frozenset({
    "amarillo", "anaranjado", "azul", "beige", "blanco", "cafe", "caqui",
    "celeste", "cian", "coral", "crema", "dorado", "fucsia", "gris", "indigo",
    "lila", "marron", "morado", "mostaza", "naranja", "negro", "oliva",
    "plateado", "purpura", "rojo", "rosa", "rosado", "turquesa", "verde", "vino",
    "violeta",
})

_COLORS_BY_LANGUAGE = {
    "en-US": ENGLISH_COLOR_WORDS,
    "es-MX": MEXICAN_SPANISH_COLOR_WORDS,
}
MARKETPLACE_LANGUAGES = tuple(_COLORS_BY_LANGUAGE)
_SIZE_WORDS_BY_LANGUAGE = {
    "en-US": frozenset({"petite", "tall", "curvy", "maternity"}),
    "es-MX": frozenset({"chica", "chico", "curvy", "extragrande", "grande", "maternal", "petite", "unitalla"}),
}
_SIZE_PHRASES_BY_LANGUAGE = {
    "en-US": (("one", "size"), ("plus", "size")),
    "es-MX": (
        ("plus", "size"), ("talla", "plus"), ("talla", "grande"),
        ("talla", "unica"), ("tamano", "unico"),
    ),
}
_ALL_COLOR_WORDS = frozenset().union(*_COLORS_BY_LANGUAGE.values())
_ALL_SIZE_WORDS = frozenset().union(*_SIZE_WORDS_BY_LANGUAGE.values())
_ALL_SIZE_PHRASES = tuple(
    phrase
    for phrases in _SIZE_PHRASES_BY_LANGUAGE.values()
    for phrase in phrases
)
_SOURCE_COLOR_MODIFIERS = frozenset({
    "bright", "dark", "light", "solid", "brillante", "claro", "oscuro", "solido",
})
_SOURCE_COLOR_CONNECTORS = frozenset({
    "and", "con", "de", "del", "e", "o", "or", "with", "y",
})
_LETTER_SIZE_RE = re.compile(r"(?<![\w-])(?:xxs|xs|s|m|l|xl|xxl|xxxl|2xl|3xl|4xl|5xl)(?![\w-])", re.IGNORECASE)
_NUMERIC_SIZE_RE = re.compile(
    r"\b(?:size|talla|tallas|tamano|tamanos)\s*(?:\d{1,3}|xxs|xs|s|m|l|xl|xxl|xxxl|2xl|3xl|4xl|5xl)\b",
    re.IGNORECASE,
)


def _normalized_words(value: str) -> tuple[str, ...]:
    normalized = unicodedata.normalize("NFKD", value).casefold()
    normalized = "".join(character for character in normalized if not unicodedata.combining(character))
    return tuple(_WORD_RE.findall(normalized))


def _contains_phrase(words: tuple[str, ...], phrases: Iterable[tuple[str, ...]]) -> bool:
    for phrase in phrases:
        width = len(phrase)
        if any(words[index:index + width] == phrase for index in range(len(words) - width + 1)):
            return True
    return False


def contains_color_or_size(text: str, language: str, source_color: str = "") -> bool:
    """Return whether text has a forbidden marketplace color or size reference.

    The validator is deliberately conservative.  Unknown marketplace-language
    labels fail closed, while source-color modifiers such as ``dark`` remain
    harmless until paired with an actual color word.
    """
    if not isinstance(text, str) or not isinstance(language, str) or not isinstance(source_color, str):
        raise TypeError("text, language, and source_color must be strings")
    if language not in _COLORS_BY_LANGUAGE:
        return True
    if any(unicodedata.category(character) == "Cf" for character in text):
        return True

    words = _normalized_words(text)
    word_set = set(words)
    if word_set & _ALL_COLOR_WORDS:
        return True
    if word_set & _ALL_SIZE_WORDS:
        return True
    if _contains_phrase(words, _ALL_SIZE_PHRASES):
        return True

    normalized_text = " ".join(words)
    if _LETTER_SIZE_RE.search(normalized_text) or _NUMERIC_SIZE_RE.search(normalized_text):
        return True

    source_words = frozenset(
        word for word in _normalized_words(source_color)
        if word not in _SOURCE_COLOR_MODIFIERS
        and word not in _SOURCE_COLOR_CONNECTORS
    )
    return bool(word_set & source_words)
