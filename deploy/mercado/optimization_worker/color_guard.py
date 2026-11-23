"""Conservative structural color agreement gate for reference selection.

The caller's visual observations are not proof that the images were inspected.
"""
import re

COLORS = {
    'khaki': ('khaki', 'caqui', '卡其'), 'black': ('black', 'negro', 'negra', '黑色'),
    'brown': ('brown', 'marron', 'marrón', 'cafe', 'café', '棕色'),
    'white': ('white', 'blanco', 'blanca', '白色'), 'ivory': ('ivory', 'marfil', '象牙白'),
    'pink': ('pink', 'rosa', '粉色'), 'blue': ('blue', 'azul', '蓝色'),
    'red': ('red', 'rojo', 'roja', '红色'), 'green': ('green', 'verde', '绿色'),
    'gray': ('gray', 'grey', 'gris', '灰色'), 'beige': ('beige', '米色'),
}

class ColorUncertain(ValueError):
    pass

def _colors(text):
    if not isinstance(text, str): return set()
    value = text.casefold()
    return {key for key, aliases in COLORS.items() for alias in aliases
            if (alias in value if any(ord(c) > 127 and '\u4e00' <= c <= '\u9fff' for c in alias) else re.search(r'(?<!\w)' + re.escape(alias) + r'(?!\w)', value))}

def validate_colors(snapshot, plan):
    if not isinstance(snapshot, dict) or not isinstance(plan, dict): raise ColorUncertain('Missing color evidence')
    title_colors = _colors(snapshot.get('title', ''))
    variant_colors = set()
    for variant in snapshot.get('variations', []) or []:
        for attr in variant.get('attribute_combinations', []) or []:
            if attr.get('id') == 'COLOR':
                colors = _colors(attr.get('value_name', ''))
                if len(colors) != 1: raise ColorUncertain('Variant color unknown')
                variant_colors.update(colors)
    if len(title_colors) != 1 or len(variant_colors) > 1:
        raise ColorUncertain('Target color or SKU ambiguous; operator confirmation required')
    target = next(iter(title_colors))
    if variant_colors and target not in variant_colors:
        raise ColorUncertain('Target title and variant color conflict')
    if plan.get('target_color') != target:
        raise ColorUncertain('Target color observation conflicts with snapshot')
    evidence = {e.get('source_id'): e for e in plan.get('source_evidence', []) if isinstance(e, dict)}
    selected = plan.get('selected_reference_ids', [])
    if not selected or any(ref not in evidence for ref in selected):
        raise ValueError('Invalid reference selection')
    if any(evidence[ref].get('observed_color') != target for ref in selected):
        raise ColorUncertain('Selected references do not match target color')
