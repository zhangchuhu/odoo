# Odoo native Codex host-session integration

User constraints: native Codex vision and image_gen only. No Ark, Seedream,
Doubao, vendor API keys, or Feishu. Odoo's authenticated queue and artifacts
remain the system of record. No live database changes during implementation.

1. Add a private durable mailbox bound to Odoo API identity; keep original Worker
   request IDs and use serial pending host work, not subprocess tool simulation.
2. Adapt Worker to poll pending host requests under the original lease. Keep the
   loop running for heartbeats while a multi-minute native tool runs externally.
3. Materialize digest-verified original image artifacts. Host opens actual files,
   selects references, records visible garment facts; code constructs immutable
   target-first image invocation. Reject unsupported infographic inventory.
4. Atomically dispatch once only after actual current tool capability and viewed
   digest evidence; replay/unknown requests cannot invoke again. Host CLI fences
   effectful actions through the original Worker lease before dispatch/upload.
5. Import actual local native output or browser-exported gallery rasters using
   Client.create_artifact. Persist upload intent and receipt. Lost receipt waits
   for known artifact reconciliation rather than repeat upload or generation.
6. Reopen original target/references/candidate for explicit visible QC. Candidate
   digest is immutable; unresolved or failed QC pauses rather than regenerates.
7. Test local storage, concurrent dispatch, request replay, changed bytes, missing
   capabilities, unknown upload, gallery evidence, and real fixture HTTP handoff.
   No paid network tests. Document session availability/authentication boundaries.

This is an Odoo adapter to the skill's host-session principles, not a claim to
implement its entire Feishu scheduler, three-candidate ranking, or infographic
inventory protocol. Host metadata is an honest attestation, not authentication
of a tool runtime. Real native tool execution requires the interactive host.
