# Contract examples

| Example | What it teaches |
| --- | --- |
| [First contract](#produce-and-assess-a-handoff) | Turn notes into a retained JSON handoff and check source-ID coverage. |
| [Packaged handoff](packaged-job/README.md) | Let bundled APM prepare a package and its selected skill. |
| [Software factory](software-factory/README.md) | Run planning, specification, build, test and review with explicit artifact handoffs. |

These are authored, secret-free fixtures, not copies of a governed project.
Copy this directory to a fresh disposable directory outside another Git
repository. Use standalone apmx, Python 3,
and an authenticated native Copilot CLI with access to your selected model.
Do not remove a project's remotes or policy to make it eligible.

## Produce and assess a handoff

From the copied `first-contract/` directory:

```sh
apmx ./handoff.contract.md --on copilot --plan &&
apmx ./handoff.contract.md --on copilot --allow-host-access
```

These commands preserve your configured Copilot model. Add `--model MODEL`
only when you want to select a supported model explicitly.
Planning does not call a model, install packages or execute checks.
`--allow-host-access` is required in terminals and pipes, with no prompt or
remembered consent. Native processes use your host identity: this is not
filesystem/network isolation or a hard spending cap.

Inspect the artifact and record paths printed by apmx. The captured handoff
lives under `.apm/runs/<run-id>/`, not over an existing `handoff.json` in your
project. The standard-library-only checker assesses JSON shape and source-ID
coverage, not the complete factual correctness or quality of the prose.

## Reuse one skill

The second fixture shares the first fixture's source notes and parameterized
checker. From the copied examples directory, prepare its explicit resources:

```sh
mkdir -p reuse-contract/checks
cp first-contract/notes.md reuse-contract/notes.md
cp first-contract/checks/check_handoff.py reuse-contract/checks/check_handoff.py
cd reuse-contract
apmx --from ./ handoff.contract.md --on copilot --allow-host-access
```

apmx prepares the import privately. The contract imports the declared
`handoff-style` package, not an `apm_modules/` path. In the current checkout,
Copilot discovers its skill under `.agents/skills/` and loads it natively;
the skill body is not injected into the prompt. Importing a skill does not
grant shell access or invoke another agent. The extra check
assesses its caution format. Local lock identity plus observed source bytes
does not establish a cryptographic pin or protected provenance.

Native discovery and newer preparation logs require a matching development
build; see [release versus current source](../../docs/install.md#choose-release-or-current-source).

## Read outcomes literally

| Outcome | Meaning in this slice |
| --- | --- |
| VERIFIED / 0 | Reserved; the current native runner cannot establish this result. |
| REJECTED / 20 | A check returned a failed condition, even if another check was incomplete. |
| UNPROVEN / 21 | Includes passing checks: this native run was not sandboxed. Also covers missing output, incomplete checks, or unavailable consent. |
| HALTED / 22 | Execution, cancellation, watchdog, capture or recording stopped the invocation. |

Raw check exits are retained: 0 passes, 1 fails, 2 is incomplete; unknown exits,
missing tools and signals are incomplete. No output does not mean `no_change`.
Every check gets a fresh baseline and the captured file.
Expect exit `21` for the completed examples with passing checks. The output
and record are still saved; the host-isolation limit is not a check failure.

The profile requires positively established no-policy
projects. Governed/unresolved-policy projects, command
leaves, `budget`, `sandbox`, captures, output alternatives and composed jobs
refuse before inference. Passing checks never authorizes merge or delivery.

On Windows replace `python3` check commands with a native Python executable,
using quoted forward-slash paths. Git for Windows' `sh.exe` runs checks;
the native Copilot producer never runs through a `.cmd` shim.
