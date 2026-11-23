# Interactive Research Queue Implementation Plan

> For agentic workers: use test-driven development and independent review. Do not claim unattended capability without actual Chrome, vision and result-return evidence.

Goal: claim one eligible research task at a time only while a verified interactive executor is available; persist checkpoints and prevent replay of ambiguous submissions. Resume after restart only when the original request and lease are reconciled.

1. Add capability gate for Chrome, vision and result return; test missing, expired and failed probes.
2. Add research-only automatic claim and bounded worker supervision; test sequential claim, lease recovery, loss of capability and exclusion of unrelated task types.
3. Add request-bound dispatcher with durable invocation intent; test restart, blockers, no duplicate external action and no credential propagation.
4. Integrate a real Codex Chrome executor and scoped result bridge; validate evidence provenance, request identity and timestamps. CAPTCHA requires the computer-use tool's per-challenge confirmation; otherwise preserve the checkpoint and wait.
5. Run worker suite, isolated integration, and scoped live acceptance before enabling login-session supervision. Never report computer-restart recovery as verified without exercising it.
