"""Persistent verified gallery library. URLs are provenance, never fetch targets."""
import base64
import hashlib
import io
import json
import math
from decimal import Decimal, InvalidOperation
import re
import uuid
from PIL import Image, UnidentifiedImageError, WebPImagePlugin  # Register WebP after Odoo preinitializes Pillow.
from psycopg2.errors import DeadlockDetected, SerializationFailure
from urllib.parse import urlsplit
from odoo import api, fields, models
from odoo.exceptions import AccessError, ValidationError
from .optimization import PREFIX, ExactJson, bounded, MAX_BINARY
from ..domain import canonical_hash, validate_gallery, validate_result, card_v3_tier_valid
from ..domain.historical_rating import historical_rating


def _visible_review_lower_bound(value):
    if not isinstance(value, str) or len(value) > 100:
        return None
    match = re.fullmatch(r'\s*(\+)?\s*((?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?)\s*(k|mil)?\s*(\+)?\s*', value, re.I)
    if not match or ('.' in match.group(2) and not match.group(3)):
        return None
    try:
        amount = Decimal(match.group(2).replace(',', '')) * (1000 if match.group(3) else 1)
        return int(amount) if amount == amount.to_integral_value() else None
    except (InvalidOperation, ValueError, OverflowError):
        return None


def verified_image(raw, mime_type, dimensions=None):
    try:
        if len(raw) > MAX_BINARY:
            raise ValueError('Image exceeds 8 MiB')
        image = Image.open(io.BytesIO(raw))
        if Image.MIME.get(image.format) != mime_type or image.width * image.height > 40_000_000:
            raise ValueError('Unsupported image MIME or dimensions')
        if dimensions and image.size != dimensions:
            raise ValueError('Image dimensions do not match evidence')
        image.verify()
    except (ValueError, OSError, SyntaxError, UnidentifiedImageError, Image.DecompressionBombError) as exc:
        raise ValidationError('图片内容、尺寸或媒体类型无法验证') from exc


def platform_key(value):
    key = str(value or '').strip().casefold()
    aliases = {'mlm': 'mercado libre méxico', 'mercado libre mexico': 'mercado libre méxico',
               'shein us': 'shein us'}
    return aliases.get(key, key)


class ImportBatch(models.Model):
    _inherit = PREFIX + 'batch'
    external_import_key = fields.Char(index=True, readonly=True)
    _unique_external_import = models.Constraint('UNIQUE(company_id, external_import_key)', 'External table batch already exists')


class CollectionCompanyFence(models.Model):
    _name = PREFIX + 'collection.company.fence'
    _inherit = PREFIX + 'protected'
    _description = 'Collection company concurrency fence'
    _internal_only = True
    version = fields.Integer(default=0, required=True)
    _unique_company = models.Constraint('UNIQUE(company_id)', 'Collection company fence already exists')


class CollectionImportReceipt(models.Model):
    _name = PREFIX + 'collection.import.receipt'
    _inherit = PREFIX + 'protected'
    _description = 'Immutable collection import receipt'
    _internal_only = True
    import_key = fields.Char(required=True, index=True)
    manifest_hash = fields.Char(required=True)
    case_ids_json = ExactJson(required=True)
    _unique_import_key = models.Constraint('UNIQUE(company_id, import_key)', 'Collection import batch already exists')


class Library(models.Model):
    _name = PREFIX + 'library'
    _inherit = PREFIX + 'protected'
    _description = 'Verified company image library'
    _internal_only = True
    collection_archived = fields.Boolean(default=False, readonly=True, index=True)
    name = fields.Char(required=True)
    site_id = fields.Char(required=True)
    case_id = fields.Many2one(PREFIX + 'case', required=True, ondelete='restrict')
    task_id = fields.Many2one(PREFIX + 'task', ondelete='restrict')
    external_key = fields.Char(index=True)
    external_batch_key = fields.Char(index=True)
    _unique_external = models.Constraint('UNIQUE(company_id, external_key)', 'External record already imported')
    snapshot_id = fields.Many2one(PREFIX + 'snapshot', required=True, ondelete='restrict')
    result_hash = fields.Char(required=True)
    payload = ExactJson(required=True)
    _unique_import = models.Constraint('UNIQUE(task_id, result_hash)', 'Gallery already imported')


class LibraryArtifact(models.Model):
    _inherit = PREFIX + 'artifact'
    library_origin_id = fields.Many2one(PREFIX + 'library', readonly=True, ondelete='restrict')
    library_artifact_id = fields.Many2one(PREFIX + 'artifact', readonly=True, ondelete='restrict')

    @api.model_create_multi
    def create(self, vals_list):
        protected = {'library_origin_id', 'library_artifact_id'}
        if any(protected.intersection(v) for v in vals_list) or any('default_' + k in self.env.context for k in protected):
            raise AccessError('Library provenance is assigned only by checked selection')
        return super().create(vals_list)


class GalleryTaskAutomation(models.Model):
    _inherit = PREFIX + 'task'

    def research_progress(self, token, epoch, request_id, progress):
        self._authorize('worker'); self._lock(); self._fence(token,epoch)
        config=(self.snapshot_id.payload or {}).get('collection_config')
        card_v3=isinstance(config,dict) and config.get('strategy') in ('card-visual-v3','card-home-v4','shein-home-v1')
        home=isinstance(config,dict) and config.get('strategy') in ('card-home-v4','shein-home-v1')
        visual=isinstance(config,dict) and config.get('strategy') in ('visual-v2','card-visual-v3','card-home-v4','shein-home-v1')
        base={'stage','queries','reviewed','qualified','target'}
        if home:base.update(('strategy','search_terms','end_reason'))
        card=progress.get('card_v3') if isinstance(progress,dict) else None
        card_fields={'known_products','completed_pages','selected_per_query','threshold_met','supplemental','excluded','visual_required','visual_done','visual_threshold_passed','visual_supplemental_passed'}
        if isinstance(config,dict) and config.get('strategy')=='shein-home-v1':card_fields|={'reviews_met','reviews_below','reviews_unknown'}
        if (self.task_type!='research.bestseller' or not isinstance(request_id,str)
                or not isinstance(progress,dict) or set(progress)!=(base|{'card_v3'} if card_v3 else base)
                or (home and (progress.get('strategy')!=config['strategy'] or progress.get('end_reason') not in (None,'sales_target_reached','query_limit','terms_exhausted')))
                or progress['stage'] not in (('search','visual','finished') if visual else ('search','repeated','single','expanded','finished'))
                or any(type(progress[k]) is not int for k in ('queries','reviewed','qualified','target'))
                or not 0<=progress['queries']<=(9 if visual else 6) or not 0<=progress['reviewed']<=(900 if visual else 60)
                or not 0<=progress['qualified']<=progress['reviewed'] or progress['target']<1
                or progress['qualified']>progress['target']):
            raise ValidationError('无效的采集进度')
        if card_v3 and (not isinstance(card,dict) or set(card)!=card_fields
                or any(type(card[k]) is not int or card[k]<0 for k in card_fields-{'completed_pages','selected_per_query'})
                or not isinstance(card['completed_pages'],list) or not isinstance(card['selected_per_query'],list)
                or len(card['completed_pages'])!=len(card['selected_per_query']) or not 3<=len(card['completed_pages'])<=9
                or any(type(n) is not int or not 0<=n<=(1 if home else 3) for n in card['completed_pages'])
                or any(n is not None and (type(n) is not int or not 0<=n<=30) for n in card['selected_per_query'])
                or card['visual_done']>card['visual_required'] or card['visual_required']>30
                or card['visual_threshold_passed']>card['threshold_met'] or card['visual_supplemental_passed']>card['supplemental']):
            raise ValidationError('无效的分页采集进度')
        if home and (not isinstance(progress['search_terms'],list) or len(progress['search_terms'])!=len(card['completed_pages'])
                or any(not isinstance(row,dict) or set(row)!={'text','source'} or not isinstance(row['text'],str)
                       or not row['text'].strip() or len(row['text'])>200
                       or row['source'] not in ('category_anchor','platform_suggestion','image_fallback') for row in progress['search_terms'])):
            raise ValidationError('无效的搜索词来源')
        if isinstance(config,dict) and config.get('result_limit')!=progress['target']:
            raise ValidationError('采集目标与任务快照不一致')
        requests=self.env[PREFIX+'external.request'].search([('task_id','=',self.id),('request_id','=',request_id),('state','=','unknown')])
        if len(requests)!=1: raise ValidationError('原始采集请求不可用')
        previous=self.status_detail or {}
        if previous.get('code')=='research_collection_progress' and previous.get('request_id')==request_id and previous.get('lease_epoch')==epoch:
            if any(progress[k]<previous.get(k,0) for k in ('queries','reviewed','qualified')) or previous.get('target')!=progress['target']:
                raise ValidationError('采集进度不能倒退')
        labels={'search':'搜索候选','visual':'核验商品设计','repeated':'核验重复候选','single':'核验其他候选','expanded':'扩展搜索与核验','finished':'采集结束'}
        message='%s · 已搜索 %s 组 · 已核验 %s 个 · 符合条件 %s / %s' % (labels[progress['stage']],progress['queries'],progress['reviewed'],progress['qualified'],progress['target'])
        if card_v3:
            message+=' · 卡片销量明确 %s / 100 · 补充候选 %s · 视觉已判断 %s / %s' % (card['known_products'],card['supplemental'],card['visual_done'],card['visual_required'])
        self._set({'status_detail':dict(progress,code='research_collection_progress',request_id=request_id,lease_epoch=epoch,message=message)})
        return self._envelope()

    image_qc_key = fields.Char(readonly=True, index=True)
    _unique_image_qc_key = models.Constraint('UNIQUE(image_qc_key)', 'Image QC already exists')

    def _validate_manual_review_decision(self, result):
        self.ensure_one()
        qc = result.get('visual_qc') if isinstance(result, dict) else None
        if not isinstance(qc, dict) or qc.get('status') != 'manual_accepted':
            return
        event_id = qc.get('decision_event_id')
        event = self.env[PREFIX + 'event'].sudo().browse(event_id).exists() if type(event_id) is int and event_id > 0 else self.env[PREFIX + 'event']
        p = event.payload if event else None
        image = result.get('image') or {}
        if (self.snapshot_id != self.case_id._wb_snapshot(self.case_id)
                or not event or event.task_id != self or event.company_id != self.company_id or event.kind != 'image_review_decision'
                or not isinstance(p,dict) or event.digest != canonical_hash(p) or p.get('action') != 'manual_accept'
                or p.get('case_id') != self.case_id.id or p.get('snapshot_id') != self.snapshot_id.id
                or str(p.get('generation_plan_id')) != str(self.payload.get('generation_plan_id'))
                or str(p.get('candidate_artifact_id')) != str(image.get('artifact_id'))
                or p.get('candidate_sha256') != image.get('sha256') or p.get('candidate_sha256') != qc.get('candidate_sha256')
                or p.get('report_hash') != canonical_hash(qc.get('report')) or p.get('request_id') != result.get('request_id')):
            raise ValidationError('人工确认记录与图片或任务不匹配')

    def host_review_evidence(self, token, epoch, evidence):
        """Attach a worker-reported QC report to its exact waiting candidate."""
        self._authorize('worker')
        self._lock()
        self._fence(token, epoch)
        current = self.status_detail or {}
        if self.task_type != 'image.generate' or current.get('code') != 'host_review_required' or not isinstance(evidence, dict):
            raise ValidationError('当前任务没有待处理候选')
        required = {'request_id','review_seq','candidate_artifact_id','candidate_sha256','candidate_attempt_id','candidate_attempt_number','report'}
        if set(evidence) != required or evidence['request_id'] != current.get('request_id') or evidence['review_seq'] != current.get('review_seq'):
            raise ValidationError('复核请求或轮次不匹配')
        aid, digest, attempt, number, report = (evidence[k] for k in ('candidate_artifact_id','candidate_sha256','candidate_attempt_id','candidate_attempt_number','report'))
        if type(aid) is not int or aid <= 0 or not isinstance(attempt, str) or not re.fullmatch(r'[0-9a-f]{32}', attempt) or type(number) is not int or number not in (1,2,3):
            raise ValidationError('候选尝试标识无效')
        if current.get('review_kind') == 'best_of_budget' and any(current.get(k) != evidence[k] for k in ('candidate_artifact_id','candidate_sha256','candidate_attempt_id','candidate_attempt_number')):
            raise ValidationError('候选与所选最佳图片不匹配')
        artifact = self.env[PREFIX + 'artifact'].browse(aid).exists()
        if not artifact or artifact.case_id != self.case_id or artifact.company_id != self.company_id or not artifact.data or artifact.mime_type not in ('image/png','image/jpeg','image/webp') or artifact.digest != digest:
            raise ValidationError('候选图片归属或摘要无效')
        raw = base64.b64decode(artifact.data, validate=True)
        verified_image(raw, artifact.mime_type)
        if hashlib.sha256(raw).hexdigest() != digest:
            raise ValidationError('候选图片摘要无效')
        names = {'garment_construction','color_material','garment_details','identity_pose','accessories','scene_lighting_framing','text_layout'}
        checks = report.get('checks') if isinstance(report, dict) else None
        binding = report.get('quality_binding') if isinstance(report, dict) else None
        if (not isinstance(binding, dict) or binding.get('request_id') != evidence['request_id'] or binding.get('candidate_sha256') != digest
                or not isinstance(report.get('skill_report'), dict) or not isinstance(checks, list) or len(checks) != len(names)
                or any(not isinstance(c, dict) or c.get('status') not in ('passed','failed','unknown') or not c.get('evidence') for c in checks)
                or {c.get('name') for c in checks} != names):
            raise ValidationError('质检报告缺失或与候选不匹配')
        bounded(evidence)
        existing = current.get('review_evidence')
        if existing and existing != evidence:
            raise ValidationError('同一复核轮次的证据不可更改')
        if not existing:
            key = 'image-review-evidence:' + hashlib.sha256((evidence['request_id'] + ':' + str(evidence['review_seq'])).encode()).hexdigest()
            record = self.env[PREFIX + 'event']._append({'task_id': self.id, 'company_id': self.company_id.id,
                'submission_key': key, 'digest': canonical_hash(evidence), 'kind': 'image_review_evidence',
                'payload': evidence, 'receipt': {'review_seq': evidence['review_seq']}, 'worker_uid': self.env.uid})
            progress = dict(current, review_evidence=evidence, review_evidence_event_id=record.id,
                            review_report_hash=canonical_hash(evidence['report']))
            self._set({'status_detail': progress})
        return self._envelope()

    def _envelope(self):
        envelope = super()._envelope()
        if self.task_type == 'image.generate' and (self.status_detail or {}).get('code') in ('host_review_required','host_resume_requested','host_review_resumed','host_color_required','image_decision_requested'):
            envelope['host_progress'] = self.status_detail
        if self.task_type != 'image.qc':
            return envelope
        ref, plan_id = self.payload.get('image_artifact_id'), self.payload.get('generation_plan_id')
        candidates = self.env[PREFIX + 'task'].search([('company_id', '=', self.company_id.id), ('case_id', '=', self.case_id.id),
            ('snapshot_id', '=', self.snapshot_id.id), ('task_type', '=', 'image.generate'), ('state', '=', 'succeeded')])
        candidates = candidates.filtered(lambda t: t.payload.get('generation_plan_id') == plan_id and
            str((t.result or {}).get('image', {}).get('artifact_id', '')) == str(ref))
        if len(candidates) != 1 or not str(plan_id or '').isdigit() or not str(ref or '').isdigit():
            return envelope
        generated = candidates[0]
        target = generated.payload.get('target_artifact_id')
        plan = self.env[PREFIX + 'artifact'].browse(int(plan_id)).exists()
        image = self.env[PREFIX + 'artifact'].browse(int(ref)).exists()
        original = self.env[PREFIX + 'artifact'].browse(int(target)).exists() if str(target or '').isdigit() else self.env[PREFIX + 'artifact']
        if (not plan or not image or not original or any(a.case_id != self.case_id or a.company_id != self.company_id for a in (plan, image, original))
                or not isinstance(plan.payload, dict) or str(plan.payload.get('snapshot_id')) != str(self.snapshot_id.id)
                or str(target) not in [str(x) for x in plan.payload.get('target_artifact_ids', [])]
                or image.digest != (generated.result or {}).get('image', {}).get('sha256')):
            return envelope
        envelope.update(generation_task_id=generated.id, generation_target_artifact_id=str(target))
        return envelope

    def host_progress(self, token, epoch, request_id, stage, intent_id=None, review_seq=None, review_kind=None, candidate_artifact_id=None, candidate_sha256=None, candidate_attempt_id=None, candidate_attempt_number=None):
        self._authorize('worker')
        self._lock()
        self._fence(token, epoch)
        if self.task_type != 'image.generate' or stage not in ('review_required', 'resumed', 'color_required') or not isinstance(request_id, str) or type(review_seq) is not int or review_seq < 1:
            raise ValidationError('无效的宿主复核状态')
        requests = self.env[PREFIX + 'external.request'].search([('task_id','=',self.id), ('request_id','=',request_id), ('state','=','unknown')])
        if len(requests) != 1:
            raise ValidationError('原始请求不可用')
        current = self.status_detail or {}
        if stage == 'color_required':
            if current.get('code') == 'host_color_required' and current.get('request_id') == request_id:
                return self._envelope()
            if current.get('code') in ('host_review_required', 'host_resume_requested', 'host_review_resumed', 'host_color_required'):
                raise ValidationError('当前任务已存在未解决的宿主状态')
            self._set({'status_detail': {'code': 'host_color_required', 'request_id': request_id,
                'message': '目标 SKU 或颜色无法确认。请核对商品规格，选择单一颜色的参考图后新建生成任务。'}})
            return self._envelope()
        if current.get('code') in ('host_review_required', 'host_resume_requested', 'host_review_resumed', 'image_decision_requested') and current.get('request_id') != request_id:
            raise ValidationError('复核请求与当前任务不匹配')
        if stage == 'review_required' and review_seq == 1 and current.get('code') in ('host_review_required','host_resume_requested') and current.get('request_id') == request_id and not current.get('review_seq'):
            current = {}
        current_seq = current.get('review_seq', 0) if current.get('request_id') == request_id else 0
        if stage == 'review_required':
            if review_kind not in (None, 'best_of_budget'):
                raise ValidationError('无效复核类型')
            if review_kind == 'best_of_budget':
                if not isinstance(candidate_attempt_id, str) or not re.fullmatch(r'[0-9a-f]{32}', candidate_attempt_id) or type(candidate_attempt_number) is not int or candidate_attempt_number not in (1,2,3):
                    raise ValidationError('缺少候选尝试标识')
                artifact = self.env[PREFIX + 'artifact'].browse(int(candidate_artifact_id)).exists() if type(candidate_artifact_id) is int else self.env[PREFIX + 'artifact']
                if not artifact or artifact.case_id != self.case_id or artifact.company_id != self.company_id or not artifact.data or artifact.mime_type not in ('image/png','image/jpeg','image/webp') or artifact.digest != candidate_sha256:
                    raise ValidationError('候选图片归属或摘要无效')
                raw = base64.b64decode(artifact.data, validate=True)
                verified_image(raw, artifact.mime_type)
                if hashlib.sha256(raw).hexdigest() != artifact.digest:
                    raise ValidationError('候选图片摘要无效')
            if review_seq == current_seq and current.get('code') in ('host_review_required','host_resume_requested','host_review_resumed'):
                if review_kind == 'best_of_budget' and (current.get('candidate_artifact_id') != candidate_artifact_id or current.get('candidate_sha256') != candidate_sha256 or current.get('candidate_attempt_id') != candidate_attempt_id or current.get('candidate_attempt_number') != candidate_attempt_number):
                    raise ValidationError('同一复核轮次候选不能变更')
                return self._envelope()
            if review_seq != current_seq + 1 or (current_seq and current.get('code') != 'host_review_resumed'):
                raise ValidationError('复核轮次已过期或不连续')
        if stage == 'resumed' and current.get('code') == 'host_review_resumed' and current.get('request_id') == request_id and intent_id and current.get('intent_id') == intent_id and current_seq == review_seq:
            return self._envelope()
        requested_intent = ((current.get('decision') or {}).get('intent_id') if current.get('code') == 'image_decision_requested' else current.get('intent_id'))
        if stage == 'resumed' and (current.get('code') not in ('host_resume_requested','image_decision_requested') or current.get('request_id') != request_id or not intent_id or requested_intent != intent_id or current_seq != review_seq):
            raise ValidationError('没有匹配的复核恢复请求')
        code, message = ('host_review_required','图片候选等待人工复核；恢复不会重新生成。') if stage == 'review_required' else ('host_review_resumed','已请求继续复核原图片。')
        progress = {'code':code,'message':message,'request_id':request_id,'review_seq':review_seq}
        if stage == 'resumed' and current.get('review_kind') == 'best_of_budget':
            progress.update({k: current[k] for k in ('review_kind','candidate_artifact_id','candidate_sha256','candidate_attempt_id','candidate_attempt_number')})
        if stage == 'review_required' and review_kind == 'best_of_budget':
            progress.update(review_kind=review_kind,candidate_artifact_id=candidate_artifact_id,candidate_sha256=candidate_sha256,candidate_attempt_id=candidate_attempt_id,candidate_attempt_number=candidate_attempt_number,
                            message='三张候选中已选最佳图，但尚未通过质检，需人工复核。')
        if stage == 'resumed': progress['intent_id'] = intent_id
        self._set({'status_detail': progress})
        return self._envelope()

    @api.model_create_multi
    def create(self, vals_list):
        if any(v.get('task_type', self.env.context.get('default_task_type')) == 'image.qc' for v in vals_list):
            raise ValidationError('图片质检已包含在逐图生成任务中')
        if 'default_image_qc_key' in self.env.context or any('image_qc_key' in v for v in vals_list):
            raise AccessError('QC key is server managed')
        records = super().create(vals_list)
        for case in records.filtered(lambda task: task.task_type == 'image.qc').mapped('case_id'):
            case._bump_qc_revision()
        return records

    def _transition(self, target):
        result = super()._transition(target)
        if self.task_type == 'image.qc':
            self.case_id._bump_qc_revision()
        return result

    def complete(self, token, epoch, key, body):
        self._authorize('worker')
        self._lock()
        was_succeeded = self.state == 'succeeded'
        receipt = super().complete(token, epoch, key, body)
        if was_succeeded or self.state != 'succeeded':
            return receipt
        screening_visual = (self.task_type == 'product.facts' and not self.case_id.collection
            and (self.payload or {}).get('screening_visual_enrichment')
            and self.payload.get('screening_visual_enrichment') == (self.snapshot_id.payload or {}).get('screening_id'))
        if screening_visual or self.task_type == 'image.generate' or (self.case_id.collection and (self.task_type == 'gallery.collect' or (self.task_type in ('product.facts', 'research.keywords', 'research.bestseller') and self.snapshot_id.payload.get('collection_config')))):
            self._attempt_derived()
        return receipt

    def _attempt_derived(self):
        self.ensure_one()
        try:
            with self.env.cr.savepoint():
                self._run_derived()
        except (SerializationFailure, DeadlockDetected):
            raise
        except Exception:
            self._set({'status_detail': {'code': 'derived_action_failed',
                'message': '后续图库归档或质检任务创建失败；原任务结果已保存，请检查后重试。'}})
            return False
        if (self.status_detail or {}).get('code') == 'derived_action_failed':
            self._set({'status_detail': None})
        return True

    def _run_derived(self):
        self.ensure_one()
        case = self.case_id.sudo()
        case._lock()
        if case.collection and case.collection_archived:
            raise ValidationError('已归档采集不能创建后续产物')
        if case._wb_snapshot(case) != self.snapshot_id:
            raise ValidationError('快照已改变，无法处理旧任务')
        if (case.collection and self.task_type in ('product.facts', 'research.keywords', 'research.bestseller')
                and case.collection_started_snapshot_id != self.snapshot_id):
            return
        if (self.task_type == 'product.facts' and not case.collection
                and (self.payload or {}).get('screening_visual_enrichment')
                == (self.snapshot_id.payload or {}).get('screening_id')
                and (self.payload or {}).get('screening_visual_enrichment')):
            case.workbench_archive(case.id, self.id, canonical_hash(self.result))
            return
        if self.task_type in ('product.facts', 'research.keywords') and case.collection and self.snapshot_id.payload.get('collection_config'):
            if self.task_type == 'product.facts' and not self.result.get('facts'):
                return
            if self.task_type == 'research.keywords' and (self.result.get('status') == 'empty' or not self.result.get('keywords')):
                return
            artifact_id = case.workbench_archive(case.id, self.id, canonical_hash(self.result))
            tasks = self.env[PREFIX + 'task'].sudo()
            site = self.snapshot_id.payload['site_id']
            if self.task_type == 'product.facts':
                kind = 'research.keywords'
                values = {'facts_artifact_id': str(artifact_id), 'site_id': site}
            else:
                kind = 'research.bestseller'
                values = {'facts_artifact_id': self.payload['facts_artifact_id'], 'keywords_artifact_id': str(artifact_id), 'site_id': site}
            existing = tasks.search([('case_id', '=', case.id), ('snapshot_id', '=', self.snapshot_id.id), ('task_type', '=', kind)])
            if not any(all(task.payload.get(k) == v for k, v in values.items()) for task in existing):
                case.workbench_task(case.id, kind, values)
        elif self.task_type == 'research.bestseller' and case.collection and self.snapshot_id.payload.get('collection_config'):
            case.workbench_library_research_import(case.id, self.id, {})
        elif self.task_type == 'gallery.collect' and case.collection:
            case.workbench_library_import(case.id, self.id, canonical_hash(self.result))
        elif self.task_type == 'image.generate':
            image = self.result.get('image') or {}
            ref = str(image.get('artifact_id') or '')
            artifact = self.env[PREFIX + 'artifact'].sudo().browse(int(ref)).exists() if ref.isdigit() else self.env[PREFIX + 'artifact']
            if not artifact or artifact.case_id != case or artifact.company_id != case.company_id or not artifact.data or artifact.digest != image.get('sha256') or artifact.mime_type != image.get('mime_type'):
                raise ValidationError('生成图片产物归属或摘要无效')


class CollectionWorkbench(models.Model):
    _inherit = PREFIX + 'case'
    collection = fields.Boolean(default=False, readonly=True, index=True)
    collection_started_snapshot_id = fields.Many2one(PREFIX + 'snapshot', readonly=True, ondelete='restrict')
    collection_archived = fields.Boolean(default=False, readonly=True, index=True)
    collection_list_hidden = fields.Boolean(default=False, readonly=True, index=True)
    collection_fence_version = fields.Integer(default=0, readonly=True)
    qc_revision = fields.Integer(default=0, readonly=True)
    collection_request_key = fields.Char(readonly=True, index=True)
    collection_request_actor = fields.Many2one('res.users', readonly=True)
    collection_request_hash = fields.Char(readonly=True)
    _collection_request_unique = models.Constraint('UNIQUE(company_id, collection_request_actor, collection_request_key)', '采集请求已存在')

    @api.model
    def workbench_regenerate_expired_image(self, case_id, task_id, expected_epoch):
        case = self._wb_case(case_id, True)
        if type(task_id) is not int or type(expected_epoch) is not int or expected_epoch < 1:
            raise ValidationError('任务或租约版本无效')
        task = self.env[PREFIX + 'task'].browse(task_id).exists()
        if not task or task.case_id != case:
            raise ValidationError('任务不属于当前商品')
        task._authorize('editor')
        case._lock()
        task._lock()
        events = self.env[PREFIX + 'event'].sudo()
        key = 'expired-image-regenerate:%s' % expected_epoch
        prior = events.search([('task_id', '=', task.id), ('submission_key', '=', key)], limit=1)
        if prior:
            return dict(prior.receipt)
        if (task.task_type != 'image.generate' or task.state != 'running'
                or task.lease_epoch != expected_epoch or not task.lease_expires_at
                or task.lease_expires_at > fields.Datetime.now()
                or task.snapshot_id != self._wb_snapshot(case) or case.collection_archived):
            raise ValidationError('任务、快照或租约状态已变化，请刷新')
        task._set({'state':'waiting', 'lease_token':False, 'lease_expires_at':False, 'worker_uid':False})
        if task._unresolved_requests():
            receipt = task.retry_unknown_task()
        else:
            task._transition('queued')
            task._set({'status_detail':None})
            receipt = {'task_id':task.id, 'source_task_id':task.id, 'state':'queued'}
        payload = {'epoch':expected_epoch, 'actor_id':self.env.uid, 'accepted_duplicate_results':True}
        events._append({'task_id':task.id, 'company_id':task.company_id.id, 'submission_key':key,
            'digest':canonical_hash(payload), 'kind':'expired_image_regenerate', 'payload':payload,
            'receipt':receipt, 'worker_uid':self.env.uid})
        return receipt

    @api.model
    def workbench_image_review_decide(self, case_id, task_id, action, review_seq, candidate_sha256, report_hash, note, intent_id):
        case = self._wb_case(case_id, True)
        task = self.env[PREFIX + 'task'].browse(int(task_id)).exists()
        if not task or task.case_id != case:
            raise ValidationError('任务不属于当前商品')
        task._authorize('editor')
        case._lock()
        task._lock()
        if (action not in ('regenerate','manual_accept') or type(review_seq) is not int or review_seq < 1
                or not isinstance(intent_id, str) or not re.fullmatch(r'[0-9a-f]{32}', intent_id)
                or not isinstance(note, str) or len(note) > 1000):
            raise ValidationError('无效的图片处理决定')
        events = self.env[PREFIX + 'event']
        key = 'image-decision:' + intent_id
        prior = events.search([('task_id','=',task.id), ('submission_key','=',key)], limit=1)
        if prior:
            p = prior.payload
            if any(p.get(k) != v for k,v in {'action':action,'review_seq':review_seq,'candidate_sha256':candidate_sha256,'report_hash':report_hash,'note':note,'actor_id':self.env.uid}.items()):
                raise ValidationError('处理请求已用于其他决定')
            return prior.receipt
        current = task.status_detail or {}
        evidence = current.get('review_evidence')
        if (task.task_type != 'image.generate' or task.state != 'running' or not task.lease_expires_at
                or task.lease_expires_at <= fields.Datetime.now() or task.snapshot_id != self._wb_snapshot(case)
                or current.get('code') != 'host_review_required' or not isinstance(evidence,dict)
                or evidence.get('review_seq') != review_seq or evidence.get('candidate_sha256') != candidate_sha256
                or canonical_hash(evidence.get('report')) != report_hash):
            raise ValidationError('候选或任务状态已变化，请刷新')
        request = self.env[PREFIX + 'external.request'].search([('task_id','=',task.id),('request_id','=',evidence['request_id']),('state','=','unknown'),('retry_superseded','=',False)],limit=1)
        ref = str(task.payload.get('generation_plan_id') or '')
        plan = self.env[PREFIX + 'artifact'].browse(int(ref)).exists() if ref.isdigit() else self.env[PREFIX + 'artifact']
        p = plan.payload if plan else None
        if (not request or not plan or plan.case_id != case or plan.company_id != case.company_id or not isinstance(p,dict)
                or plan.digest != canonical_hash(p) or str(p.get('snapshot_id')) != str(task.snapshot_id.id)
                or str(p.get('case_id')) != str(case.id)
                or not isinstance(p.get('target_artifact_ids'), list) or not isinstance(p.get('source_artifact_ids'), list)
                or not isinstance(task.payload.get('source_artifact_ids'), list)
                or str(task.payload.get('target_artifact_id')) not in [str(i) for i in p.get('target_artifact_ids',[])]
                or [str(i) for i in p.get('source_artifact_ids',[])] != [str(i) for i in task.payload.get('source_artifact_ids',[])]):
            raise ValidationError('生成计划、图片或原请求无效')
        payload = {'action':action,'intent_id':intent_id,'case_id':case.id,'snapshot_id':task.snapshot_id.id,
                   'generation_plan_id':plan.id,'request_id':evidence['request_id'],'review_seq':review_seq,
                   'candidate_artifact_id':evidence['candidate_artifact_id'],'candidate_sha256':candidate_sha256,
                   'candidate_attempt_id':evidence['candidate_attempt_id'],'candidate_attempt_number':evidence['candidate_attempt_number'],
                   'report_hash':report_hash,'note':note,
                   'actor_id':self.env.uid,'decided_at':fields.Datetime.to_string(fields.Datetime.now())}
        event = events._append({'task_id':task.id,'company_id':task.company_id.id,'submission_key':key,
             'digest':canonical_hash(payload),'kind':'image_review_decision','payload':payload,
             'receipt':{'intent_id':intent_id},'worker_uid':self.env.uid})
        receipt = {'intent_id':intent_id,'event_id':event.id}
        event._set({'receipt':receipt})
        task._set({'status_detail':dict(current,code='image_decision_requested',decision=payload,decision_event_id=event.id,
                                      message='已记录人工处理决定，等待宿主处理。')})
        return receipt

    @api.model
    def workbench_request_best_review_resume(self, case_id, task_id, candidate_artifact_id, candidate_sha256, candidate_attempt_id, candidate_attempt_number, expected_review_seq):
        case = self._wb_case(case_id, True)
        task = self.env[PREFIX + 'task'].browse(int(task_id)).exists()
        if not task or task.case_id != case:
            raise ValidationError('任务不属于当前商品')
        task._authorize()
        task._lock()
        detail = task.status_detail or {}
        if (task.task_type != 'image.generate' or task.state != 'running' or not task.lease_expires_at or task.lease_expires_at <= fields.Datetime.now()
            or task.snapshot_id != self._wb_snapshot(case) or detail.get('review_kind') != 'best_of_budget'
            or detail.get('candidate_artifact_id') != candidate_artifact_id or detail.get('candidate_sha256') != candidate_sha256
            or detail.get('candidate_attempt_id') != candidate_attempt_id or detail.get('candidate_attempt_number') != candidate_attempt_number
            or type(expected_review_seq) is not int or detail.get('review_seq') != expected_review_seq):
            raise ValidationError('最佳候选或租约已改变，请刷新')
        if not self.env[PREFIX + 'external.request'].search_count([('task_id','=',task.id), ('request_id','=',detail.get('request_id')), ('state','=','unknown')]):
            raise ValidationError('原始请求不可用')
        if detail.get('code') == 'host_resume_requested' and detail.get('intent_id'):
            return True
        if detail.get('code') != 'host_review_required' or type(detail.get('review_seq')) is not int:
            raise ValidationError('最佳候选当前不可复核')
        progress = dict(detail, code='host_resume_requested', message='等待宿主复核所选最佳图片；不会重新生成。', intent_id=str(uuid.uuid4()))
        task._set({'status_detail': progress})
        return True

    @api.model
    def workbench_request_host_review_resume(self, case_id, task_id, expected_review_seq=None):
        case = self._wb_case(case_id, True)
        task = self.env[PREFIX + 'task'].browse(int(task_id)).exists()
        if not task or task.case_id != case:
            raise ValidationError('任务不属于当前商品')
        task._authorize()
        task._lock()
        detail = task.status_detail or {}
        if task.task_type != 'image.generate' or task.state != 'running' or not task.lease_expires_at or task.lease_expires_at <= fields.Datetime.now() or task.snapshot_id != self._wb_snapshot(case) or detail.get('code') not in ('host_review_required','host_resume_requested'):
            raise ValidationError('没有可恢复的原图片复核')
        if not self.env[PREFIX + 'external.request'].search_count([('task_id','=',task.id), ('request_id','=',detail.get('request_id')), ('state','=','unknown')]):
            raise ValidationError('原始请求不可用')
        if detail.get('review_kind') == 'best_of_budget':
            raise ValidationError('最佳候选仍需人工复核，不能以普通方式继续')
        if not detail.get('review_seq'):
            if expected_review_seq is not None:
                raise ValidationError('复核轮次已改变，请刷新')
            task._set({'status_detail': {'code':'host_review_required','message':'旧复核状态已恢复，请重新请求继续复核。', 'request_id':detail['request_id'], 'review_seq':1}})
            return False
        if type(expected_review_seq) is not int or expected_review_seq != detail.get('review_seq'):
            raise ValidationError('复核轮次已改变，请刷新')
        if detail.get('code') == 'host_resume_requested':
            if not detail.get('intent_id'):
                raise ValidationError('复核恢复意图不完整，请联系管理员')
            return True
        task._set({'status_detail': {'code':'host_resume_requested', 'message':'等待宿主继续复核原图片；不会重新生成。', 'request_id':detail['request_id'], 'intent_id':str(uuid.uuid4()), 'review_seq':detail['review_seq']}})
        return True

    @api.model
    def _collection_company_fence(self):
        self.env.cr.execute("""INSERT INTO meli_optimization_collection_company_fence (company_id,version)
            VALUES (%s,1) ON CONFLICT (company_id) DO UPDATE
            SET version = meli_optimization_collection_company_fence.version + 1""", [self.env.company.id])

    def _collection_case_fence(self):
        self.ensure_one()
        if self.collection:
            self.env.cr.execute('UPDATE meli_optimization_case SET collection_fence_version = COALESCE(collection_fence_version,0)+1 WHERE id=%s', [self.id])
            self.invalidate_recordset(['collection_fence_version','collection_archived'])

    @api.model_create_multi
    def create(self, vals_list):
        protected=('collection_request_key','collection_request_actor','collection_request_hash')
        if any('default_'+name in self.env.context for name in protected) or any(any(name in vals for name in protected) for vals in vals_list):
            raise AccessError('采集请求身份由服务端管理')
        if any('default_' + n in self.env.context for n in ('collection_archived','collection_fence_version','collection_list_hidden','collection_started_snapshot_id')) or any({'collection_archived','collection_fence_version','collection_list_hidden','collection_started_snapshot_id'} & set(v) for v in vals_list):
            raise AccessError('Archive status is server managed')
        if 'default_qc_revision' in self.env.context or any('qc_revision' in v for v in vals_list):
            raise AccessError('QC revision is server managed')
        effective = [self._creation_values(vals) for vals in vals_list]
        if any(vals.get('collection_archived') or vals.get('collection_list_hidden') or vals.get('collection_fence_version') not in (None, False, 0) for vals in effective):
            raise AccessError('Archive status is server managed')
        if any(vals.get('collection') for vals in effective):
            for vals in effective:
                if vals.get('collection'):
                    batch = self.env[PREFIX + 'batch'].browse(vals.get('batch_id')).exists()
                    if batch:
                        batch._authorize()
                        if batch.company_id != self.env.company:
                            raise AccessError('仅可为当前公司创建采集')
            self.env.cr.execute('SELECT pg_advisory_xact_lock(hashtext(%s), %s)', ('collection-company', self.env.company.id))
            self._collection_company_fence()
        return super().create(vals_list)

    def _bump_qc_revision(self):
        self.ensure_one()
        self.env.cr.execute('UPDATE meli_optimization_case SET qc_revision = COALESCE(qc_revision, 0) + 1 WHERE id = %s', [self.id])
        self.invalidate_recordset(['qc_revision'])

    @api.model
    def workbench_retry_derived(self, case_id, task_id):
        case = self._wb_case(case_id, True)
        task = self.env[PREFIX + 'task'].browse(int(task_id)).exists()
        if not task or task.case_id != case or task.state != 'succeeded' or (task.status_detail or {}).get('code') != 'derived_action_failed':
            raise ValidationError('没有可重试的后续操作')
        task._authorize()
        task._lock()
        if not task._attempt_derived():
            return {'ok': False}
        return {'ok': True}

    @api.model
    def workbench_collection_source_search(self, item_id, page=0):
        self._authorize('editor')
        if not isinstance(item_id, str) or not item_id.strip() or len(item_id.strip()) > 200 or type(page) is not int or page < 0:
            raise ValidationError('请输入有效的商品 ID')
        wanted = item_id.strip().casefold()
        matches = []
        for case in self.search([('company_id','=',self.env.company.id),('collection','=',False)], order='id desc'):
            snapshot = self._wb_snapshot(case)
            if not snapshot:
                continue
            payload = snapshot.payload if snapshot and isinstance(snapshot.payload,dict) else {}
            identity = payload.get('item_id') or case.variant_item_id
            if not isinstance(identity,str) or identity.strip().casefold() != wanted:
                continue
            case._authorize('editor')
            scope=payload.get('variant_scope') or {}
            images=self._wb_source_images(case,snapshot)
            matches.append({'case_id':case.id,'snapshot_id':snapshot.id,'revision':case.revision,'item_id':identity,
                'title':str(payload.get('title') or case.name),'shop':str(payload.get('shop') or ''),
                'source_url':str(payload.get('source_url') or ''),
                'variant':str(scope.get('label') or '未区分变体') if isinstance(scope,dict) else '未区分变体',
                'images':images})
        return {'rows':matches[page*20:(page+1)*20], 'total':len(matches), 'page':page}

    @api.model
    def workbench_collection_create_request(self, request_key, values):
        self._authorize('editor')
        if not isinstance(request_key,str) or not re.fullmatch(r'[0-9a-f]{32}',request_key) or not isinstance(values,dict):
            raise ValidationError('采集请求无效')
        try:
            request_hash=hashlib.sha256(json.dumps(values,sort_keys=True,ensure_ascii=False,allow_nan=False,separators=(',',':')).encode()).hexdigest()
        except (TypeError,ValueError):
            raise ValidationError('采集请求无效') from None
        self.env.cr.execute('SELECT pg_advisory_xact_lock(hashtext(%s), %s)', ('collection-company', self.env.company.id))
        existing=self.search([('company_id','=',self.env.company.id),('collection_request_actor','=',self.env.uid),('collection_request_key','=',request_key)],limit=1)
        if existing:
            if existing.collection_request_hash != request_hash: raise ValidationError('采集请求内容已改变')
            return self.workbench_detail(existing.id)
        mode=values.get('source_mode')
        if mode not in ('existing','manual'): raise ValidationError('请选择源商品方式')
        clean={k:v for k,v in values.items() if k not in ('source_mode','source_case_id','source_snapshot_id','source_revision','source_artifact_id')}
        provenance=None
        if mode == 'existing':
            for k in ('source_case_id','source_snapshot_id','source_revision','source_artifact_id'):
                if type(values.get(k)) is not int or values[k] < (0 if k=='source_revision' else 1): raise ValidationError('商品或图片身份无效')
            source=self._wb_case(values['source_case_id'],True)
            if source.company_id != self.env.company or source.collection: raise AccessError('商品不属于当前公司')
            source._lock()
            snapshot=self._wb_snapshot(source)
            if not snapshot or snapshot.id != values['source_snapshot_id'] or source.revision != values['source_revision']:
                raise ValidationError('商品快照已改变，请重新查询')
            payload=snapshot.payload if isinstance(snapshot.payload,dict) else {}
            item_id=payload.get('item_id') or source.variant_item_id
            if not isinstance(item_id,str) or str(clean.get('source_sku') or '').strip().casefold()!=item_id.strip().casefold():
                raise ValidationError('商品 ID 不匹配，请重新查询')
            refs=payload.get('source_image_artifact_ids') or []
            if not isinstance(refs,list) or str(values['source_artifact_id']) not in [str(x) for x in refs]:
                raise ValidationError('图片不属于当前商品快照')
            image=self.env[PREFIX+'artifact'].browse(values['source_artifact_id']).exists()
            if not image or image.case_id != source or image.company_id != source.company_id or not image.data:
                raise ValidationError('源图片不存在或无权访问')
            image._authorize('worker')
            try:
                raw=base64.b64decode(image.data,validate=True)
                if hashlib.sha256(raw).hexdigest()!=image.digest: raise ValueError('digest')
                verified_image(raw,image.mime_type)
            except (TypeError,ValueError,ValidationError) as exc:
                raise ValidationError('源图片不可用') from exc
            clean.update(source_sku=item_id,image_data=image.data.decode() if isinstance(image.data,bytes) else image.data,image_mime=image.mime_type,
                         source_url=str(payload.get('source_url') or ''))
            provenance={'case_id':source.id,'snapshot_id':snapshot.id,'artifact_id':image.id,'sha256':image.digest}
        detail=self._collection_create(clean,source_provenance=provenance)
        case=self.browse(detail['id'])
        case._set({'collection_request_key':request_key,'collection_request_actor':self.env.uid,'collection_request_hash':request_hash})
        return detail

    @api.model
    def workbench_collection_create(self, values):
        return self._collection_create(values)

    def _collection_create(self, values, external_source=None, source_provenance=None):
        self._authorize()
        self.env.cr.execute('SELECT pg_advisory_xact_lock(hashtext(%s), %s)', ('collection-company', self.env.company.id))
        if not isinstance(values, dict):
            raise ValidationError('采集信息无效')
        config_keys = ('platform', 'min_sold', 'min_reviews', 'min_rating', 'result_limit')
        config = None
        if any(key in values for key in config_keys):
            config = {key: values.get(key) for key in config_keys}
            platform = config['platform']
            if isinstance(platform, str) and platform not in ('shein-us', 'mercado-libre-mx'):
                link = re.fullmatch(r'\[[^\]]+\]\((https?://[^\s)]+)\)', platform.strip())
                parsed = urlsplit(link.group(1) if link else platform.strip())
                host = (parsed.hostname or '').lower()
                if parsed.scheme in ('http', 'https') and not parsed.username and not parsed.password:
                    if host == 'shein.com' or host.endswith('.shein.com'):
                        config['platform'] = 'shein-us'
                    elif host == 'mercadolibre.com.mx' or host.endswith('.mercadolibre.com.mx'):
                        config['platform'] = 'mercado-libre-mx'
            if (config['platform'] not in ('shein-us', 'mercado-libre-mx')
                    or not isinstance(values.get('source_sku'), str) or not values['source_sku'].strip()
                    or len(values['source_sku'].strip()) > 200 or not values.get('image_data')
                    or any(type(config[key]) is not int or not 0 <= config[key] <= 1000000000 for key in ('min_sold', 'min_reviews'))
                    or type(config['min_rating']) not in (int, float) or not math.isfinite(config['min_rating']) or not 0 <= config['min_rating'] <= 5
                    or type(config['result_limit']) is not int or config['result_limit'] < 1):
                raise ValidationError('请提供商品 ID、原图及有效的平台和筛选条件')
            if config['platform']=='mercado-libre-mx':
                config.update(strategy='card-home-v4',initial_query_count=3,max_expansion_queries=6,
                              max_pages_per_query=1,sales_known_target=100,top_per_query=30,visual_limit=30)
            elif values.get('strategy')=='shein-home-v1':
                config.update(strategy='shein-home-v1',initial_query_count=3,max_expansion_queries=6,
                              max_pages_per_query=1,sales_known_target=100,top_per_query=30,visual_limit=30)
            elif values.get('strategy'):
                raise ValidationError('不支持的采集策略')
        source_url = str(values.get('source_url') or '').strip()
        if source_url and urlsplit(source_url).scheme not in ('https', 'http'):
            raise ValidationError('来源链接仅支持 HTTP(S)，不会由服务器抓取')
        name = str(values.get('name') or values.get('garment') or values.get('source_sku') or '独立采集')[:200]
        batch = self.env[PREFIX + 'batch'].create({'name': '独立爆款采集'})
        case = self.create({'name': name, 'batch_id': batch.id, 'collection': True})
        source_ids = []
        if values.get('image_data'):
            source_ids.append(self.workbench_upload_garment(case.id, values['image_data'], values.get('image_mime')))
        snapshot_payload = {
            'source_image_artifact_ids': source_ids, 'source_url': source_url[:2000],
            'site_id': ('MLM' if config and config['platform'] == 'mercado-libre-mx' else 'SHEIN_US' if config else str(values.get('site_id') or 'MLM')[:30]), 'source_sku': str(values.get('source_sku') or '').strip()[:200], 'source': 'user_supplied_research'}
        if values.get('garment'):
            snapshot_payload['garment'] = str(values['garment'])[:4000]
        if source_provenance is not None:
            snapshot_payload['source_provenance'] = source_provenance
        if config is not None:
            snapshot_payload['collection_config'] = config
        if external_source is not None:
            snapshot_payload['source'] = 'feishu_task'
            snapshot_payload['external_source'] = external_source
        self.env[PREFIX + 'snapshot'].create({'case_id': case.id, 'payload': snapshot_payload})
        return self.workbench_detail(case.id)

    @api.model
    def workbench_collection_replace_from_records(self, import_id, base, table, records):
        with self.env.cr.savepoint():
            return self._collection_replace_from_records(import_id, base, table, records)

    def _collection_replace_from_records(self, import_id, base, table, records):
        self._authorize()
        if any(not isinstance(v, str) or not v.strip() or len(v) > 200 for v in (import_id,base,table)):
            raise ValidationError('导入批次与来源标识无效')
        if not isinstance(records, list) or not records or len(records) > 500:
            raise ValidationError('导入记录数量无效')
        identities = [r.get('record') for r in records if isinstance(r, dict)]
        if len(identities) != len(records) or any(not isinstance(x,str) or not x.strip() or len(x)>200 for x in identities) or len(set(identities)) != len(identities):
            raise ValidationError('记录标识缺失或重复')
        for row in records:
            if not isinstance(row.get('status'), str) or not row['status'].strip() or len(row['status']) > 200:
                raise ValidationError('缺少源任务状态')
            if row.get('image_mime') not in ('image/png','image/jpeg','image/webp'):
                raise ValidationError('原图类型无效')
            try:
                raw=base64.b64decode(row.get('image_data') or '', validate=True)
                verified_image(raw,row['image_mime'])
            except (ValueError, TypeError) as exc:
                raise ValidationError('原图无效') from exc
        manifest = canonical_hash({'base':base,'table':table,'records':records})
        company = self.env.company
        self.env.cr.execute('SELECT pg_advisory_xact_lock(hashtext(%s), %s)', ('collection-company', company.id))
        self._collection_company_fence()
        receipts = self.env[PREFIX + 'collection.import.receipt'].sudo()
        previous = receipts.search([('company_id','=',company.id),('import_key','=',import_id)],limit=1)
        if previous:
            if previous.manifest_hash != manifest:
                raise ValidationError('导入批次内容已改变')
            return previous.case_ids_json
        cases = self.search([('company_id','=',company.id),('collection','=',True),('collection_archived','=',False)],order='id')
        for case in cases:
            self.workbench_collection_archive(case.id, True, case.revision)
        ids=[]
        for row in records:
            source={'base':base,'table':table,'record':row['record'],'status':row['status']}
            detail=self._collection_create(row, source)
            ids.append(detail['id'])
        receipts._append({'company_id':company.id,'import_key':import_id,'manifest_hash':manifest,'case_ids_json':ids})
        return ids

    @api.model
    def workbench_collection_archive(self, case_id, archived, expected_revision):
        if type(archived) is not bool or type(expected_revision) is not int:
            raise ValidationError('无效归档请求')
        case = self._wb_case(case_id, True)
        if case.company_id != self.env.company or not case.collection:
            raise AccessError('仅可管理当前公司的独立采集')
        self.env.cr.execute('SELECT pg_advisory_xact_lock(hashtext(%s), %s)', ('collection-company', case.company_id.id))
        self._collection_company_fence()
        case._lock()
        case._collection_case_fence()
        if case.revision != expected_revision:
            raise ValidationError('商品快照已改变')
        tasks = self.env[PREFIX + 'task'].search([('case_id', '=', case.id)])
        if archived and (any(t.state in ('queued', 'running', 'waiting') for t in tasks) or
                self.env[PREFIX + 'external.request'].search_count([('task_id','in',tasks.ids),('state','in',['unknown','dispatched'])])):
            raise ValidationError('存在活动或未确定任务')
        libraries = self.env[PREFIX + 'library'].search([('case_id','=',case.id),('company_id','=',case.company_id.id)])
        case._set({'collection_archived': archived})
        for library in libraries:
            library._set({'collection_archived': archived})
        return True

    @api.model
    def workbench_collection_hide_from_list(self, case_id, expected_revision):
        if type(expected_revision) is not int:
            raise ValidationError('无效请求')
        case = self._wb_case(case_id, True)
        if case.company_id != self.env.company or not case.collection:
            raise AccessError('仅可管理当前公司的独立采集')
        self.env.cr.execute('SELECT pg_advisory_xact_lock(hashtext(%s), %s)', ('collection-company', case.company_id.id))
        self._collection_company_fence()
        case._lock()
        case._collection_case_fence()
        if case.revision != expected_revision:
            raise ValidationError('商品快照已改变')
        tasks = self.env[PREFIX + 'task'].search([('case_id', '=', case.id), ('state', 'in', ['queued', 'running', 'waiting'])])
        if tasks:
            raise ValidationError('存在活动任务，请先处理')
        case._set({'collection_list_hidden': True})
        return True

    @api.model
    def workbench_collections(self):
        self._authorize('worker')
        return [{'id': c.id, 'name': c.name, 'company_id': c.company_id.id, 'company_name': c.company_id.name, 'snapshot': self._wb_snapshot(c).payload}
                for c in self.search([('collection', '=', True), ('collection_archived', '=', False), ('collection_list_hidden', '=', False)], order='id desc', limit=200)]

    @api.model
    def workbench_upload_garment(self, case_id, data, mime_type):
        case = self._wb_case(case_id, True)
        if mime_type not in ('image/png', 'image/jpeg', 'image/webp'):
            raise ValidationError('服装参考图支持 PNG、JPEG 或 WebP')
        try:
            raw = base64.b64decode(data, validate=True)
        except (ValueError, TypeError) as exc:
            raise ValidationError('图片编码无效') from exc
        verified_image(raw, mime_type)
        return self.env[PREFIX + 'artifact'].create({'case_id': case.id, 'data': data,
            'mime_type': mime_type, 'digest': hashlib.sha256(raw).hexdigest()}).id

    @api.model
    def workbench_snapshot_source_image(self, case_id, data, mime_type, expected_revision):
        case = self._wb_case(case_id, True)
        if case.variant_identity:
            raise ValidationError('变体商品原图必须从平台验证并同步')
        case._lock()
        if type(expected_revision) is not int or expected_revision != case.revision:
            raise ValidationError('商品快照已改变，请刷新后重试')
        snapshot = self._wb_snapshot(case)
        if not snapshot or not isinstance(snapshot.payload, dict):
            raise ValidationError('需要有效的商品快照')
        payload = dict(snapshot.payload)
        prior = payload.get('source_image_artifact_ids') or []
        if not isinstance(prior, list) or any(not str(ref).isdigit() for ref in prior) or len({str(ref) for ref in prior}) != len(prior):
            raise ValidationError('源图片引用无效')
        for ref in prior:
            artifact = self.env[PREFIX + 'artifact'].browse(int(ref)).exists()
            if not artifact or artifact.case_id != case or artifact.company_id != case.company_id or not artifact.data or artifact.mime_type not in ('image/png', 'image/jpeg', 'image/webp'):
                raise ValidationError('源图片归属或内容无效')
            artifact._authorize('worker')
            try:
                raw = base64.b64decode(artifact.data, validate=True)
            except (ValueError, TypeError) as exc:
                raise ValidationError('源图片编码无效') from exc
            verified_image(raw, artifact.mime_type)
            if hashlib.sha256(raw).hexdigest() != artifact.digest:
                raise ValidationError('源图片摘要不匹配')
        image_id = self.workbench_upload_garment(case.id, data, mime_type)
        payload['source_image_artifact_ids'] = [str(ref) for ref in prior] + [str(image_id)]
        self.env[PREFIX + 'snapshot'].create({'case_id': case.id, 'payload': payload})
        return self.workbench_detail(case.id)

    def _library_image(self, case, image):
        ref = str(image.get('artifact_id', ''))
        artifact = self.env[PREFIX + 'artifact'].browse(int(ref)).exists() if ref.isdigit() else self.env[PREFIX + 'artifact']
        if not artifact:
            raise ValidationError('图片缺少归档产物')
        artifact._authorize('worker')
        if artifact.case_id != case or artifact.company_id != case.company_id or not artifact.data or artifact.digest != image.get('sha256') or artifact.mime_type != image.get('mime_type'):
            raise ValidationError('图片归属、二进制、摘要或媒体类型未验证')
        raw = base64.b64decode(artifact.data)
        verified_image(raw, artifact.mime_type, (image['width'], image['height']))
        if hashlib.sha256(raw).hexdigest() != artifact.digest:
            raise ValidationError('归档图片摘要不匹配')
        return artifact

    @api.model
    def workbench_library_external_import(self, values):
        """Import one external result with its source image, without inventing a gallery."""
        self._authorize()
        if not isinstance(values, dict):
            raise ValidationError('无效的导入数据')
        keys = ('base', 'table', 'record')
        if any(not isinstance(values.get(k), str) or not values[k].strip() or len(values[k]) > 200 for k in keys):
            raise ValidationError('缺少外部记录标识')
        source_id = str(values.get('source_sku') or '').strip()
        url = str(values.get('url') or '').strip()
        parsed = urlsplit(url)
        if not source_id or len(source_id) > 200 or parsed.scheme not in ('https', 'http') or not parsed.hostname or parsed.username or parsed.password or len(url) > 2000:
            raise ValidationError('源商品或链接无效')
        external_key = canonical_hash([values[k] for k in keys])
        self.env.cr.execute('SELECT pg_advisory_xact_lock(hashtext(%s), hashtext(%s))', (str(self.env.company.id), external_key))
        library = self.env[PREFIX + 'library']
        existing = library.search([('company_id', '=', self.env.company.id), ('external_key', '=', external_key)], limit=1)
        if existing:
            return existing.id
        external_batch_key = canonical_hash([values['base'], values['table']])
        self.env.cr.execute('SELECT pg_advisory_xact_lock(hashtext(%s), hashtext(%s))', (str(self.env.company.id), external_batch_key))
        batches = self.env[PREFIX + 'batch']
        batch = batches.search([('company_id', '=', self.env.company.id), ('external_import_key', '=', external_batch_key)], limit=1)
        if not batch:
            batch = batches.create({'name': '飞书爆款结果导入', 'external_import_key': external_batch_key})
        case = self.create({'name': source_id, 'batch_id': batch.id, 'collection': True})
        image_id = self.workbench_upload_garment(case.id, values.get('image_data'), values.get('image_mime'))
        source = {'source_sku': source_id, 'source_identifier_type': '商品ID', 'source_image_artifact_ids': [image_id], 'source': 'feishu_base',
                  'base': values['base'], 'table': values['table'], 'record': values['record']}
        snapshot = self.env[PREFIX + 'snapshot'].create({'case_id': case.id, 'payload': source})
        candidate = {'url': url}
        for field in ('platform', 'title', 'sold_display', 'review_count', 'rating', 'visual_features', 'price', 'listed_at'):
            val = values.get(field)
            if val is not None and val is not False:
                candidate[field] = str(val)[:4000]
        payload = {'images': [], 'source_snapshot': source, 'research_candidate': candidate, 'case_id': case.id,
                   'reference_item_id': url, 'selected_variant': {}, 'completeness_status': 'unavailable'}
        bounded(payload)
        return library._append({'company_id': case.company_id.id, 'case_id': case.id, 'snapshot_id': snapshot.id,
            'external_key': external_key, 'external_batch_key': external_batch_key, 'result_hash': canonical_hash(payload), 'name': url[:200],
            'site_id': 'unknown', 'payload': payload}).id

    @api.model
    def workbench_library_research_import(self, case_id, task_id, candidate_images):
        case = self._wb_case(case_id, True)
        case._lock()
        if case.collection and case.collection_archived:
            raise ValidationError('已归档采集不能添加图库')
        task = self.env[PREFIX + 'task'].browse(int(task_id)).exists()
        if not task or task.case_id != case or task.task_type != 'research.bestseller' or task.state != 'succeeded' or task.snapshot_id != self._wb_snapshot(case):
            raise ValidationError('研究任务或快照无效')
        task._authorize('worker')
        config = task.snapshot_id.payload.get('collection_config')
        if not case.collection or not isinstance(config, dict) or not isinstance(candidate_images, dict):
            raise ValidationError('缺少独立采集配置')
        validate_result('research.bestseller', task.result)
        source = task.snapshot_id.payload
        refs = source.get('source_image_artifact_ids') or []
        if not source.get('source_sku') or not refs:
            raise ValidationError('缺少源商品或原图')
        for ref in refs:
            art = self.env[PREFIX + 'artifact'].browse(int(ref)).exists() if str(ref).isdigit() else self.env[PREFIX + 'artifact']
            if not art or art.case_id != case or art.company_id != case.company_id or not art.data:
                raise ValidationError('源图归属无效')
            raw = base64.b64decode(art.data, validate=True)
            verified_image(raw, art.mime_type)
            if hashlib.sha256(raw).hexdigest() != art.digest:
                raise ValidationError('源图摘要不匹配')
        supplemental=task.result.get('supplemental_candidates',[])
        card_v3=config.get('strategy') in ('card-visual-v3','card-home-v4','shein-home-v1')
        if supplemental and not card_v3:
            raise ValidationError('当前采集策略不支持补充候选')
        candidates = task.result['candidates'] + supplemental
        expected_keys = {c['platform'] + ':' + c['item_id'] for c in candidates}
        if set(candidate_images) - expected_keys:
            raise ValidationError('未知候选图片')
        library = self.env[PREFIX + 'library']
        done, pending = [], []
        for candidate in candidates:
            key = candidate['platform'] + ':' + candidate['item_id']
            external_key = canonical_hash(['research_candidate', case.company_id.id, task.id, candidate['platform'], candidate['item_id']])
            existing = library.search([('company_id', '=', case.company_id.id), ('external_key', '=', external_key)], limit=1)
            if existing:
                done.append(existing.id)
                continue
            image = candidate.get('images') or candidate.get('image') or candidate_images.get(key)
            images = image if isinstance(image, list) else [image] if image else []
            try:
                if candidate['platform'] != config['platform'] or not images:
                    raise ValidationError('平台或图片不可用')
                if config.get('strategy')=='shein-home-v1':
                    tier='supplemental' if candidate in supplemental else 'threshold_met'
                    if not card_v3_tier_valid(candidate,config['min_sold'],tier,allow_plus_hundred=True,platform='shein-us'):
                        raise ValidationError('候选销量分层不符合任务门槛')
                    reviews = candidate.get('review_count')
                    if reviews is None: reviews = _visible_review_lower_bound(candidate.get('reviews_display'))
                    metrics = [(reviews, config['min_reviews'])]
                elif config['platform'] == 'mercado-libre-mx':
                    if card_v3:
                        tier='supplemental' if candidate in supplemental else 'threshold_met'
                        if not card_v3_tier_valid(candidate,config['min_sold'],tier,allow_plus_hundred=config.get('strategy')=='card-home-v4'):
                            raise ValidationError('候选销量分层不符合任务门槛')
                        metrics=[]
                    else:
                        metrics = [(candidate.get('sold_lower_bound'), config['min_sold'])]
                else:
                    reviews = candidate.get('review_count')
                    if reviews is None:
                        reviews = _visible_review_lower_bound(candidate.get('reviews_display'))
                    metrics = [(reviews, config['min_reviews']), (candidate.get('rating'), config['min_rating'])]
                if any(type(val) not in (int, float) or not math.isfinite(val) or val < minimum for val, minimum in metrics):
                    raise ValidationError('候选指标缺失或不足')
                if config['platform'] == 'shein-us' and candidate['rating'] is not None and candidate['rating'] > 5:
                    raise ValidationError('评分无效')
                sources = [item.get('source') for item in images]
                if [item.get('order') for item in images] != list(range(len(images))):
                    raise ValidationError('图片顺序不连续')
                manifest = candidate.get('gallery_image_urls')
                if manifest is None:
                    manifest = [candidate.get('image_url')]
                if not isinstance(manifest, list) or not manifest or sources != manifest or len(set(sources)) != len(sources):
                    raise ValidationError('图片来源与候选证据不一致')
                # Older collectors used the product ID for every gallery image.
                # Repair only that recognizable legacy shape in the derived
                # library record; leave the original task evidence untouched.
                if len(images) > 1 and all(item.get('image_id') == candidate['item_id'] for item in images):
                    images = [dict(item, image_id=f"{candidate['item_id']}:{index}") for index, item in enumerate(images)]
                for item in images:
                    url = urlsplit(item.get('source', ''))
                    if url.scheme != 'https' or not url.hostname or url.username or url.password:
                        raise ValidationError('图片来源无效')
                    self._library_image(case, item)
                gallery = {'schema_version':'1', 'evidence':task.result['evidence'], 'reference_item_id':candidate['item_id'], 'selected_variant':{'candidate':candidate['item_id']}, 'observed_at':candidate['observed_at'], 'gallery_manifest_hash':canonical_hash(images), 'completeness_status':'complete', 'images':images}
                validate_gallery(gallery)
                payload = dict(gallery, source_snapshot=source, research_candidate=candidate)
                bounded(payload)
                with self.env.cr.savepoint():
                    entry = library._append({'company_id':case.company_id.id,'case_id':case.id,'task_id':task.id,'snapshot_id':task.snapshot_id.id,'external_key':external_key,'result_hash':canonical_hash([task.id,candidate['platform'],candidate['item_id']]),'name':candidate['item_id'],'site_id':source['site_id'],'payload':payload})
                    check = library.search([('id', '=', entry.id), ('company_id', '=', case.company_id.id), ('external_key', '=', external_key)], limit=1)
                    if not check or check.task_id != task or check.snapshot_id != task.snapshot_id or check.payload != payload:
                        raise ValidationError('图库写入回读失败')
                done.append(check.id)
            except (ValidationError, ValueError, TypeError, KeyError):
                pending.append(key)
        if pending:
            task._set({'status_detail':{'code':'research_images_pending','message':'部分候选缺少可验证图片或不满足条件，等待处理。','pending_count':len(pending)}})
        elif (task.status_detail or {}).get('code') == 'research_images_pending':
            task._set({'status_detail':None})
        return {'library_ids':done, 'pending':pending}

    @api.model
    def workbench_library_recollect(self, library_id):
        library = self.env[PREFIX + 'library'].browse(int(library_id)).exists()
        if not library:
            raise ValidationError('图库记录不存在')
        case = self._wb_case(library.case_id.id, True)
        case._lock()
        if library.company_id != case.company_id or library.collection_archived or not case.collection or case.collection_archived:
            raise ValidationError('图库记录不可采集')
        if library.snapshot_id != self._wb_snapshot(case):
            raise ValidationError('图库快照已过期')
        candidate = (library.payload or {}).get('research_candidate') or {}
        item_id, url = candidate.get('item_id'), candidate.get('url')
        parsed = urlsplit(url or '')
        if not item_id or parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password:
            raise ValidationError('图库缺少可核验的候选商品链接')
        variant = {'candidate': item_id, 'product_url': url, 'library_id': str(library.id)}
        tasks = self.env[PREFIX + 'task'].search([('case_id', '=', case.id), ('snapshot_id', '=', library.snapshot_id.id), ('task_type', '=', 'gallery.collect'), ('state', 'not in', ['failed', 'cancelled'])])
        existing = tasks.filtered(lambda t: t.payload.get('reference_item_id') == item_id and t.payload.get('selected_variant') == variant)[:1]
        if existing:
            return {'task_id': existing.id, 'reused': True}
        task_id = case.workbench_task(case.id, 'gallery.collect', {'reference_item_id': item_id, 'selected_variant': variant})
        return {'task_id': task_id, 'reused': False}

    @api.model
    def workbench_library_import(self, case_id, task_id, result_hash):
        case = self._wb_case(case_id, True)
        case._lock()
        if case.collection and case.collection_archived:
            raise ValidationError('已归档采集不能添加图库')
        task = self.env[PREFIX + 'task'].browse(int(task_id)).exists()
        task._authorize('worker')
        if not task or task.case_id != case or task.snapshot_id != self._wb_snapshot(case) or task.task_type != 'gallery.collect' or task.state != 'succeeded' or canonical_hash(task.result) != result_hash:
            raise ValidationError('仅可导入当前快照的已完成且未改变的图库结果')
        validate_gallery(task.result)
        if any(task.result[k] != task.payload[k] for k in ('reference_item_id', 'selected_variant')):
            raise ValidationError('采集结果商品或 SKU 不匹配')
        for image in task.result['images']:
            origin = urlsplit(image['source'])
            if origin.scheme not in ('https', 'http') or not origin.hostname or origin.username or origin.password:
                raise ValidationError('图库图片必须保留有效的 HTTP(S) 来源链接')
            self._library_image(case, image)
        library = self.env[PREFIX + 'library']
        existing = library.search([('task_id', '=', task.id), ('result_hash', '=', result_hash)], limit=1)
        if existing:
            return existing.id
        variant = task.result['selected_variant']
        origin_id = str(variant.get('library_id') or '')
        if origin_id:
            origin = library.browse(int(origin_id)).exists() if origin_id.isdigit() else library
            candidate = (origin.payload or {}).get('research_candidate') if origin else None
            if (not origin or origin.case_id != case or origin.company_id != case.company_id
                    or origin.snapshot_id != task.snapshot_id or not isinstance(candidate, dict)
                    or candidate.get('item_id') != task.result['reference_item_id']
                    or candidate.get('url') != variant.get('product_url')):
                raise ValidationError('补采来源记录或商品不匹配')
            candidates = [candidate]
        else:
            candidates = self._library_candidates(case, task.snapshot_id, task.result['reference_item_id'])
        payload = dict(task.result, source_snapshot=self._wb_snapshot(case).payload,
                       research_candidate=candidates[0] if len(candidates) == 1 else {})
        bounded(payload)
        return library._append({'company_id': case.company_id.id, 'case_id': case.id, 'task_id': task.id,
            'snapshot_id': task.snapshot_id.id, 'result_hash': result_hash, 'name': task.result['reference_item_id'],
            'site_id': str(self._wb_snapshot(case).payload.get('site_id') or 'unknown'), 'payload': payload}).id

    @api.model
    def _library_candidates(self, case, snapshot, reference):
        tasks = self.env[PREFIX + 'task'].search([('case_id', '=', case.id), ('snapshot_id', '=', snapshot.id),
            ('task_type', '=', 'research.bestseller'), ('state', '=', 'succeeded')], order='id desc')
        matches = []
        for task in tasks:
            matches.extend(c for c in (task.result or {}).get('candidates', [])
                           if isinstance(c, dict) and str(c.get('item_id') or '') == str(reference))
        return matches

    @api.model
    def workbench_library(self, query='', site='', filters=None, offset=0):
        self._authorize('worker')
        domain = [('collection_archived', '=', False)]
        if site:
            domain += [('site_id', '=', site)]
        if query:
            domain += [('name', 'ilike', str(query)[:200])]
        entries = self.env[PREFIX + 'library'].search(domain, order='id desc')
        by_id = {e.id: e for e in entries}
        superseded = set()
        for entry in entries:
            variant = (entry.payload or {}).get('selected_variant') or {}
            ref = str(variant.get('library_id') or '') if isinstance(variant, dict) else ''
            original = by_id.get(int(ref)) if ref.isdigit() else None
            if (original and original.id < entry.id and entry.task_id.task_type == 'gallery.collect'
                    and entry.case_id == original.case_id and entry.snapshot_id == original.snapshot_id
                    and entry.company_id == original.company_id and entry.name == original.name):
                superseded.add(original.id)
        rows = []
        historical_evidence = {}

        def verified_historical_rating(entry, candidate):
            task = entry.task_id
            if task.task_type != 'research.bestseller' or task.state != 'succeeded':
                return None
            platform, item = candidate.get('platform'), candidate.get('item_id')
            if not isinstance(platform, str) or not isinstance(item, str):
                return None
            result = task.result or {}
            matches = [c for c in (result.get('candidates') or []) + (result.get('supplemental_candidates') or [])
                       if c.get('platform') == platform and c.get('item_id') == item]
            if len(matches) != 1 or matches[0].get('rating') is not None:
                return None
            if task.id not in historical_evidence:
                evidence = [x for x in result.get('evidence', []) if x.get('id') == 'collector']
                source = evidence[0].get('source', '') if len(evidence) == 1 else ''
                match = re.fullmatch(r'odoo-artifact:(\d+):sha256:([0-9a-f]{64})', source)
                value = None
                if match:
                    artifact = self.env[PREFIX + 'artifact'].browse(int(match.group(1))).exists()
                    if artifact and artifact.case_id == entry.case_id and artifact.company_id == entry.company_id and artifact.digest == match.group(2) and artifact.data:
                        try:
                            value = (base64.b64decode(artifact.data, validate=True), match.group(2), evidence[0].get('observed_at'))
                        except (ValueError, TypeError):
                            pass
                historical_evidence[task.id] = value
            evidence = historical_evidence[task.id]
            if not evidence:
                return None
            rating = historical_rating(evidence[0], evidence[1], platform, item)
            return (rating, evidence[2]) if rating is not None else None

        for e in entries:
            if e.id in superseded:
                continue
            payload = e.payload or {}
            source = payload.get('source_snapshot') or {}
            candidate = payload.get('research_candidate') or {}
            origin = 'import_frozen' if candidate else 'unknown'
            if 'research_candidate' not in payload:
                matches = self._library_candidates(e.case_id, e.snapshot_id, e.name)
                candidate = matches[0] if len(matches) == 1 else {}
                origin = 'legacy_backfill' if candidate else 'unknown'
            def value(*keys):
                for key in keys:
                    if candidate.get(key) is not None:
                        return candidate[key]
                return False
            restored_rating = verified_historical_rating(e, candidate) if value('rating_display', 'rating') is False else None
            rows.append({'id': e.id, 'name': e.name, 'site_id': e.site_id, 'result_hash': e.result_hash,
                'payload': payload, 'company_id': e.company_id.id,
                'research_metadata_origin': origin,
                'source_sku': source.get('source_sku') or source.get('seller_sku') or False,
                'source_image_artifact_ids': source.get('source_image_artifact_ids') or [],
                'price': value('price_display', 'price'), 'listed_at': value('listed_at', 'listing_date'),
                'platform': value('platform'), 'title': value('title'), 'url': value('url'),
                'sales_text': value('sold_display'), 'reviews_text': value('reviews_display', 'review_count'),
                'rating_text': restored_rating[0] if restored_rating else value('rating_display', 'rating'),
                'rating_observed_at': restored_rating[1] if restored_rating else candidate.get('rating_observed_at') or candidate.get('observed_at'),
                'price_observed_at': candidate.get('price_observed_at'),
                'rating_source': 'verified_historical_evidence' if restored_rating else candidate.get('rating_source'),
                'visual_features': value('visual_features', 'visual_category')})
        if filters is None:
            return {'rows': rows}
        filters = filters if isinstance(filters, dict) else {}
        query_text = str(filters.get('query') or '').strip().casefold()
        platform = platform_key(filters.get('platform'))
        start = str(filters.get('dateFrom') or '')
        end = str(filters.get('dateTo') or '')
        rows = [r for r in rows if (not query_text or any(query_text in str(r.get(k) or '').casefold()
                    for k in ('source_sku', 'title', 'url', 'visual_features'))) and
                (not platform or platform == platform_key(r.get('platform'))) and
                (not start or (r['listed_at'] and str(r['listed_at'])[:10] >= start)) and
                (not end or (r['listed_at'] and str(r['listed_at'])[:10] <= end))]
        offset = max(0, int(offset or 0))
        return {'rows': rows[offset:offset + 50], 'total': len(rows), 'limit': 50}

    @api.model
    def workbench_library_generate(self, case_id, library_id, indices, source_artifact_ids, expected_revision=None):
        case = self._wb_case(case_id, True)
        case._lock()
        if type(expected_revision) is not int or expected_revision != case.revision:
            raise ValidationError('目标商品快照已改变或版本缺失，请刷新并重新选择图库')
        library = self.env[PREFIX + 'library'].browse(int(library_id)).exists()
        if not library:
            raise ValidationError('图库不存在')
        library._authorize('worker')
        if library.company_id != case.company_id:
            raise AccessError('图库与目标商品必须属于同一公司')
        if library.collection_archived:
            raise ValidationError('该图库已归档')
        images = library.payload['images']
        if not isinstance(indices, list) or not indices or any(type(i) is not int or i < 0 or i >= len(images) for i in indices) or len(set(indices)) != len(indices):
            raise ValidationError('请选择不重复的有效图片')
        from .generation_sources import snapshot_sources
        try:
            source_artifact_ids = snapshot_sources(self._wb_snapshot(case).payload)
        except ValueError as exc:
            raise ValidationError('当前商品快照缺少可用原图，请先补齐商品原图') from exc
        artifacts = self.env[PREFIX + 'artifact']
        for ref in source_artifact_ids:
            source = artifacts.browse(int(ref)).exists()
            source._authorize('worker')
            if not source or source.case_id != case or source.company_id != case.company_id or not source.data or source.mime_type not in ('image/png', 'image/jpeg', 'image/webp'):
                raise ValidationError('源服装必须为当前商品的已归档图片')
            try:
                raw = base64.b64decode(source.data, validate=True)
            except (ValueError, TypeError) as exc:
                raise ValidationError('源服装图片编码无效') from exc
            verified_image(raw, source.mime_type)
            if hashlib.sha256(raw).hexdigest() != source.digest:
                raise ValidationError('源服装归档图片摘要不匹配')
        clones = []
        for index in indices:
            original = self._library_image(library.case_id, images[index])
            clones.append(artifacts._append({'case_id': case.id, 'company_id': case.company_id.id,
                'data': original.data, 'digest': original.digest, 'mime_type': original.mime_type,
                'library_origin_id': library.id, 'library_artifact_id': original.id}))
        plan_payload = {'schema_version': '1', 'operation': 'garment_swap', 'case_id': case.id,
            'snapshot_id': self._wb_snapshot(case).id, 'input_revision': expected_revision, 'library_id': library.id,
            'library_result_hash': library.result_hash, 'selected_indices': indices, 'target_count': len(indices),
            'source_artifact_ids': [str(i) for i in source_artifact_ids], 'target_artifact_ids': [str(a.id) for a in clones],
            'preserve': ['identity', 'pose', 'accessories', 'scene', 'lighting', 'framing']}
        plan = artifacts.create({'case_id': case.id, 'payload': plan_payload, 'digest': bounded(plan_payload)})
        task_ids = [self.workbench_task(case.id, 'image.generate', {'source_artifact_ids': plan_payload['source_artifact_ids'],
            'target_artifact_id': str(a.id), 'generation_plan_id': str(plan.id)}) for a in clones]
        return {'task_ids': task_ids, 'generation_plan_id': plan.id, 'target_count': len(indices)}
