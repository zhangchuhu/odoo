"""Merge image observations into immutable, snapshot-derived product facts."""
from .handlers import product_facts
import re

VISUAL_FIELDS = frozenset({'服装类别', '类别', '领型', '袖型', '剪裁', '版型', '裙摆', '裤型', '颜色', '图案', '闭合方式', '可见装饰', '长度', 'category', 'neckline', 'sleeve', 'silhouette', 'fit', 'hem', 'color', 'pattern', 'closure', 'length', 'visible details'})
BLOCKED_VALUE = re.compile(r"棉|涤纶|聚酯|羊毛|真丝|成分|材质|销量|售价|cotton|polyester|poli[eé]ster|wool|silk|material|composition|sales|vendidos|price|precio", re.I)


def merge_visual_facts(snapshot, observations, uncertainties, verified_images):
    result = product_facts({'snapshot': snapshot})
    for item in result['facts']:
        item['category'] = 'known'
    if not isinstance(verified_images, list) or not isinstance(observations, list) or not isinstance(uncertainties, list):
        raise ValueError('Invalid observations')
    ids = set()
    for image in verified_images:
        if not isinstance(image, dict) or not all(isinstance(image.get(k), str) and image[k] for k in ('id', 'source', 'observed_at')) or not image['id'].startswith('image:') or image['id'] in ids:
            raise ValueError('Invalid image evidence')
        ids.add(image['id'])
    names = {str(f['name']).casefold() for f in result['facts']}
    for row in observations:
        if not isinstance(row, dict) or not isinstance(row.get('name'), str) or not row['name'].strip() or row['name'].casefold() in names or 'value' not in row:
            raise ValueError('Invalid visual observation')
        if row['name'].strip().casefold() not in VISUAL_FIELDS or not isinstance(row['value'], str) or not row['value'].strip() or BLOCKED_VALUE.search(row['value']):
            raise ValueError('Visual field outside allowed scope')
        refs = row.get('evidence_ids')
        if not isinstance(refs, list) or not refs or not all(isinstance(ref, str) and ref in ids for ref in refs):
            raise ValueError('Visual observation has no verified image')
        result['facts'].append({'name': row['name'], 'value': row['value'], 'evidence_ids': refs, 'category': 'visual'})
    if not all(isinstance(item, str) and item.strip() for item in uncertainties):
        raise ValueError('Invalid uncertainties')
    result['evidence'].extend(verified_images)
    result['uncertainties'].extend(uncertainties)
    return result
