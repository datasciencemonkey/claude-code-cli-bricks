# Repository Readiness Assessment

**Repo:** `coding-agents-databricks-apps` (CoDA - Coding Agents on Databricks Apps)
**Version:** 0.15.0
**Assessed:** 2026-03-08

---

## Overall Score: **7.0 / 10** — Solid Foundation, Not Yet Customer-Ready

The repo is a well-engineered template for running coding agents (Claude, Codex, Gemini, OpenCode) on Databricks Apps. It has strong architecture, decent test coverage, and good security practices. However, it's missing several elements customers expect from a production template.

---

## Scorecard

| Category | Score | Weight | Notes |
|----------|-------|--------|-------|
| **Code Quality** | 8/10 | 20% | Clean separation of concerns, proper error handling, well-organized |
| **Security** | 8/10 | 15% | Single-user token model, file perms (0o600), path traversal protection, security headers |
| **Documentation** | 7/10 | 15% | README is excellent, deployment guide is clear, but gaps exist |
| **Testing** | 6/10 | 15% | 51 tests across 4 modules, but no CI automation and limited coverage areas |
| **CI/CD** | 3/10 | 10% | Only a manual release workflow — no test/lint/scan automation |
| **Dependency Mgmt** | 4/10 | 10% | All deps use `>=` floor pins — no lockfile, no upper bounds |
| **Licensing & Legal** | 1/10 | 5% | No LICENSE file — legally ambiguous for customers |
| **Extensibility** | 7/10 | 5% | Template pattern works, app.yaml.template is clear |
| **Observability** | 8/10 | 5% | MLflow tracing, health endpoint, setup progress tracking |

---

## What's Working Well

1. **Architecture** — Clean Flask + PTY + Gunicorn design with single-worker intentionality documented
2. **Multi-agent support** — 4 agents configured at boot with shared Databricks skills
3. **Security model** — Token-owner verification, proper credential file permissions, security headers
4. **UX** — Loading screen with snake game, split panes, 8 themes, voice input, image paste
5. **MLflow tracing** — Automatic session-level tracing with zero config
6. **Workspace sync** — Non-blocking post-commit hook syncs to Databricks Workspace
7. **README** — Professional, comprehensive, well-structured with architecture diagrams
8. **Test quality** — Tests that exist are well-written with proper mocking and edge cases

---

## What's Missing for Customer Readiness

### P0 — Must Fix Before Shipping

| # | Gap | Impact | Effort |
|---|-----|--------|--------|
| 1 | **No LICENSE file** | Customers can't legally use/modify the code. Template repos need a permissive license (Apache 2.0 or MIT). | 5 min |
| 2 | **No dependency lockfile** | `flask>=2.0` spans 3+ major versions. Builds are non-reproducible. A single upstream break could break all deployments. | 30 min |
| 3 | **No CI test automation** | 51 tests exist but never run in CI. Regressions can ship undetected. | 1 hr |
| 4 | **No CONTRIBUTING.md** | For a template repo, customers need guidance on forking, customizing, and contributing back. | 30 min |
| 5 | **`index.html` is 1506 lines** | Single monolithic file mixing HTML, CSS, and JS. Hard for customers to customize. Should split into `styles.css`, `terminal.js`, `app.js`. | 2-3 hrs |

### P1 — Should Fix Before GA

| # | Gap | Impact | Effort |
|---|-----|--------|--------|
| 6 | **No CHANGELOG.md** | Release workflow generates notes, but there's no persistent changelog for customers to read. | 30 min |
| 7 | **No rate limiting on API endpoints** | `/api/session` can be called repeatedly to spawn unlimited PTY processes. | 1 hr |
| 8 | **No input validation on API payloads** | `session_id`, `cols`, `rows` etc. accepted without type/bounds checking. | 1 hr |
| 9 | **No CSRF protection** | POST endpoints accept any origin. Flask-WTF or same-origin check needed. | 1 hr |
| 10 | **`PLAN-issue-8.md` committed** | Internal planning docs shouldn't ship in the template. | 5 min |
| 11 | **`app.yaml` committed (not just template)** | Contains real deployment config. Only `app.yaml.template` should be tracked; `app.yaml` should be gitignored. | 10 min |
| 12 | **No `uv.lock` or `requirements.lock`** | Even with pyproject.toml, reproducible installs need a lockfile. | 15 min |
| 13 | **Setup scripts run sequentially** | 8 setup steps run one-by-one. Could parallelize independent steps (micro, claude, codex, gemini, opencode) to cut startup time 2-3x. | 2 hrs |
| 14 | **No test for the main Flask routes** | Heartbeat, upload, and reinit are tested, but session create/input/output/resize have no tests. | 3 hrs |

### P2 — Nice to Have

| # | Gap | Impact | Effort |
|---|-----|--------|--------|
| 15 | **No Dockerfile** | Vendor lock-in to Databricks Apps. Customers may want to run locally in Docker for dev/testing. | 1 hr |
| 16 | **No linting/formatting config** | No ruff, black, isort, or pre-commit config. Code style is consistent but unenforced. | 30 min |
| 17 | **No security scanning** | No Dependabot, CodeQL, or Snyk in CI. | 30 min |
| 18 | **Demo GIF missing** | README has `<!-- TODO: Add demo GIF -->`. A 30-second recording would dramatically improve first impressions. | 30 min |
| 19 | **No per-session audit log** | MLflow traces sessions but there's no lightweight audit trail of who connected when. | 1 hr |
| 20 | **Gemini CLI pins to `@nightly`** | Nightly builds can break without warning. Should pin to a stable version. | 10 min |

---

## Recommendations — What I'd Add

### Immediate (before any customer sees this)

```
1. Add LICENSE (Apache 2.0)
2. Add uv.lock / pin dependencies
3. Add GitHub Actions CI workflow (pytest + ruff lint)
4. Remove PLAN-issue-8.md from template
5. Gitignore app.yaml (keep only template)
```

### Short Term (before GA)

```
6. Split index.html into separate CSS/JS files
7. Add CONTRIBUTING.md with fork/customize/deploy guide
8. Add CHANGELOG.md
9. Add session creation rate limiting (max 10 concurrent)
10. Add input validation middleware for API payloads
11. Add CSRF same-origin check
12. Parallelize setup steps for faster cold boot
13. Add integration tests for session lifecycle endpoints
```

### Medium Term (post-GA polish)

```
14. Add Dockerfile for local dev
15. Add pre-commit hooks (ruff, formatting)
16. Add Dependabot / CodeQL scanning
17. Record and embed demo GIF
18. Add configurable session limits (via env var)
19. Pin Gemini CLI to stable release
20. Add structured logging (JSON format) for log aggregation
```

---

## Summary

The repo is a **strong prototype** — the architecture is sound, the multi-agent design is well thought out, security is better than most templates, and the UX features (snake game, themes, voice input) show real polish. But it's missing the **packaging and governance** that enterprise customers expect: licensing, reproducible builds, automated testing, and contribution guidelines. Fixing the P0 items would take roughly half a day and would move this from a 7.0 to an 8.5.
