# APMX

**APMX runs Agentic Software Factories from Markdown contracts. Your project is
the workspace. Declare each task's required inputs and handoffs, the artifacts
it must deliver, and the checks that must pass before another task can use them.**

**Start locally with your own harness. Share and version contracts through APM.
Share not just the skills for doing the work, but the goals and checks for
accepting it.**

**Released and development behavior are different.** The experimental
[v0.4.2 downloads](https://github.com/danielmeppiel/apmx/releases/tag/v0.4.2)
contain the earlier four-stage Copilot implementation. Follow the
[native installation guide](docs/install.md#install-a-prebuilt-archive) for
those immutable assets.

This development checkout adds implicit project capture, a five-stage factory,
bounded repair, portable evidence and a native
[OpenCode profile](docs/opencode.md), alongside Copilot. Follow the
[development example setup](examples/contracts/software-factory/README.md#set-up)
with this checkout selected, not the older pinned source. Authenticate in your
chosen harness; its installation and model access remain external prerequisites.

## Three commands

```sh
apmx ./feature-factory --plan     # see the contracts, handoffs and checks; no model calls
apmx ./feature-factory            # run: each contract loops until its checks pass
apmx audit <receipt>              # verify what was produced, from what, and how
```

The same commands work for a single contract (`apmx build.contract.md`) or a
Git package (`apmx --from owner/repo`). `--on copilot|opencode` selects the
harness; Copilot is the default.

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
stages omitted):

```text
$ apmx ./feature-factory
Factory  feature-factory   5 contracts   copilot

[1/5] planning   needs request.md -> produces plan.md
      attempt 1/1  checks: [+] plan-sections
      [+] plan.md -> handed to specification
...
[3/5] build      needs ... -> produces changes.diff, implementation.md
      attempt 1/3  checks: [x] checkout-regression  [+] shipping-examples ...
      attempt 2/3  checks: [+] shipping-examples  [+] checkout-regression  [+] implementation-report-format
      [+] changes.diff, implementation.md -> handed to documentation
...
[+] COMPLETE   5/5 contracts   9/9 checks

Outputs   feature-factory/.apm/chains/<id>/artifacts/
Receipt   feature-factory/.apm/chains/<id>/receipt/
          provenance  in-toto + SLSA v1
          checks      in-toto test-result (9)
          inventory   CycloneDX 1.5
Next      apmx audit feature-factory/.apm/chains/<id>/receipt
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

## 3. A receipt proves what was produced

Every **COMPLETE** run delivers a [receipt](docs/evidence.md) in industry
formats. It binds the factory definition, its contracts and checks, the APM
dependencies used and the delivered files by SHA-256:

- `provenance.intoto.json`: in-toto Statement with SLSA Provenance v1
- `checks/*.intoto.json`: in-toto Test Result per check
- `abom.cdx.json`: CycloneDX 1.5 inventory of APM dependencies
- `definition.json`, `index.json` and `summary.md`, plus the exact `apm.yml`
  and lock when APM dependencies were used

Anyone holding the outputs and the receipt can verify them without rerunning
the factory:

```text
$ apmx audit feature-factory/.apm/chains/<id>/receipt --outputs ./delivered
[+] Integrity     all files match index.json (SHA-256)
[+] Standards     in-toto Statement v1, SLSA Provenance v1, CycloneDX 1.5: schema-valid
[+] Factory       definition sha256:...  5 contracts, 9 checks
[+] Checks        9/9 passed (in-toto test-result)
[+] Ingredients   1 APM dependency   apm audit --ci: clean (content, lock, drift)
[+] Outputs       files bound and re-hashed
[!] Identity      unsigned: proves content binding, not who ran it

[+] VALID   (content-bound; not authenticated)
```

For dependencies, `apmx audit` reinstalls the recorded lock with the bundled
APM and runs `apm audit --ci`. Add `--policy <source>` to enforce an
organization's APM policy. `--format json` suits CI gates. Receipts are
**unsigned**: they prove content binding, not who ran the factory, and they
include captured source files, so review them before sharing. See
[audit](docs/audit.md).

**Local execution is not a sandbox.** Agents and checks can use host files,
network and logins; model usage may cost money. An interactive factory asks
before it executes, defaulting to No. Exit codes: **0** COMPLETE, **20**
REJECTED, **21** UNPROVEN (never success), **22** HALTED, **23** receipt export
failed after a COMPLETE run. See [results](docs/results.md).

## Go further

APMX runs and checks; the selected harness does the agent work. Bundled APM prepares shared
packages and skills when needed, with no separate APM installation.

- [Factory setup, artifacts and manual recovery](examples/contracts/software-factory/README.md)
- [Write your first contract](examples/contracts/first-contract/README.md)
- [Reuse a packaged task and skill](examples/contracts/packaged-job/README.md)
- [Use the development OpenCode profile](docs/opencode.md)
- [Report a problem or contribute](https://github.com/danielmeppiel/apmx/issues) - include a small example and remove secrets from logs.

APMX is an independent project by Daniel Meppiel, licensed under Apache-2.0
for APMX-specific additions.
Retained Microsoft APM material remains MIT-licensed; its terms and attribution
are preserved in NOTICE. This is not an official Microsoft or GitHub release.
[Source origin](docs/source-origin.md) | [License](LICENSE) | [Notices](NOTICE)
