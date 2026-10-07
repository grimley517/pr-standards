# pr-standards

Central PR review standards for every repo owned by grimley517 and grimpop.

## What runs on every PR to the default branch

| Lens | Tool | Gates merge? | Applies to |
|---|---|---|---|
| OWASP Top 10 (A01–A10) + secrets | Semgrep `p/owasp-top-ten`, `p/secrets` (new findings only) | yes | code, deployable |
| Hexagonal architecture | `gate.py hexagonal`: domain/ and application/ layers must exist; changed domain/application files may not import adapters/infrastructure or IO frameworks | yes | code, deployable |
| Business-logic coverage | `gate.py coverage`: runs `make coverage` / `npm run coverage`, reads Cobertura XML, ≥80% line coverage over domain+application **only** | yes | code, deployable |
| Onboarding | `gate.py onboarding`: README ≥50 words, `make` or `npm run build`, `make run` or `npm run serve` | yes | all |
| Logging & alerting | `gate.py observability`: alert/monitoring config + logging library present | yes | deployable |
| Spelling & grammar (en-GB) | `gate.py content` via self-hosted LanguageTool on changed .md/.html prose | yes | pages |
| Judgement review | GitHub Copilot code review (auto on push) using `.github/copilot-instructions.md`: one combined OWASP pass, business-logic placement, tests, onboarding, logging/alerting | comments only | all |

Required status check: **`pr-standards / gate`**, enforced by a `pr-standards` ruleset on each repo's default branch.

## Files placed in each repo
- `.github/workflows/pr-standards.yml`: caller of `gate.yml`, with the detected `type:` pinned (edit to override: `code | deployable | pages`).
- `.github/copilot-instructions.md`: review lenses. Repo-specific guidance goes under `## Repo notes`; rollout preserves it.

## Optional per-repo tuning (add by hand)
- `.github/pr-standards.json`: `{"domain": ["src/Core"], "application": ["src/UseCases"], "coverage_min": 80, "coverage_file": "coverage.xml", "language": "en-GB"}`
- `.github/wordlist.txt`: words the spelling check should accept.

## Rollout
```bash
python3 scripts/rollout.py --dry-run   # plan
python3 scripts/rollout.py             # apply to all (idempotent)
python3 scripts/rollout.py repomgr     # one repo
```
Forks and archived repos are skipped.
