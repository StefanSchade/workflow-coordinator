# Phase-specific execution bindings

Implement FTR-WFC-PHASE-EXECUTION-BINDINGS for the existing consumer configuration and all executing workflow phases. Role bindings remain defaults; an optional phase-keyed mapping in Consumer Config supplies partial adapter/model/reasoning overrides, merged field by field after role defaults and existing role CLI overrides. Select phase keys against the selected program's actual executing phases, never by hard-coded review names. Reject unknown or nonexecuting phases, unknown fields, empty/invalid values, missing effective fields and mixed effective adapters deterministically before provider execution. Maintain the single scalar executable and existing authorization and reviewer independence. Avoid provider routers and future-provider abstractions.

Resolve effective bindings before launch, expose them in plan and immutable execution envelope, identity and evidence, use them by actual phase for every Hop, and preserve pinned bindings on resume and reconciliation. Preserve old configurations and their behavior; preserve existing envelope compatibility and identity for configurations without phase overrides where feasible without compromising correctness. Document exact syntax, precedence, validation and identity in Operator Manual, packaged skill and relevant consumer examples. Include robust@v1 example with distinct broad and focused reviewer models and the same role. Add meaningful positive and negative tests including generic phases, partial merge, same-role different models, mixed adapters, invalid phase and field, admission, actual dispatch, digest/evidence, and configuration drift/resume. Preserve trusted Git and sandbox boundaries. Run repository quality gates. Create a concise backlog FTR describing decision, acceptance, compatibility, limits and a documentary assessment that future mixed adapters would need explicit future design, without implementing one. Record measured checks; independent review and integration remain operator decisions.

## Acceptance

All functional and governance requirements in the task are satisfied, existing configurations work unchanged, effective choices are pinned and evidenced, tests and mandatory quality gates pass, and independent review assesses the complete candidate.

## Withheld authority

No mixed-adapter runs, provider router, registry, unrelated refactoring, changes to trusted Git or sandbox boundaries, runtime promotion, installation, push, tag, publication, external repository changes, historical worktree cleanup, weakening or skipping tests. Do not edit existing WFC state or evidence. No self-acceptance or Git writes by agents.

## Default escalation

Escalate material architecture or scope conflicts, inability to maintain authorization, independence or run pinning, and missing required evidence. Do not silently substitute models.
