"""Deterministic product visual profile validation."""
from __future__ import annotations
from collections.abc import Mapping as MappingABC, Sequence
from dataclasses import dataclass
from typing import Mapping
import re
import unicodedata
from enum import Enum

class Platform(str, Enum):
    MERCADO_MX = 'mercado-libre-mx'
    SHEIN_US = 'shein-us'
from .query_terms import contains_color_or_size

STYLES = {'boho': ('boho', 'boho'), 'casual': ('casual', 'casual'), 'party': ('fiesta', 'party'), 'formal': ('formal', 'formal'), 'hippie': ('hippie', 'hippie'), 'youthful': ('juvenil', 'youthful'), 'modern': ('moderno', 'modern'), 'beach': ('playero', 'beach'), 'religious': ('religioso', 'religious'), 'rock': ('rockero', 'rock'), 'romantic': ('romántico', 'romantic'), 'understated': ('sencillo', 'understated'), 'sexy': ('sexy', 'sexy'), 'simple': ('simple', 'simple'), 'traditional': ('tradicional', 'traditional'), 'unique': ('único', 'unique'), 'vintage': ('vintage', 'vintage'), 'elegant': ('elegante', 'elegant'), 'semiformal': ('semiformal', 'semi-formal'), 'feminine': ('femenino', 'feminine'), 'minimalist': ('minimalista', 'minimalist'), 'classic': ('clásico', 'classic'), 'luxury': ('lujoso', 'luxury'), 'streetwear': ('urbano', 'streetwear'), 'sporty': ('deportivo', 'sporty'), 'preppy': ('colegial', 'preppy'), 'mature': ('maduro', 'mature'), 'y2k': ('Y2K', 'Y2K'), 'western': ('vaquero', 'western'), 'gothic': ('gótico', 'gothic')}
SCENES = {'quinceanera': ('15 años', 'quinceañera'), 'baptism': ('bautizo', 'baptism'), 'wedding': ('boda', 'wedding'), 'casual': ('casual', 'casual'), 'cocktail': ('cóctel', 'cocktail'), 'everyday': ('diario', 'everyday'), 'party': ('fiesta', 'party'), 'mexican_party': ('fiesta mexicana', 'Mexican party'), 'balloon': ('globo', 'balloon'), 'graduation': ('graduación', 'graduation'), 'night': ('noche', 'night'), 'bridal': ('novia', 'bridal'), 'office': ('oficina', 'office'), 'first_communion': ('primera comunión', 'first communion'), 'work': ('trabajo', 'work'), 'business': ('negocios', 'business'), 'semiformal_event': ('evento semiformal', 'semi-formal event'), 'formal_event': ('evento formal', 'formal event'), 'wedding_guest': ('invitada de boda', 'wedding guest'), 'gala': ('gala', 'gala'), 'prom': ('baile de graduación', 'prom'), 'ceremony': ('ceremonia', 'ceremony'), 'religious_event': ('evento religioso', 'religious event'), 'night_out': ('salida nocturna', 'night out'), 'date': ('cita', 'date night'), 'dinner': ('cena', 'dinner'), 'club': ('club nocturno', 'club'), 'festival': ('festival', 'festival'), 'vacation': ('vacaciones', 'vacation'), 'beach': ('playa', 'beach'), 'resort': ('resort', 'resort'), 'travel': ('viaje', 'travel'), 'weekend': ('fin de semana', 'weekend'), 'school': ('escuela', 'school'), 'campus': ('universidad', 'campus'), 'outdoor': ('actividades al aire libre', 'outdoor'), 'workout': ('entrenamiento', 'workout'), 'home': ('casa', 'home'), 'loungewear': ('descanso en casa', 'loungewear')}
SELLING_POINTS = {'v_neck': ('escote V', 'V-neck'), 'round_neck': ('cuello redondo', 'round neck'), 'square_neck': ('escote cuadrado', 'square neck'), 'high_neck': ('cuello alto', 'high neck'), 'shirt_collar': ('cuello camisero', 'shirt collar'), 'halter_neck': ('cuello halter', 'halter neck'), 'off_shoulder': ('hombros descubiertos', 'off shoulder'), 'one_shoulder': ('un hombro', 'one shoulder'), 'strapless': ('sin tirantes', 'strapless'), 'sleeveless': ('sin mangas', 'sleeveless'), 'short_sleeve': ('manga corta', 'short sleeve'), 'long_sleeve': ('manga larga', 'long sleeve'), 'puff_sleeve': ('manga abullonada', 'puff sleeve'), 'bell_sleeve': ('manga campana', 'bell sleeve'), 'adjustable_straps': ('tirantes ajustables', 'adjustable straps'), 'cropped': ('corte corto', 'cropped'), 'bodycon': ('ajustado', 'bodycon'), 'a_line': ('corte A', 'A-line'), 'loose_fit': ('corte holgado', 'loose fit'), 'oversized': ('oversize', 'oversized'), 'wrap': ('corte cruzado', 'wrap'), 'high_waist': ('tiro alto', 'high waist'), 'wide_leg': ('pierna ancha', 'wide leg'), 'straight_leg': ('pierna recta', 'straight leg'), 'flared_leg': ('pierna acampanada', 'flared leg'), 'palazzo': ('palazzo', 'palazzo'), 'mini_length': ('largo mini', 'mini length'), 'midi_length': ('largo midi', 'midi length'), 'maxi_length': ('largo maxi', 'maxi length'), 'full_length': ('largo completo', 'full length'), 'front_knot': ('nudo frontal', 'front knot'), 'tie_waist': ('lazo en cintura', 'tie waist'), 'belted': ('con cinturón', 'belted'), 'button_front': ('botones frontales', 'button front'), 'zip_closure': ('cierre de cremallera', 'zip closure'), 'ruched': ('fruncido', 'ruched'), 'pleated': ('plisado', 'pleated'), 'ruffled': ('volantes', 'ruffled'), 'slit': ('abertura', 'slit'), 'cutout': ('recorte', 'cutout'), 'open_back': ('espalda abierta', 'open back'), 'asymmetric': ('asimétrico', 'asymmetric'), 'layered': ('en capas', 'layered'), 'pockets': ('con bolsillos', 'with pockets'), 'elastic_waist': ('cintura elástica', 'elastic waist'), 'corset': ('corsé', 'corset'), 'peplum': ('peplum', 'peplum'), 'lace_trim': ('detalle de encaje', 'lace trim'), 'satin_finish': ('acabado satinado', 'satin finish'), 'lace': ('encaje', 'lace'), 'sequins': ('lentejuelas', 'sequins'), 'mesh': ('malla', 'mesh'), 'knit': ('tejido de punto', 'knit'), 'denim': ('mezclilla', 'denim'), 'velvet': ('terciopelo', 'velvet'), 'sheer': ('transparencia', 'sheer'), 'solid': ('liso', 'solid'), 'floral_print': ('estampado floral', 'floral print'), 'striped': ('a rayas', 'striped'), 'plaid': ('a cuadros', 'plaid'), 'polka_dot': ('de lunares', 'polka dot'), 'animal_print': ('estampado animal', 'animal print'), 'two_piece': ('dos piezas', 'two-piece'), 'three_piece': ('tres piezas', 'three-piece'), 'matching_set': ('conjunto coordinado', 'matching set')}
CATEGORY_CODES = frozenset(['dress', 'formal_gown', 'jumpsuit', 'overalls', 'womens_pants_set', 'womens_shorts_set', 'womens_skirt_set', 'womens_suit', 'top', 'blouse', 'shirt', 'vest', 'blazer', 'jacket', 'coat', 'sweater', 'cardigan', 'pants', 'shorts', 'skirt', 'shoes'])

_PROFILE_FIELDS = (
    "category_code",
    "category",
    "subtype",
    "silhouette",
    "fit",
    "color",
    "style_codes",
    "style",
    "selling_point_codes",
    "selling_points",
    "construction",
    "defining_features",
    "exclusions",
    "scene_codes",
    "use_scene",
    "query_language",
    "query_seeds",
)
_COLLECTION_LIMITS = {
    "style_codes": (1, 3),
    "style": (1, 3),
    "scene_codes": (1, 3),
    "use_scene": (1, 3),
    "selling_point_codes": (2, 6),
    "selling_points": (2, 6),
    "construction": (1, 8),
    "defining_features": (2, 5),
    "exclusions": (1, 8),
    "query_seeds": (3, 3),
}
_SCALAR_FIELDS = (
    "category",
    "subtype",
    "silhouette",
    "fit",
    "color",
    "category_code",
    "query_language",
)
_LANGUAGE_BY_PLATFORM = {
    Platform.MERCADO_MX: "es-MX",
    Platform.SHEIN_US: "en-US",
}
DEFAULT_MAX_IMAGE_BYTES = 20 * 1024 * 1024
DEFAULT_TIMEOUT_SECONDS = 300.0
_WORD_RE = re.compile(r"[^\W_]+", re.UNICODE)
_GENERIC_SEEDS = {
    "women fashion", "womens fashion", "generic trendy clothing",
    "moda mujer", "ropa de moda generica", "ropa femenina",
}
_CATEGORY_TERMS = {
    "dress", "skirt", "top", "blouse", "shirt", "pants", "trousers",
    "jacket", "coat", "shoes", "set", "suit", "vest", "jumpsuit", "overalls", "shorts", "sweater", "cardigan", "blazer", "gown", "conjunto", "traje", "chaleco", "mono", "overol", "overoles", "short", "sueter", "vestido", "falda", "top", "blusa",
    "camisa", "pantalon", "pantalones", "chaqueta", "abrigo", "zapatos",
}
_WRONG_LANGUAGE_TERMS = {
    "es-MX": {"dress", "skirt", "blouse", "shirt", "pants", "trousers", "jacket", "coat", "shoes", "set", "suit", "vest", "jumpsuit", "overalls", "sweater", "gown"},
    "en-US": {"vestido", "falda", "blusa", "camisa", "pantalon", "pantalones", "chaqueta", "abrigo", "zapatos", "conjunto", "traje", "chaleco", "mono", "overol", "overoles", "sueter"},
}
@dataclass(frozen=True)
class ProductVisualProfile:
    category_code: str
    category: str
    subtype: str
    silhouette: str
    fit: str
    color: str
    style_codes: tuple[str, ...]
    style: tuple[str, ...]
    selling_point_codes: tuple[str, ...]
    selling_points: tuple[str, ...]
    construction: tuple[str, ...]
    defining_features: tuple[str, ...]
    exclusions: tuple[str, ...]
    scene_codes: tuple[str, ...]
    use_scene: tuple[str, ...]
    query_language: str
    query_seeds: tuple[str, str, str]

    def __post_init__(self) -> None:
        for name in _SCALAR_FIELDS:
            object.__setattr__(self, name, _normalized_string(getattr(self, name), name))
        if self.query_language not in frozenset(_LANGUAGE_BY_PLATFORM.values()):
            raise ValueError("query_language must be a supported marketplace label")
        if isinstance(self.style_codes, (list, tuple)):
            object.__setattr__(self, "style_codes", tuple("boho" if code == "bohemian" else code for code in self.style_codes))
        for name, (minimum, maximum) in _COLLECTION_LIMITS.items():
            values = _normalized_collection(
                getattr(self, name), name, minimum=minimum, maximum=maximum,
            )
            if name == "query_seeds" and any(not 3 <= len(value) <= 120 for value in values):
                raise ValueError("query_seeds items must contain 3-120 characters")
            object.__setattr__(self, name, values)
        if self.category_code not in CATEGORY_CODES:
            raise ValueError("unknown category code")
        lang_index = 0 if self.query_language == "es-MX" else 1
        for codes, values, mapping in ((self.style_codes, self.style, STYLES), (self.scene_codes, self.use_scene, SCENES), (self.selling_point_codes, self.selling_points, SELLING_POINTS)):
            if len(codes) != len(values) or any(code not in mapping or mapping[code][lang_index] != value for code, value in zip(codes, values)):
                raise ValueError("profile code and value mapping mismatch")
        _validate_category_code(self)
        _validate_query_seed_roles(self)

    @classmethod
    def from_dict(
        cls, raw: Mapping[str, object], platform: Platform,
    ) -> "ProductVisualProfile":
        if not isinstance(raw, MappingABC):
            raise TypeError("visual profile must be a mapping")
        if not isinstance(platform, Platform):
            raise TypeError("platform must be a Platform")
        supplied = set(raw.keys())
        required = set(_PROFILE_FIELDS)
        if supplied != required:
            missing = required - supplied
            unknown = supplied - required
            if missing:
                raise ValueError("visual profile has missing fields")
            if unknown:
                raise ValueError("visual profile has unknown fields")
        profile = cls(**{name: raw[name] for name in _PROFILE_FIELDS})
        if profile.query_language != _LANGUAGE_BY_PLATFORM[platform]:
            raise ValueError("query_language does not match the marketplace")
        return profile

    def to_dict(self) -> dict[str, object]:
        result = {name: getattr(self, name) for name in _PROFILE_FIELDS}
        for name in (
            "style_codes", "style", "scene_codes", "use_scene", "selling_point_codes", "selling_points", "construction", "defining_features",
            "exclusions", "query_seeds",
        ):
            result[name] = list(result[name])
        return result


def _normalized_string(value: object, field: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field} must be a string")
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field} must be a non-empty string")
    return normalized


def _normalized_collection(
    value: object,
    field: str,
    *,
    minimum: int,
    maximum: int,
) -> tuple[str, ...]:
    if isinstance(value, (str, bytes, bytearray)) or not isinstance(value, Sequence):
        raise TypeError(f"{field} must be a sequence of strings")
    normalized = tuple(_normalized_string(item, f"{field} item") for item in value)
    if not minimum <= len(normalized) <= maximum:
        if minimum == maximum:
            raise ValueError(f"{field} must contain exactly {minimum} items")
        raise ValueError(f"{field} must contain {minimum}-{maximum} items")
    if len(normalized) != len(set(normalized)):
        raise ValueError(f"{field} must not contain duplicates")
    return normalized


def _words(value: str) -> set[str]:
    return {"".join(c for c in unicodedata.normalize("NFKD", word.casefold()) if not unicodedata.combining(c)) for word in _WORD_RE.findall(value)}


def _attribute_words(values: Sequence[str]) -> set[str]:
    result: set[str] = set()
    for value in values:
        result.update(_words(value))
    return result


def _validate_query_seed_roles(profile: ProductVisualProfile) -> None:
    seed_words = [_words(seed) for seed in profile.query_seeds]
    category_words = (_words(profile.category) | _words(profile.subtype)) - STOPWORDS
    silhouette_words = _words(profile.silhouette) - STOPWORDS - category_words
    construction_words = _attribute_words(profile.construction) - STOPWORDS - category_words
    selling_point_words = _attribute_words(profile.selling_points) - STOPWORDS - category_words
    profile_style_words = _attribute_words(profile.style) - STOPWORDS - category_words
    use_scene_words = _attribute_words(profile.use_scene) - STOPWORDS - category_words
    target_categories = category_words & CATEGORY_TERMS_BY_LANGUAGE[profile.query_language]
    forbidden_language = _WRONG_LANGUAGE_TERMS[profile.query_language]

    for seed, words in zip(profile.query_seeds, seed_words, strict=True):
        normalized = " ".join(seed.casefold().split())
        if normalized in _GENERIC_SEEDS:
            raise ValueError("query_seeds must not use generic fashion phrases")
        if words & forbidden_language:
            raise ValueError("query_seeds contain obvious wrong-market language")
        if not words & category_words:
            raise ValueError("every query seed must agree lexically with category or subtype")
        drift = (words & CATEGORY_TERMS_BY_LANGUAGE[profile.query_language]) - target_categories - _attribute_words(profile.selling_points)
        if drift:
            raise ValueError("query_seeds contain category drift")
        if contains_color_or_size(seed, profile.query_language, profile.color):
            raise ValueError("query_seeds must not contain color or size")

    if not seed_words[1] & (
        silhouette_words | construction_words | selling_point_words
    ):
        raise ValueError("query seed 2 must contain silhouette, construction, or selling-point evidence")
    if seed_words[0] - category_words - STOPWORDS:
        raise ValueError("query seed 1 must use category or subtype vocabulary only")
    if not (
        seed_words[2] & use_scene_words
        or seed_words[2] & profile_style_words
    ):
        raise ValueError("query seed 3 must contain style or use-scene evidence")



STOPWORDS = frozenset({"de", "del", "y", "con", "para", "el", "la", "los", "las", "of", "and", "with", "for", "the", "a"})
CATEGORY_TERMS_BY_LANGUAGE = {
    "es-MX": frozenset({"vestido", "falda", "top", "blusa", "camisa", "pantalon", "pantalones", "chaqueta", "abrigo", "zapatos", "conjunto", "traje", "chaleco", "mono", "overol", "overoles", "short", "shorts", "sueter", "cardigan", "blazer"}),
    "en-US": frozenset({"dress", "skirt", "top", "blouse", "shirt", "pants", "trousers", "jacket", "coat", "shoes", "set", "suit", "vest", "jumpsuit", "overalls", "shorts", "sweater", "cardigan", "blazer", "gown"}),
}
_CATEGORY_ANCHORS = {
    "dress": ({"vestido"}, {"dress"}), "formal_gown": ({"vestido"}, {"gown", "dress"}),
    "jumpsuit": ({"mono"}, {"jumpsuit"}), "overalls": ({"overol", "overoles"}, {"overalls"}),
    "womens_pants_set": ({"conjunto", "traje"}, {"set", "suit"}),
    "womens_shorts_set": ({"conjunto", "traje"}, {"set", "suit"}),
    "womens_skirt_set": ({"conjunto", "traje"}, {"set", "suit"}),
    "womens_suit": ({"traje", "conjunto"}, {"suit", "set"}),
    "top": ({"top"}, {"top"}), "blouse": ({"blusa"}, {"blouse"}),
    "shirt": ({"camisa"}, {"shirt"}), "vest": ({"chaleco"}, {"vest"}),
    "blazer": ({"blazer"}, {"blazer"}), "jacket": ({"chaqueta"}, {"jacket"}),
    "coat": ({"abrigo"}, {"coat"}), "sweater": ({"sueter"}, {"sweater"}),
    "cardigan": ({"cardigan"}, {"cardigan"}), "pants": ({"pantalon", "pantalones"}, {"pants", "trousers"}),
    "shorts": ({"short", "shorts"}, {"shorts"}), "skirt": ({"falda"}, {"skirt"}),
    "shoes": ({"zapatos"}, {"shoes"}),
}
def _validate_category_code(profile):
    index = 0 if profile.query_language == "es-MX" else 1
    words = _words(profile.category) | _words(profile.subtype)
    if not words.intersection(_CATEGORY_ANCHORS[profile.category_code][index]):
        raise ValueError("category code does not match category vocabulary")
    expected = {"womens_pants_set": ({"pantalon", "pantalones"}, {"pants", "trousers"}),
                "womens_shorts_set": ({"short", "shorts"}, {"shorts"}),
                "womens_skirt_set": ({"falda"}, {"skirt"})}
    if profile.category_code in expected and not words.intersection(expected[profile.category_code][index]):
        raise ValueError("set category code does not match lower garment")


def validate_profile(raw, platform):
    return ProductVisualProfile.from_dict(raw, Platform(platform)).to_dict()
