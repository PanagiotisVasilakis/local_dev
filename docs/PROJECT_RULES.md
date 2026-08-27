# Project execution rules

These rules are standing constraints for work on this repository.

1. Repository scope is strict: work only in `PanagiotisVasilakis/local_dev`. Do not read, modify, create branches/issues/PRs in, or otherwise operate on any other repository.
2. Use task-scoped branches. Keep the default branch unchanged until a task has an implementation checkpoint and has passed its review gate.
3. A task is not `DONE` when implementation finishes. Every task must receive a separate deep review and adversarial verification pass. Review findings must be fixed and re-verified before proposing the next task.
4. Never claim a test, lint, type-check, build, migration, or other verification passed unless it was actually executed against the relevant implementation. Report unavailable gates explicitly.
5. Deterministic guarantees belong in code/tooling, not prompts. In particular, paid-call authorization and budget enforcement must be application-enforced.
6. Evidence from repository state and executed tools is authoritative. Model-generated statements are not evidence by themselves.
7. Fail closed when safety, accounting, provenance, or verification state is ambiguous.
8. Prefer a modular monolith and minimal dependencies. Add complexity only when it produces a concrete quality, reliability, or maintainability gain.
9. Preserve exact monetary accounting for paid APIs. Never use binary floating-point values for budget enforcement.
10. The configured personal paid-API ceiling is €20 per UTC calendar month unless the user explicitly changes it. A provider call must never be authorized when committed spend plus active reservations plus the bounded worst-case cost of the new call would exceed the durable monthly policy.
