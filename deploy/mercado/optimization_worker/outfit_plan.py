"""Validate host-observed clothing references and render a code-owned prompt.

Structural validation cannot establish that the host actually viewed images.
Infographics remain unavailable until literal inventory is implemented.
"""
import copy
import json
import re

ANGLES = ('front','front three-quarter','side','back three-quarter','back','detail or flat lay')
INSTANCES = frozenset(('dress','top','skirt','pants','jacket','coat','undergarment','other'))
ROLES = frozenset(('model','upper_construction','lower_construction','closure_detail','collar_detail','sleeve_detail','cuff_detail','hem_detail','skirt_hem','material_detail','garment_evidence','size_chart','unusable','waist_detail','trim_detail','full_outfit_flat_lay'))
CODE = re.compile(r'[a-z][a-z0-9_-]{0,47}:[a-z0-9][a-z0-9_-]{0,79}\Z')
SHA = re.compile(r'[a-f0-9]{64}\Z')
LABELS = {'garment_type':'Garment type evidence','material':'Material-family evidence','color':'Color-family evidence','collar_shape':'Collar-shape evidence','neckline':'Neckline evidence','closure':'Closure evidence','sleeves':'Sleeve construction evidence','trim':'Trim evidence','silhouette':'Silhouette evidence'}
REQUIRED = {'color:ivory':'Use the evidenced ivory color.','material:lace':'Preserve the evidenced lace material.','trim:lace':'Preserve the evidenced lace trim and edge details.','closure_state:closed':'Keep the evidenced closure closed.','cuff:present':'Preserve both evidenced cuffs.'}
FORBIDDEN = {'neckline:v-neck':'Use no V-neck construction.','front_style:open-cardigan':'Use no open-cardigan front.','undergarment_visibility:exposed-straps':'Show no exposed undergarment straps.'}

def _render(code, forbidden=False):
    category, value = code.split(':',1)
    label = LABELS.get(category,category.replace('_',' ').replace('-',' ').capitalize()+' evidence')
    value = value.replace('_',' ').replace('-',' ')
    return (FORBIDDEN.get(code, f'Do not use {label.lower()}: {value}.') if forbidden
            else REQUIRED.get(code, f'{label}: {value}.'))

def _render_required(codes):
    remaining = list(codes)
    rendered = []
    composites = [
        (('collar_shape:pointed','collar_size:small'), 'Preserve the evidenced small pointed collar.'),
        (('placket:continuous-to-bottom','closure_type:pearl-buttons','closure_state:closed'), 'Preserve the continuous button placket with pearl buttons, closed through the bottom.'),
        (('sleeve_length:long','sleeve_coverage:both','cuff:present'), 'Preserve both long sleeves and both cuffs.'),
    ]
    for parts, sentence in composites:
        if all(code in remaining for code in parts):
            rendered.append(sentence)
            for code in parts:
                remaining.remove(code)
    return rendered + [_render(code) for code in remaining]

def validate_and_render(plan, source_ids, digests, inventory=None, tentative=False):
    if not isinstance(plan, dict) or not isinstance(source_ids, list) or not source_ids or any(not isinstance(s,str) or not s for s in source_ids) or len(set(source_ids)) != len(source_ids):
        raise ValueError('Invalid sources')
    angle = plan.get('classification')
    if angle not in ANGLES and not (angle == 'infographic' and (tentative or inventory is not None)):
        raise ValueError('Infographic inventory or target angle unavailable')
    selected = plan.get('selected_reference_ids')
    if not isinstance(selected, list) or not 1 <= len(selected) <= 9 or any(not isinstance(s,str) for s in selected) or len(set(selected)) != len(selected) or not set(selected) <= set(source_ids):
        raise ValueError('Invalid reference selection')
    if not isinstance(digests, dict) or set(digests) != set(source_ids) or any(not isinstance(v,str) or not SHA.fullmatch(v) for v in digests.values()) or len({digests[s] for s in selected}) != len(selected):
        raise ValueError('Invalid or duplicate source digests')
    evidence = plan.get('source_evidence')
    if not isinstance(evidence,list) or len(evidence)!=len(source_ids) or any(not isinstance(e,dict) or not isinstance(e.get('source_id'),str) for e in evidence) or {e.get('source_id') for e in evidence} != set(source_ids):
        raise ValueError('Source evidence must cover each source')
    by_id = {e['source_id']:e for e in evidence}
    for e in evidence:
        roles=e.get('roles')
        if e.get('angle') not in (*ANGLES, 'infographic') or not isinstance(roles,list) or not roles or any(not isinstance(r,str) for r in roles) or len(set(roles))!=len(roles) or any(r not in ROLES and not (r.startswith('instance:') and (r[9:] in INSTANCES or (inventory is not None and r[9:] in inventory['garment_instances']) or (tentative and bool(r[9:].strip()) and len(r[9:]) <= 4096))) for r in roles) or type(e.get('information_score')) is not int or not 0 <= e['information_score'] <= 100:
            raise ValueError('Invalid source observation')
    if any(set(by_id[s]['roles']) & {'unusable','size_chart'} or by_id[s]['information_score'] == 0 for s in selected):
        raise ValueError('Selected reference is unusable')
    facts=plan.get('garment_facts')
    if not isinstance(facts,dict) or set(facts) != {'required','forbidden'} or any(not isinstance(facts[k],list) for k in facts):
        raise ValueError('Invalid facts')
    codes=facts['required']+facts['forbidden']
    if not codes or len(codes)>64 or any(not isinstance(c,str) or not CODE.fullmatch(c) for c in codes) or len(set(codes))!=len(codes):
        raise ValueError('Invalid fact codes')
    instances=plan.get('garment_instances')
    if not isinstance(instances,list) or not 1<=len(instances)<=12 or any(not isinstance(v,str) or v not in INSTANCES for v in instances) or len(set(instances))!=len(instances):
        raise ValueError('Invalid garment instances')
    items='; '.join(f'{i}. {v}' for i,v in enumerate(inventory['garment_instances'] if inventory is not None else instances,1))
    details=' '.join(_render_required(facts['required']) + [_render(code,True) for code in facts['forbidden']])
    prompt=('Asset type: fashion product image edit.\nPrimary request: Change only the clothing worn or displayed in Image 1. Replace every enumerated target clothing instance with the source garment.\n'
            'Input images: Image 1 is the edit target. Image 2 is the closest-angle primary garment reference; its visible construction wins conflicts. Images 3-N are complementary garment references.\n'
            f'Target instances: {items}.\nGarment evidence: {details}.\n'
            'Composition/framing: Preserve Image 1 camera, crop, viewpoint, canvas, aspect ratio, and layout.\n'
            'Constraints: Keep Image 1 identity, face, body, skin, hair, hands, feet, pose, accessories, carried objects, shoes, background, lighting, shadows, and color grade unchanged. Remove every remnant of original clothing. Match folds, occlusion, perspective, highlights, reflections, and shadows. Do not invent openings, straps, bows, slits, decoration, logos, text, or accessories. Preserve infographic text and layout verbatim when applicable.')
    if inventory is not None:
        prompt += '\nPreserve these literal visual elements as data: ' + json.dumps(inventory, ensure_ascii=False)
    return copy.deepcopy(plan), prompt
