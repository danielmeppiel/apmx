# Enterprise security story: a real receiving gate

Status: approved for design and implementation on 2026-10-07.
Repository: https://github.com/danielmeppiel/apmx
Starting baseline: ac21f4c9b5a999b7d320186ff43bc9ef52a2d723.
Parallel companion: textual-design.md.

## Outcome

Show a factory-generated change being accepted or blocked by a real,
receiver-controlled GitHub required check. Leave reproducible public PRs and
their check results available for inspection. Do not substitute terminal dumps,
contrived screenshots, or a passing test suite that merely simulates rejection.

The presentation uses actual APMX execution, PR checks, independent evidence
verification and stock GitHub attestation verification. Human review and
ordinary software security checks remain necessary.

## Existing capability and missing work

Existing portable evidence contains an in-toto Statement v1 / SLSA provenance
v1 production record, artifact-linked assessment statements, a CycloneDX 1.5
ABOM from APM, retained inputs and outputs, and a normalized factory identity.
The maintained independent consumer checks pinned schemas, bytes and semantic
relationships without importing APMX. These packages are currently unsigned.

New work is the receiving policy, exact Git revision/change binding, trusted
assessment and signing boundary, CI integration, and public PR examples.
Do not invent compatibility with an external verifier that has not been tested.

## Primary scenario

1. A factory produces a candidate patch and evidence for a known application
   baseline. Submit the candidate as a PR, keeping production metadata separate
   from receiver authorization.
2. Receiver-controlled CI uses trusted, pinned verifier and policy code, not
   code from the proposed change. It validates the approved factory definition,
   evidence relationships, required checks, and the exact candidate under review.
3. Bind the source base commit, proposed patch and resulting candidate tree using
   a documented representation. Recompute the relationship rather than comparing
   an arbitrary directory digest with a Git tree ID. Distinguish PR head SHA,
   base SHA and GitHub's synthetic merge SHA. Reject stale evidence after updates.
4. Run receiver-required checks against those candidate bytes in an unprivileged
   execution context. Agent-authored checks cannot redefine receiver policy.
5. A separate protected attestor signs the exact assessed artifacts and records
   the receiver decision. Agent execution and untrusted checks have no signing
   credentials or ability to rewrite the attestor or its policy.
6. A required PR check accepts only the verified candidate and policy result.
   Mutations, unapproved factory definitions, missing evidence and unauthorized
   signers produce genuine failures and block the affected PR revision.

## Meaning of the signature

The first delivered signature authenticates the protected CI assessment of the
candidate. It does not retroactively prove that a local model invocation occurred.
Authenticated factory production requires running production inside a trusted
builder with an appropriate isolation and attestation boundary.

Stock `gh attestation verify` checks signed subjects and permitted signer
identity. It does not, by itself, validate APMX's internal statement relationships.
Both signature verification and the semantic/policy consumer are required.
No SLSA security level or claim of software correctness is implied.

Use approved signer workflow and revision constraints, not only a repository
allowlist. Generate attestations through a pinned supported GitHub action.
Separate source/build type identifiers from attestation predicate types.
Never present a user-controlled predicate field as authenticated platform identity.

Before activation, enforce these distinctions in code and regression tests:

- A workflow path plus mutable branch name is not an immutable signer revision.
  Bootstrap a receiver-controlled digest allowlist or pinned reusable signer.
- Signed definition, assessment set and receiver policy identity must match the
  independently verified candidate context, not only an "accepted" string.
- Successful evidence integrity verification can describe a recorded failed
  checker. Require the receiver's mandated checks to pass; do not treat valid
  schemas and consistent recorded failures as candidate acceptance.
- Run actual receiver-required candidate checks without signing privileges and
  bind their results before the separate attestation step.
- Establish workflow-definition provenance, trigger prerequisites and the exact
  checked PR revision using observed GitHub behavior, not YAML comments alone.
- Refuse oversized Git blobs before reading them into memory. Limit untrusted
  tree listings as well as file counts and bytes written to disk.
- Label all mocked fixture surfaces, including model events and ABOM inventory.
  Empty synthetic inventory is not a real dependency incident-response demo.

### Workflow activation prerequisite

GitHub changed `pull_request_target` on 2025-12-08: workflow source,
`GITHUB_REF` and `GITHUB_SHA` now use the repository's default branch, even
when a PR targets the isolated receiver branch. The workflow must therefore
reach the default branch through approved normal integration; publishing only
the receiver branch does not register its PR gate. Do not bypass this with a
direct push, a default-branch change or an automatic merge.

Workflow context SHA is not proof of the check's PR association. GitHub lists
`pull_request_target` as eligible for required workflow checks, but activation
must still inspect the actual check run, PR head and required-check result.
Do not add a manual check publisher or extra write permissions merely by
assuming that `GITHUB_SHA` is also the check's head SHA. A passing push smoke
run establishes neither PR enforcement nor signer authentication.

## Public examples

Create an isolated receiver/demo base branch in the existing public repository,
with clearly labeled, harmless example PRs. Use an exact-branch required-check
rule if needed; preserve all existing repository protections. Do not modify
main's rules or merge requirements. Do not merge demonstration PRs.

| Example | Required receiving check |
| --- | --- |
| Approved candidate, complete evidence and authorized assessment | Passes |
| Patch changed after the evidence was captured | Fails: candidate binding |
| Self-consistent evidence from an unapproved factory | Fails: factory policy |
| Required evidence omitted | Fails: missing evidence |
| Missing or unauthorized attestation identity | Fails: signer policy |

Prove GitHub actually reports the negative examples as blocked by the required
check, not just red optional checks or draft status. Verify the positive example
is not accidentally blocked by a misconfigured gate. Leave links and exact run
revisions in the public walkthrough.

Prefer separate minimal public fixtures over uploading any existing local demo
record. Fixtures must be explicitly labeled as fixtures. If a genuine new factory
run is needed, use public synthetic inputs, record the real invocation and outcome,
and do not present a fixture or replay as live model execution.

## Secondary scenario: dependency incident response

Use structured ABOM and capability identity matching to identify changes
associated with a revoked capability revision, then reject a new submission
using that revision. Match package identity and immutable revision/digest,
not a substring search. Report recorded dependency association; do not claim
complete runtime observation or infer model consumption from inventory alone.

## Presentation

- Produce the change using a real `apmx` invocation.
- Open the PR and watch the required check with `gh pr checks --watch`.
- Verify the signed artifact with stock `gh attestation verify`, enforcing the
  chosen repository, signer workflow and immutable signer revision.
- Compare one passing PR with the intentionally blocked PRs in GitHub.
- Show the dependency incident as a policy decision, not a wall of inventory JSON.

Use supported built-in PR creation tools when executing this work through the
agent. Document ordinary user CLI commands in the walkthrough.

## Safety and scope

- Treat PR branches, archives and evidence as untrusted data. Enforce bounded,
  safe reads/extraction; never execute PR code in a privileged workflow.
- Do not use privileged pull_request_target jobs to check out or execute PR code.
- Policies and verifier versions come from a receiver-controlled protected source.
- Pin external workflow dependencies and keep permissions job-scoped.
- Do not upload private native logs, local transcripts, credentials, unrelated
  source code or the user's historical demo records.
- Do not mutate either installed demo kit or its active shell leases.
- No direct pushes to main, force pushes, protection bypasses or automatic merges.
- GitHub artifact attestations support public repositories and Enterprise Cloud
  private repositories, not GitHub Enterprise Server. Do not claim universal support.

## Milestones and acceptance

1. Document the threat model, exact revision binding and trusted job boundaries;
   persist this plan as docs/security-story.md in the implementation branch.
2. Implement a tested independent receiver-policy layer and positive/negative
   fixtures, reusing the existing consumer rather than copying its rules.
3. Build the isolated public receiver branch, attestation flow and required check.
   Establish one real positive case before multiplying negative examples.
4. Publish the labeled PR examples; confirm their live check and mergeability
   states; document reproducible commands, limitations and cleanup.
5. Complete incident-response example and review the privileged execution,
   signature, policy and untrusted-input boundaries before calling the track done.

A missing platform permission or unavailable attestation capability is a reported
blocker, never permission to replace real enforcement with a simulated success.

## Sources

- https://cli.github.com/manual/gh_attestation_verify
- https://github.com/actions/attest
- https://slsa.dev/provenance/v1
- https://github.com/in-toto/attestation/blob/main/spec/v1/statement.md
- https://cyclonedx.org/capabilities/
- https://github.blog/changelog/2025-11-07-actions-pull_request_target-and-environment-branch-protections-changes/
- https://docs.github.com/en/pull-requests/how-tos/merge-and-close-pull-requests/troubleshooting-required-status-checks
