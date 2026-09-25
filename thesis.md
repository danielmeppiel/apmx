# Version Your Factory, Not Just Your Code

## Situation: Automation moves the bottleneck

As agents generate code faster, verification and human review must keep up. The authoring system becomes infrastructure to operate.

> Version your factory so you can govern what runs, verify what it produces, and preserve the evidence.

## Pain: Who governs the system behind the diff?

Third-party skills, plugins and MCP servers create a shared attack surface: poison the producer to influence many patches. Separately, models can hallucinate or pursue misleading green results by weakening checks, without malicious intent. Policy-authorized auto-merge raises the stakes.

| Developer, incident and audit questions | Enterprise need |
| --- | --- |
| Which factory, component and policy versions produced this output? | Auditable production history. |
| Which checks evaluated it? Was acceptance weakened? | Protected rules and permissions. |
| Which runs link to a suspect update? | Scope investigation and containment. |
| What changes with a replacement? What needs rechecking? | Approved updates with historical evidence preserved. |

Security, controlled change and compliance support require governing the producer, not merely reviewing its output.

## Solution: Version the factory as maintained software

Composable capabilities need versions, dependencies, tests, owners and release notes. Agent Plugins 1.0 provides shared packaging for skills and MCP configuration in compatible clients; installation, permissions and client-specific behavior remain client-controlled.

| Building block | Responsibility |
| --- | --- |
| **APM** | Apply package management to agent capabilities: package, lock and distribute reusable instructions and resources. |
| **Agent Contracts** | Version goals, inputs, outputs, selected capabilities, checks and attempt/time bounds. |
| **First-class Checks** | Version and retain executable criteria; evaluate the exact captured candidate. |
| **APMX** | Prepare capabilities, run through harness adapters, and check or repair within unchanged bounds and criteria. |
| **Evidence Package** | Keep the exact agreement, supplied components, execution, artifacts and check results together, including failures. Retain bytes or retrievable references, not just hashes. |

Contracts package with capabilities through APM. Runtime and platform controls enforce permissions; independently controlled gates enforce acceptance. Governance rules are versioned too, with separate authority for changes. High-risk or uncertain work goes to people.

> Improve the patch. Do not lower the bar.

## Why it works: Established discipline, applied to the producer

Package managers and lockfiles make dependencies explicit. Versioned CI requirements separate code from acceptance. Production provenance provides interoperable history. This applies established discipline to the producer, without introducing another model, harness, control plane or supply-chain standard.

An agent bill of materials (**ABOM**) inventories recorded authoring capabilities, separately from application dependencies. **SLSA provenance** describes production. **in-toto Statements** bind those claims and separate check results to exact artifact hashes. APMX captures the facts; the exports do not decide acceptance.

### What the prototype must prove

1. Show locked capabilities, their ABOM, and exact contract and checker identities.
2. Run one artifact-producing agreement on Copilot and OpenCode, preserving portable definition identity, not identical output. Produce one Evidence Package per run: in-toto-conformant Statements carrying SLSA production provenance and separate completed check results, bound to actual artifact digests.
3. Validate schemas and digest bindings; modifying retained output breaks its old receipt match. Separately demonstrate a required-check failure or enforced budget refusing acceptance.
4. Approve a compatible capability replacement or rollback without rewriting the workflow. Lock and definition identities change; prior evidence remains. Revalidate compatibility and affected outputs, then compare evidence.

Start with internal capability catalogs; prove a bounded slice another team can reuse without its author.

The payoff: **Which factory configuration produced this artifact, what rules evaluated it, and what supports accepting it?**

**Status/scope, 2026-09-25:** APM and Copilot-based APMX v0.4.2 record/check foundations exist; OpenCode, authored bounded repair and standards export are proposed. This proof remains to be demonstrated. Records narrow investigations; they do not prove causality or capture every model influence. Record unknowns; logs are bounded, access-controlled supporting material. Unsigned schema-conformant provenance is not authenticated attestation or a SLSA level. Trusted CI/issuer signing and protected gates add assurance, not correctness or compliance certification. Refusing acceptance does not prevent every unauthorized runtime action.

**Sources:** [APM](https://github.com/microsoft/apm) | [Agent Plugins](https://agent-plugins.org/) | [SLSA provenance](https://slsa.dev/spec/v1.2/build-provenance) | [in-toto Statement](https://github.com/in-toto/attestation/blob/main/spec/v1/statement.md) | [Test Result](https://github.com/in-toto/attestation/blob/main/spec/predicates/test-result.md) | [OWASP prompt injection](https://genai.owasp.org/llmrisk/llm01-prompt-injection/)
