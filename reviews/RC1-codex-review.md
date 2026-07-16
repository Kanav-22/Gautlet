# Codex Independent Review — Claude/Fable RC1

- **Status:** CHANGES_REQUESTED
- **Reviewed RC1 head:** `84d756d5a77a6d83da61d3961ad6bd320429e23a`
- **Verified RC1 base:** `17cf790e6fd860de75b3532ec5be9a5e76d88e28`
- **Verified integration merge:** `c8d46e0545f09bbb482b9f5c8fb02fcf9cd9aeb4`
- **Review branch:** `codex/rc1-review`

## Verdict

RC1's functional engineering is strong. The four-cell Windows/Linux test matrix is real, every failure remains visible, both release-gate jobs execute the built wheel, the two RC.3 fixes match the failures in the first CI run, and the exact final head has six green GitHub Actions jobs. My fresh Windows source gate passed all 268 tests, and the ordinary wheel-only gate built both distributions, installed the wheel in a new environment, validated installed assets, and completed a 100/100 offline evaluation.

Approval is withheld for four concrete release-hardening corrections. None requires reworking GAUNTLET's accepted MVP behavior.

## Findings

### 1. [P2] Correct the network-isolation overclaim

`docs/release/RELEASE_READINESS_AND_SECURITY.md:58` says the minimal child environment means "accidental network use fails." The implementation at `src/gauntlet/adapters/python_callable.py:327` removes inherited proxy settings, API keys, and credentials, but installs no socket or OS network denial. An unauthenticated public request, a URL supplied in input, or any hard-coded endpoint can still connect.

Required correction: say that credential- or proxy-dependent calls lose inherited configuration, while **all socket access remains possible** unless an external sandbox/firewall blocks it. Keep the existing Docker/OS-level isolation deferral and hostile-code warning.

### 2. [P2] Make the wheel gate hermetic instead of inheriting Python import paths

`scripts/release_gate.py:113` invokes every child without an explicit environment, while lines 11–12 claim the source tree is deliberately absent from `sys.path`. `PYTHONPATH` and `PYTHONHOME` are inherited.

I ran the gate twice:

- Ordinary environment: exit `0`, `RELEASE GATE PASSED`, installed-wheel evaluation `ready, score 100.00/100`.
- Parent `PYTHONPATH=<repository>/src`: the wheel built and installed, but `gauntlet doctor` imported the source checkout and passed before the later containment check rejected `benchmarks/agent_mvp` outside `install-env`; exit `1`.

The existing `resolve().is_relative_to()` check prevents a false success, which is good, but the gate is host-environment-sensitive and does not establish its stated "source absent from sys.path" condition.

Required correction:

1. Remove inherited `PYTHONPATH` and `PYTHONHOME` from post-install runtime commands and set `PYTHONNOUSERSITE=1`.
2. Pass that controlled environment to the CLI smoke commands and packaged-path probe.
3. Add an installed-origin probe proving both `gauntlet.__file__` and `builtin_agent_mvp_path()` resolve under `install-env`.
4. Add a regression that invokes the gate with the parent `PYTHONPATH` deliberately pointed at `src`; it must still pass using only the wheel.

The static entry-point check, policy check plus `doctor`, exact count of 15 scenario YAMLs, installed-pack validation, and full acceptance suite are otherwise sufficient for this RC. An exact installed manifest-ID assertion would be useful additional hardening, but is not blocking.

### 3. [P2] Repair the RC1 handoff's contradictory commit history

`HANDOFF.md:577` correctly describes two RC.3 fixes, but `HANDOFF.md:580` and `HANDOFF.md:600` say no RC.3 commit exists. The RC1 history contains:

- `6f2d9e6` — ANSI help-test normalization.
- `cd63b86` — Windows short-path containment.

The commit list at `HANDOFF.md:564` also stops at the first three implementation commits and omits both fixes plus the later handoff-result commits. Replace the stale pre-CI text with the actual eight-commit RC1 history and distinguish "pre-CI adversarial audit found no runtime defect" from "CI found two release-infrastructure defects."

### 4. [P2] Declare least-privilege workflow permissions in the workflow

`.github/workflows/ci.yml` has no explicit `permissions` block, so its token capability depends on repository or organization defaults while checkout retains credentials and the workflow executes repository tests. The current `Kanav-22/Gautlet` repository setting is read-only, so this is not evidence that the reviewed CI run had write access; it is a portability and defense-in-depth gap in the release workflow itself.

Required correction: add top-level `permissions: contents: read`. Prefer `persist-credentials: false` on both checkout steps because no later step pushes. [GitHub documents](https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/enabling-features-for-your-repository/managing-github-actions-settings-for-a-repository) that workflow defaults can be configured as read or write, so the file should carry its own minimum privilege.

## Release-document accuracy cleanups

Address these in the same documentation fix:

- `docs/release/RELEASE_READINESS_AND_SECURITY.md:14` must qualify that artifacts are outside project source **by default**; the CLI permits an override and lines 89–92 document the inside-project failure mode.
- Line 124 says five milestone reviews; `reviews/M0.md` through `reviews/M5.md` total six.
- Lines 127–129 should distinguish the pre-CI audit's lack of runtime findings from the two RC1 CI hardening defects.
- Line 130's claim that remaining risks are "not unknowns" is an unsupported absolute; say that known remaining risks are documented.
- Lines 93–95 should say the ACL failures were not reproduced outside the Codex Windows sandbox, rather than claiming they cannot affect any end user.

## CI and fix verification

### Workflow

- Matrix is exactly `ubuntu-latest` / `windows-latest` × Python `3.11` / `3.12`.
- Release gate runs independently on Ubuntu and Windows with Python 3.12.
- No `continue-on-error`, `|| true`, ignored return code, or failure-only conditional exists. `fail-fast: false` only lets every matrix cell finish.
- All six named pytest node IDs exist and run in a dedicated visible step.
- Exact final-head run `29462717995` at `84d756d` concluded `success` with all six jobs green.
- First run `29461736356` independently reproduces the ANSI assertion failures in all four matrix cells and the Windows 8.3/long-path mismatch.

### RC.3 fixes

- `6f2d9e6`: sound test-only normalization. `GITHUB_ACTIONS=true pytest tests/test_cli_m5.py` passes after ANSI removal; runtime CLI behavior is unchanged.
- `cd63b86`: correct fail-closed containment. Resolving both paths before `is_relative_to()` handles the runner's `RUNNER~1` / `runneradmin` aliases and rejects sibling-prefix paths. The actual Windows release job passes after this commit.

### Determinism advisory

Evaluation behavior is seeded and the workflow has no hidden retry or failure masking. Infrastructure inputs are not dependency-identical across reruns: `actions/checkout@v4` and `actions/setup-python@v5` are mutable major tags, while pip resolves broad dependency ranges without a constraints file. This is acceptable as a current-compatibility lane, but not a reproducible release environment. For stronger supply-chain/replay guarantees, pin actions to full commit SHAs and add a reviewed constraints-based release lane; [GitHub's secure-use guidance](https://docs.github.com/en/actions/reference/security/secure-use) identifies full-length SHAs as the immutable action reference.

## Independent Windows gate

Fresh environment: `.venv-rc1-review`, CPython 3.12.10, ordinary unsanitized process environment.

| Check | Exit | Actual result |
|---|---:|---|
| `pip install -e ".[dev]"` | 0 | editable `gauntlet-0.1.0` installed |
| `gauntlet --version` | 0 | `gauntlet 0.1.0` |
| Full pytest | 0 | **268 passed in 161.74s** |
| `GITHUB_ACTIONS=true pytest tests/test_cli_m5.py` | 0 | **6 passed in 2.41s** |
| Six named security/adapter tests | 0 | **6 passed in 1.97s** |
| `ruff check .` | 0 | `All checks passed!` |
| `ruff format --check .` | 0 | `77 files already formatted` |
| `mypy src tests` | 0 | no issues in 65 source files |
| Flagship benchmark validation | 0 | schema 1, version 0.1.0, 15 scenarios |
| `python scripts/release_gate.py` | 0 | `RELEASE GATE PASSED`; installed-wheel evaluation ready, 100.00/100 |
| Release gate with parent `PYTHONPATH=<repo>/src` | 1 | intentional contamination probe exposed source import; containment failed closed |

## Boundary verification

- Merge base is exactly `17cf790`; all eight RC1 commits descend from it with no Codex history rewrite.
- RC1 changes only `.github/workflows/ci.yml`, `scripts/release_gate.py`, `docs/release/RELEASE_READINESS_AND_SECURITY.md`, `HANDOFF.md`, and the ANSI-resilient CLI test.
- No specification file `00`–`16` and no `reviews/` file changed in `17cf790..84d756d`. `reviews/M5.md` was legitimately updated before the RC1 base.
- No adapter, executor, evidence, assertion, scoring, reporting, or other runtime/fail-closed behavior was weakened.
- No public plugin discovery, Docker isolation, LLM judge, LangGraph integration, or HTML reporting was implemented.

## Re-review gate

Fable should address only the findings above, update the RC1 handoff honestly, run the full local gates plus the contaminated-`PYTHONPATH` release regression, and push a new exact head with green Windows/Linux CI. Codex should then re-review that head. No change to `main` is authorized by this review.
