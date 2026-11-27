# Request-bound local research bridge

`{"operation":"binding_status","data":{}}` returns the current request's bound tab and window, plan match, completed query count, ordered queries, and live-guard status. This is a fenced read, not permission to switch tabs; the collector displays its own tab and match state. A tab ID alone does not establish current browser liveness.

## Homepage research (`card-home-v4`)

This section applies only when the snapshot explicitly selects `card-home-v4`.
Read the task source image first. Before binding the search plan, call
`prepare_suggestions` with `source_artifact_id`, `viewed_source_sha256`,
`category`, and `anchor`. Use an accessible Mercado México browser to read the
actual search input and visible suggestions. Call `record_suggestions` with
`page_url`, `prefix`, `visible_input`, `state`, `suggestions`, and `screenshot`;
each suggestion has `text` and its one-based visible `position`. The transport
encodes the screenshot as base64. Use `observed` only for an actual visible
nonempty list; use `unavailable` or `unverified` when it cannot be confirmed.
Do not save a verification page or send a CAPTCHA image. If no screenshot can
be captured because the browser or page is unavailable, report
`suggestions_unavailable` with `reason` equal to `browser_unavailable`,
`page_unavailable`, or `suggestions_unavailable`. This is a host report, not
an observed empty list. A CAPTCHA is not an unavailable-suggestions report.

Call `seal_suggestions` with the source `visual_profile`; its response contains
the initial three query texts and their source labels. Submit `plan` using those
texts in order, `strategy: "card-home-v4"`, and the `design_profile` schema
below. For this strategy the second and third actual query texts can differ from
the source profile's image-derived `query_seeds`. The first query is the same
category anchor. Platform suggestions are not a claim of search popularity.

Use `binding_status.card_v3.next_page` and `queries` for the next task-owned
search. Search and scroll only the first result page. When all selected home
pages are complete and expansion is needed, call `home_expansion_options`
with empty data; send its returned `queries` unchanged to `seal_expansion`.
An empty returned list means no further recorded related suggestion is
available. Never navigate a next-page link. The host freezes a queue from
clearly attributable card sales over 100, verifies detail sales, and separates
the user's configured threshold from supplemental candidates.

For an interactive CAPTCHA, first wait briefly for noninteractive completion.
Before each permitted interaction, call `reserve_captcha_attempt` with `stage`
(`suggestions`, `search`, `detail`, `visual`, or `gallery`) and a unique
`action_id`. A confirmed response returns attempt 1 or 2; repeating an ID is
idempotent. The task-wide limit is two attempts, including across stages and
leases. Never submit or log verification content or screenshots. If the
challenge requests a message/email code, the browser is unavailable, or the
attempts are exhausted, call `arm_home_verification` with stage `search` or
`detail` when the current page is already bound, then click **验证完成后自动恢复任务**
in the collector, call `page_blocked` with `captcha_required`, and stop. If the
watch cannot be armed, preserve the wait and use the manual recovery entry.
If `binding_status.failure_code` is `browser_unavailable`, report that code
through `page_blocked` and stop. Page identity must be checked after any
successful verification.

## Mercado Libre candidate photos and visual decisions

For `visual-v2`, the plan contains the fields in the plan example below plus
`strategy: "visual-v2"` and `design_profile`. `design_profile` has exactly
`primary`, `secondary`, `styles`, and `occasions`. `primary` has exactly
`category`, `components`, `length`, `shoulders`, `structure`, `silhouette`,
`fit`, and `sleeve_length`. Use the same stable English labels for source and
candidate observations, and the internal category code for `category`. Use
`null` for unknown; use `{ "status": "not_applicable", "value": null }`
only where a field really does not apply. For example, components can be
not applicable to a single dress, while an obscured length is unknown.
`secondary`, `styles`, and `occasions` are arrays of visible, specific labels,
or `null` if unknown. Do not infer evidence from the title or use color/size.
For `visual-v2`, submit the confirmed photo receipts using exactly
`submission_id`, `receipts`, `candidate_primary`, `secondary`, `styles`,
`occasions`, `critical_conflict`, and `features`; the host computes the outcome.
The same `candidate_primary` fields and label vocabulary are required. Record
`critical_conflict` only when conflicting construction is visibly supported.
The task's configured sales threshold applies. Finish only after reaching its
target or exhausting the initial and available expansion searches, with no
detail-count or elapsed-time stopping rule. Existing v1 instructions below
apply only to historical tasks.

Once the collector confirms a candidate's sales threshold, open the first item in that candidate's visible magnified gallery and choose **下载候选首图供款式核验**. The extension reads the first photo, skipping any leading video, and gives the host a Chrome download receipt. The host validates the assigned file, saves the image digest, and issues a persistent photo receipt tied to the task, candidate and selected variant. For an additional angle, display that gallery position and enter its position before downloading.

Call `visual_candidate_status` with empty data to obtain the pending candidate and the staged paths, sources and receipts for its verified images. Read the candidate image file and the source garment image. To save the verdict, call `submit_visual_verdict` with `submission_id`, `receipts` (first photo first), `status`, `reason`, `category`, `match_level` and `features`. A qualified result uses `qualified` with a null reason. A visual rejection uses `rejected` with `visual_category_mismatch` or `imagery_ambiguous_or_inaccessible`, and null match level and features. Use the same submission ID and data if the response is lost. After a confirmed verdict, restore progress in the collector before requesting the next candidate.

The host fences the current lease and verifies the saved image bytes and candidate order before committing the decision and its receipt together. The browser guard is required when obtaining the photo. The committed photo can be reviewed and submitted after the browser focus has moved. A qualified first photo is reusable in the full gallery when its source URL, variant and file digest match.

## Search and full galleries

After connecting the collector, use **恢复本任务采集进度** to load its bound plan and checkpoints. `saved_query_groups` counts durably saved groups. After staging and submitting qualified candidates, read `binding_status.gallery_step` in the same invocation. It provides the next candidate identity, detail URL, and where applicable image source and zero-based order. Submit the requested manifest or image, then read the status again. For `reconcile_image`, send the identity and order with `artifact_id: null` to look up the original scoped receipt. At `finish`, submit the empty finish operation. A resolved request reports `complete`.

For a manifest, open the requested qualified product in the task tab. For Mercado Libre, open the selected variant’s first full-size photo and choose **从第一张放大图逐项核验并回传图库**. The collector advances visible viewer controls and saves each observed photo in order. When the viewer does not expose a reliable count or navigation control, use **记录当前可见原图** for each full-size image of the selected variant in page order. **回传有序图库清单与详情截图** records the ordered links and an actual capture of that tab. **保存此候选全部原图并入库** downloads the confirmed candidate’s original images with Chrome and checks each business receipt before continuing. If this button is unavailable, for each image step use Chrome’s Save Link As on the matching verified link and save it to `gallery_step.save_path`. Call `image_saved` with only the specified identity and zero-based order. The host verifies the file, submits it with the saved screenshot, and checks the business artifact readback before advancing. On restarting the interaction, read the new status to obtain the current save path.

When three query groups match the prior verified checkpoint, the service restores the already reviewed candidate prefix for this same Odoo task. The collector may then show one or more saved details without a click in the new session. Read `binding_status.details` for the saved count, restored count, source request and epoch, and last verified identity. When they agree with the collector, click **在任务标签打开下一候选**. The broker selects the next product from the saved candidate order. The collector opens that product on the bound tab and verifies the page identity before bringing it to the foreground. A freshly restored rejection counts as a reviewed candidate.

For `candidate_images_pending`, read `gallery_step` first. Reconcile any uncertain image receipt before restoring the browser binding. With a live matching guard, continue with the indicated manifest, image, or finish. After an extension reload or browser restart, use the dedicated control page to confirm the marked window and create a replacement task tab if the host requests a rebind. In the task-bound collector, choose **打开当前合格商品图库**. The host issues a challenge for the next candidate in the archived result. The extension opens the detail on the bound tab and verifies the active tab, product identity and detail URL. A fresh gallery confirmation establishes the guard. Continue from the original manifest or image receipt. This stage uses the archived evidence directly.

During initial research, after the collector has complete evidence, connect the task-bound collector and click `回传当前任务证据`. Once it confirms staging, call `{"operation":"submit_staged","data":{"observed_at":"2026-11-20T00:00:00Z"}}`, replacing the example timestamp with the real UTC observation time. The browser extension sends the JSON to the local broker; the worker reads only the exact current request and lease epoch from its private ledger. It requires a live guard and uses the existing evidence validation and submission path. A staging confirmation is not a successful business submission. No Downloads access is required.

The dispatcher may be constructed with `bridge_factory`, which creates a `ReturnBridge` bound to the selected request. It opens a private Unix socket only for that invocation. Codex receives `OPTIMIZATION_RETURN_SOCKET` and a minimal environment without the Odoo API credential. The parent owns the credential, applies the lease fence, and calls the existing collector implementation. This is a local integration primitive, not an enabled service.

The child sends a JSON object to `python -m optimization_worker.research_bridge_socket --socket "$OPTIMIZATION_RETURN_SOCKET"` on standard input. The fields are exactly `operation` and `data`; the request ID is bound by the server. Operations are `plan`, `submit`, `manifest`, `image`, and `finish`. `plan` data is the collector plan object. `submit` data has `evidence` and UTC `observed_at`. `manifest` data has `identity`, `manifest`, and base64 `screenshot`; `image` data has `identity`, base64 `file`, base64 `screenshot`, `source`, and `order`; `finish` uses an empty object. The response contains only `accepted`. Existing collector and image upload validators remain authoritative; the strict finish path requires full manifests and matching image receipts.

The socket lives in a mode 0700 temporary directory and uses mode 0600. These controls exclude other OS users; they do **not** protect the credential or socket from other processes running as the same macOS user. Do not treat this as a privilege boundary or expose it to untrusted same-user processes. The input protocol has size limits and sanitizes errors, but caller identity is not independently authenticated.

The local service checks Chrome, visual input and the authenticated callback before taking new work. The Odoo task and its receipt ledger determine whether a result has been committed.

The socket call timeout is 600 seconds. After an interrupted evidence or image submission, query the original write intent. For scoped uploads, use `reconcile_evidence` with `{"artifact_id":null}` or `reconcile_image` with `{"identity":"<bound candidate>","artifact_id":null,"order":0}`, substituting the actual order. If a receipt already supplies an artifact ID, pass it as a decimal string. Continue only after the response is accepted.

For an observed CAPTCHA, expired login, or unreadable page, send `page_blocked` with `{"code":"captcha_required"}`, `login_required`, or `page_unreadable` respectively. An accepted response saves the reason on the original task so the operator can resume it after the condition clears.

Live probe adapter subprocesses have independent timeout settings: `OPTIMIZATION_PROBE_VISION_TIMEOUT`, `OPTIMIZATION_PROBE_CHROME_TIMEOUT`, and `OPTIMIZATION_PROBE_CALLBACK_TIMEOUT`, in seconds, default 120 and bounded to (0, 300]. `OPTIMIZATION_PROBE_MAX_AGE` controls the maximum allowed evidence age in seconds, default 30 and bounded to (0, 300]. The longest adapter timeout limits any individual probe, while evidence must be produced after its nonce was issued and remain fresh after all three probes complete. Vision runs first so a 90-second visual probe does not immediately stale a previously collected browser result. All probe executables must be trusted real adapters; these parameters do not themselves establish readiness. The separate Worker thread continues handling existing leases while probes run; new claims stay gated.

`OPTIMIZATION_USE_LIVE_ADAPTERS=1` opts into the built-in Chrome receipt probe, randomized multimodal image-input probe, and disposable authenticated callback transport probe. The parent service uses its configured real Odoo credential; it is not passed to the Codex probes. A successful disposable transport roundtrip does not establish business artifact upload, lease-fenced checkpoint, or completion capability. Configure `OPTIMIZATION_PROBE_CASE_ID` as an explicit positive case ID in an authorized company. The callback readiness check requires both the transport roundtrip and an artifact ORM write/read rollback for that case; Odoo checks the worker role and case/company authorization. Its evidence explicitly sets `task_completion_verified` to false, so readiness means only that these preflight checks passed, not that the task completion flow has been verified. Without a case ID, the callback check fails closed. This integration remains disabled unless explicitly configured and deployed. Successful checks are cached per process for a configurable maximum of 30 seconds (`OPTIMIZATION_PROBE_CACHE_TTL`, default 30) and never past the oldest evidence expiry; concurrent callers share one check, and process restart clears the cache.

`probe_diagnostics(client, nonce, case_id)` combines the disposable transport probe with an explicitly authorized case's `artifact-rollback` probe. The latter creates a small JSON artifact and a tiny binary artifact inside an Odoo savepoint, forces a database readback and digest check, then deliberately rolls back before returning. It leaves no artifact records; database sequences can advance. It proves the currently authorized worker could execute the artifact ORM operations inside that transaction, not that a separate HTTP artifact upload, durable commit, task checkpoint, or task completion would succeed. Its `task_completion_verified` remains false and it is not wired to claim readiness.

When the worker has no active lease, queue polling uses `OPTIMIZATION_IDLE_POLL_SECONDS` (default 60 seconds; range 5–300). The service does not probe the executor for dispatch unless a research host request is pending; a newly queued task may therefore wait up to the idle polling interval before discovery. Active leases retain the normal five-second polling and heartbeat cycle. Each new claim and actual dispatch still requires fresh capability evidence.

Before a new claim, the worker calls the authenticated read-only research queue precheck. It returns only `claimable`, never task IDs or product data. It considers authorized companies, queued work and expired running leases, and excludes archived cases, unmet dependencies and unresolved external requests. Empty or unavailable precheck results skip browser/vision probes and claims. The result is advisory: queue state can change, so the worker still performs the normal atomic claim and lease fencing after capability checks. An existing locally journaled lease continues its heartbeat and recovery independently of the queue precheck.

Collector evidence may include `observed_at` on each `details` item. It must be an ISO-8601 datetime with an explicit timezone and cannot be later than the outer `submit.observed_at`, which is the actual batch evidence observation time. Each supplied detail time is normalized to UTC in its candidate result and marked `observation_time_scope: detail`. Older evidence without that field remains compatible: the candidate retains the batch timestamp and is explicitly marked `observation_time_scope: batch`; do not present it as an independently observed detail time. The source evidence JSON retains the original per-detail timestamp for audit. Company library payloads retain this scope under `research_candidate`.

Each Dispatcher invocation configures a fresh, randomly named Codex permission profile extending `:read-only`, with a writable request-specific temporary directory, a network-proxied allowlist containing only that request's Unix socket, and shell read denial for the workspace `.local` directory plus selected known credential paths. It does not use `danger-full-access`. This constrains sandboxed shell commands only: MCP, browser and computer-use have independent permissions, same-user processes may read files outside this sandbox, and the deny list cannot cover unknown credential locations. The exact profile must be tested on the target Codex version and configuration; legacy `sandbox_mode` or managed policy may take precedence. Never rely on prompt wording as a credential boundary.

## Plan schema and safe validation feedback

`plan` 的 `data` 必须恰好包含 `platform`、`queries`、`category`、`result_limit`、`source_artifact_id`、`viewed_source_sha256`、`visual_profile`。`category` 是内部 category code（如 `dress`、`womens_pants_set`），并且与 `visual_profile.category_code` 一致；不得填写 `vestido` 或其他翻译。以下示例展示字段结构，源图 ID、SHA256、平台和数量必须取自本次任务，不得照抄。视觉属性必须依据实际观察：

```json
{
  "platform": "mercado-libre-mx",
  "queries": ["vestido camisero", "vestido camisero encaje botones", "vestido camisero romántico"],
  "category": "dress",
  "result_limit": 10,
  "source_artifact_id": "222",
  "viewed_source_sha256": "bc44049920df9d5c511855581c1d59c03a1d7da1fcb3963e8c8389a0dd67ada3",
  "visual_profile": {
    "category_code": "dress", "category": "vestido", "subtype": "vestido camisero",
    "silhouette": "falda de corte A", "fit": "entallado en cintura y falda amplia", "color": "rosa",
    "style_codes": ["romantic", "elegant"], "style": ["romántico", "elegante"],
    "selling_point_codes": ["shirt_collar", "short_sleeve", "button_front", "lace", "belted", "a_line"],
    "selling_points": ["cuello camisero", "manga corta", "botones frontales", "encaje", "con cinturón", "corte A"],
    "construction": ["encaje", "manga corta", "botones frontales"],
    "defining_features": ["cuello camisero", "falda amplia", "cinturón"], "exclusions": ["pantalones"],
    "scene_codes": ["semiformal_event"], "use_scene": ["evento semiformal"], "query_language": "es-MX",
    "query_seeds": ["vestido camisero", "vestido camisero encaje botones", "vestido camisero romántico"]
  }
}
```

`queries` 必须与 `visual_profile.query_seeds` 完全相同且顺序一致：第 1 条为具体服装子类，避免 `vestido` 等泛词；第 2 条体现廓形、构造或卖点；第 3 条体现风格或使用场景。三条均不能包含颜色或尺码。`style_codes`/`style`、`selling_point_codes`/`selling_points`、`scene_codes`/`use_scene` 按平台语言严格对应，完整枚举和映射见 `visual_profile.py`。墨西哥站为 `es-MX`，SHEIN US 为 `en-US`。

新建 Mercado Libre 任务可在上述 plan 额外增加 `"strategy":"staged-v1"`；已绑定的历史计划保持原样。`binding_status.collection_stage` 返回当前阶段及 `review`、`search` 或 `finish`。需要扩展时，根据已观察到的商品生成 1–3 条不同且不含颜色、尺码的西语搜索词，以 `{"operation":"seal_expansion","data":{"queries":[...]}}` 提交；只有当前任务、租约与浏览器守护有效且前两个候选阶段已完成时才会接受。确认后按 `binding_status.queries` 的顺序在原标签采集，重新打开采集器并恢复检查点。达到目标、候选或时间预算后提交真实证据，不能编造销量或候选。搜索与详情请求在途时先完成当前步骤；已确认搜索、详情和扩展词可在授权的新租约中重新核验并恢复。

Plan 格式或验证失败会返回固定的 `invalid_plan` 指引，不包含服务内部错误或凭证。若原计划尚未绑定，可以根据该提示修正后重新提交；已绑定的计划不可更改。其他类型的回传失败仍仅返回 `accepted: false`，提交结果不确定时必须对账，不能重传。

固定 `invalid_plan` 仅针对提交前可检查的纯输入格式；租约、来源绑定、服务端校验及其他执行异常统一返回 `accepted: false`，不得据此判断是格式错误或重复提交安全。
