# APMX

**APMX runs Agentic Software Factories from Markdown contracts. Your project is
the workspace. Declare each task's required inputs and handoffs, the artifacts
it must deliver, and the checks that must pass before another task can use them.**

**Start locally with your own harness. Share and version contracts through APM.
Share not just the skills for doing the work, but the goals and checks for
accepting it.**

**Install** the experimental
[v0.7.1 release](https://github.com/danielmeppiel/apmx/releases/tag/v0.7.1)
with the [native installation guide](docs/install.md#install-a-prebuilt-archive).
It runs contracts through native Copilot or the native
[OpenCode profile](docs/opencode.md); authenticate in your chosen harness, whose
installation and model access remain external prerequisites.

## Three commands

```sh
apmx ./feature-factory --plan     # see the contracts, handoffs and checks; no agents, no installs
apmx ./feature-factory            # run: one attempt per contract unless its budget allows retries
apmx audit <receipt>              # verify what was produced, from what, and how
```

The same commands work for a single contract (`apmx build.contract.md`) or a
Git package (`apmx --from owner/repo`). `--on copilot|opencode` selects the
harness; Copilot is the default. A fourth command, `apmx check <receipt>`,
reruns a factory's own checks on the outputs another run recorded, with no
agent; [`run.yml`](#accept-factory-prs-on-trusted-evidence) uses it to write the receipt
of record. The [docs](https://danielmeppiel.github.io/apmx/) cover every
command in depth.

## 1. A contract binds a task to its checks

A **contract** is a Markdown task that declares what it needs, which files it
must deliver and how to check them. Here is the example's
[planning contract](examples/contracts/software-factory/contracts/planning.contract.md),
with its instructions shortened:

```markdown
---
needs: request.md
produces: plan.md
verify:
  plan-sections: python3 -I -B checks/documents.py planning plan.md
---
Plan the checkout change in request.md. Write plan.md with
Goal, Changes, Validation and Risks sections.
```

Each contract runs as an **agent loop**: the harness does the work, then the
checks independently judge the delivered files. A contract can author a
[repair budget](docs/repair.md), for example at most three attempts sharing
600 seconds; only eligible assessed rejections are retried. Without a budget,
a contract gets one attempt.

Checks are your own commands. **Gherkin is optional**; the example's
patch-aware checks use it to test the changed application independently:

```gherkin
Scenario: Free delivery at 5000 cents
  Given a subtotal of 5000
  When I request pricing and checkout
  Then delivery is 0 cents and the total is 5000 cents
```

A section check can assess document structure; it does not prove the
reasoning is correct.

## 2. A factory is contracts connected by files

A **factory** is a directory of contracts and checks. Contracts hand off
through **files on disk**: when one declares `needs: plan.md`, it waits for the
contract that `produces: plan.md`, and receives that exact checked file. No
pipeline file is required. The
[checkout example](examples/contracts/software-factory/README.md) adds free
delivery from a 5000-cent subtotal:

```text
request.md -> plan.md -> specification.md
                        -> changes.diff + implementation.md
                        -> documentation.diff + documentation.md -> review.md
```

After [installation](docs/install.md) and
[example setup](examples/contracts/software-factory/README.md#set-up), preview
with `--plan`, then run it. A run prints one block per contract and ends with
what was delivered and how to verify it (abridged; timings and the remaining
stages omitted). The build lines illustrate a repair, which appears only when a
check rejects an attempt:

```text
$ apmx ./feature-factory
Factory  feature-factory   5 contracts   copilot / default model

[1/5] planning   needs request.md -> produces plan.md
      attempt 1/1  checks: [+] plan-sections
      [+] plan.md -> handed to specification
...
[3/5] build      needs request.md, specification.md -> produces changes.diff, implementation.md   budget 3 attempts
      attempt 1/3  checks: [+] shipping-examples [x] checkout-regression [+] implementation-report-format
      attempt 2/3  checks: [+] shipping-examples [+] checkout-regression [+] implementation-report-format
      [+] changes.diff, implementation.md -> handed to review, documentation
...
[+] COMPLETE   5/5 contracts   9/9 checks

Outputs   feature-factory/.apm/chains/<id>/artifacts/
          plan.md  specification.md  changes.diff  implementation.md  documentation.diff  documentation.md  review.md
Receipt   feature-factory/.apm/chains/<id>/receipt.intoto.jsonl
          provenance  in-toto + SLSA v1
          checks      in-toto test-result (9)
          inventory   CycloneDX 1.5 (<n> components)
          outputs     4 delivered files in outputs/
          unsigned: binds content, not identity; review before sharing

Next      apmx audit feature-factory/.apm/chains/<id>/receipt.intoto.jsonl
```

Any contract can deliver several files. The build stage declares:

```yaml
produces:
  - changes.diff
  - implementation.md
```

Agents work on private copies; your checkout is unchanged. The patch is
produced by a bounded Git exporter, not hand-written hunks. Every declared output must exist and every required check
must pass before a downstream contract receives anything. A rejected run says
which check failed, keeps its attempts under `Saved`, and prints the rerun
command. `--verbose` shows the full agent and check logs.

**Observed with real Copilot and OpenCode in development:** the same factory
definition completed all five stages and nine checks, retaining seven outputs
through each harness. Both produced actual code and documentation patches and
loaded the selected testing skill. These are source-route observations, not a
claim about the older downloads.

## 3. A receipt is evidence of what was produced

Every **COMPLETE** run writes a [receipt](docs/evidence.md) next to its record:
one standard in-toto bundle, `receipt.intoto.jsonl`, with one signed-ready
statement per line:

- a SLSA Provenance v1 for the delivery: factory definition digest, package and
  commit, per-stage harness and models, base tree, before-images and deletions;
- an in-toto Test Result v0.1 per check of the final candidate;
- a CycloneDX 1.5 inventory of the APM dependencies, inline.

Every statement names the same subjects: the delivered files at their project
paths, which `outputs/` holds. For a factory that produces patches, those are
the files the patches produce (`src/pricing.py`), not the patch. Inputs, stage
outputs, transcripts, rejected attempts and raw check logs are kept by digest in
`evidence/sha256/`, optional for verification. The checked-in receipt of the
five-stage example is one 37 KB bundle of 11 lines
([`receipt-v2`](tests/unit/contracts/golden/receipt-v2/receipt.intoto.jsonl));
its evidence is not checked in.

Anyone holding the bundle can verify it without rerunning the factory. Here
v0.7.1 audits that receipt; add `--outputs <dir>` to re-hash the delivered
files as well:

```text
$ apmx audit receipt-v2/receipt.intoto.jsonl
Receipt   receipt-v2/receipt.intoto.jsonl   5 contracts   unsigned

[i] Policy        none found
                  no apm-policy.yml in project and no git origin for org policy
[+] Integrity     11 statements; line digests computed; evidence not present (optional)
[+] Standards     in-toto Statement v1, SLSA Provenance v1, in-toto Test Result, CycloneDX 1.5: schema-valid
[+] Factory       definition sha256:0bec0549b9af..  danielmeppiel/apmx-demo-factory#v1.0.0  5 contracts, 9 checks
[+] Checks        9/9 passed (in-toto test-result)
[+] Ingredients   2 APM dependencies   apm audit --ci: clean (content, lock, drift)
                  installed bytes match CycloneDX inventory and recorded hashes
[i] Revocation    no policy: nothing revoked to check
[+] Outputs       4 files bound: docs/checkout.md src/checkout.py src/pricing.py tests/test_free_shipping.py
[!] Identity      unsigned: proves content binding, not who ran it

[+] VALID   (content-bound; not authenticated)
```

Since the next release, Standards is part of the Integrity row and Ingredients
is named Dependencies.

`apmx audit --explain` prints what the receipt records (factory, stages,
checks, ingredients, outputs; the `ingredients` topic is `dependencies` since
the next release); `apmx audit --export abom` writes the CycloneDX
inventory as a standalone file for SBOM tools.

For dependencies, `apmx audit` reinstalls the recorded lock with the bundled
APM and runs `apm audit --ci`. It passes your discovered `apm-policy.yml` with
`--policy` only when that policy has APM rules; with no policy, or one that
has only a `factories:` section, it runs `apm audit --ci --no-policy`.
`--offline` skips this step.
`--format json|sarif` suits CI gates. Local receipts are **unsigned**: they
prove content binding, not who ran the factory. `evidence/` includes captured
inputs and transcripts, so review it before sharing. See [audit](docs/audit.md)
and the [`apmx audit` reference](https://danielmeppiel.github.io/apmx/docs/reference/audit/).

**Local execution is not a sandbox.** Agents and checks can use host files,
network and logins; model usage may cost money. An interactive factory asks
before it executes, defaulting to No. Exit codes: **0** COMPLETE, **20**
REJECTED, **21** UNPROVEN (never success), **22** HALTED, **23** receipt export
failed after a COMPLETE run. See [results](docs/results.md).

## Accept factory PRs on trusted evidence

Run the factory in CI with [`run.yml`](.github/workflows/run.yml)
(`uses: danielmeppiel/apmx/.github/workflows/run.yml@v0.7.1`). The agent,
the checks of record (`apmx check`) and the signature each run in a separate
job. The signature comes from a **trusted signer**: today the `attest` job of
`run.yml`, a GitHub Actions workflow signing through Sigstore. The receiving
repository pins what it accepts in its `apm-policy.yml`:

```yaml
factories:
  allow: ["acme/checkout-factory@sha256:<definition digest>"]
  require_signed: true
  signers:
    - workflow: danielmeppiel/apmx/.github/workflows/run.yml
      workflow_sha: a70c1939dddc6f5d7888b76fd5bb8bab3e69e886   # git rev-parse v0.7.1^{commit}
      repo: acme/factories
```

`run.yml` signs a SLSA Verification Summary over the delivered files and
attests the receipt bundle beside it, so the factory's PR carries **code only**.
The receiving repository gates every pull request with one step on
`pull_request_target` (the base branch's copy runs, so a PR cannot rewrite its
gate). Green means accepted on trusted evidence, and nothing else: it is not
a guarantee that the code is correct. Human PRs fail it as `Not a factory
delivery` and are merged through the repository's normal path. `apmx audit`
looks the PR's changed files up by SHA-256 in the signer's attestation store,
requires each to be a subject of one PASSED summary from an allowed signer,
checks the statements it binds against the policy read from the PR base, and
refuses a PR built on a stale base:

```yaml
- uses: danielmeppiel/apmx/.github/actions/setup@v0.7.1   # installs apmx 0.7.1
- run: apmx audit
  env: { GH_TOKEN: "${{ github.token }}" }
```

See the [example](examples/accept-factory-prs/README.md).

## Go further

APMX runs and checks; the selected harness does the agent work. Bundled APM prepares shared
packages and skills when needed, with no separate APM installation.

- [Factory setup, artifacts and manual recovery](examples/contracts/software-factory/README.md)
- [Write your first contract](examples/contracts/first-contract/README.md)
- [Reuse a packaged task and skill](examples/contracts/packaged-job/README.md)
- [Use the OpenCode profile](docs/opencode.md)
- [Report a problem or contribute](https://github.com/danielmeppiel/apmx/issues) - include a small example and remove secrets from logs.

APMX is an independent project by Daniel Meppiel, licensed under Apache-2.0
for APMX-specific additions.
Retained Microsoft APM material remains MIT-licensed; its terms and attribution
are preserved in NOTICE. This is not an official Microsoft or GitHub release.
[Source origin](docs/source-origin.md) | [License](LICENSE) | [Notices](NOTICE)
