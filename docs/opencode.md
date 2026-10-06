# Development OpenCode profile

The unpublished source adapter accepts `--on opencode` through the same
preparation, execution, capture, checking, bounded repair and record owners as
Copilot. It does not add a second execution engine, dependency resolver or
acceptance model. Contract bodies, selected capabilities, original application
bytes, handoffs and checks are unchanged; only the native invocation and
observation adapter differ.

**Native proof is still gated.** Deterministic protocol/engine conformance and
actual OpenCode 1.2.24 preparation with the shared MCP artifact server have
passed. A genuine minimal inference attempt selected the native default
`github-copilot/claude-sonnet-4.6`; its provider rejected that model with
`model_not_available_for_integrator`. OpenCode exited zero but emitted a JSON
error, which is a failure, not completion. No positive OpenCode code-producing
or five-stage factory proof is established. A supported model requires an
explicit operator choice before another native rehearsal. Copilot observations
and synthetic test events are not evidence of OpenCode inference.

## Prerequisites and selection

This profile requires **OpenCode 1.2.24**, Git and the existing APMX source
environment. Other native versions refuse rather than assuming compatibility.
Authenticate in OpenCode itself using the intended provider. APMX neither
supplies nor copies credentials. Native authentication does not imply access
to every listed model.

For an already prepared trusted consumer, preview the same local factory with:

```sh
apmx . --on opencode --plan
```

Preview neither runs startup probes nor proves native access. Execution needs
the existing explicit host-access consent and, for factories, unproven-handoff
consent:

```sh
apmx . --on opencode --allow-host-access --allow-unproven-inputs
```

Add `--model provider/model` only after replacing that placeholder with an
explicitly chosen native model identifier. Without `--model`, OpenCode retains
its native configured/default selection. APMX never switches providers or
falls back to another model after an error. Single-contract execution omits
the factory-only `--allow-unproven-inputs`.

The same `--from PACKAGE_REF .` route supports prepared source factories;
this adapter does not fix the official APM standalone-resource archive gap
or publish a development package. Compare harnesses using exactly the same
factory bytes, capabilities, original application and repair budgets.

## Bounded native startup

Startup uses supervised, deadline-bound native version, effective-configuration
and global-path probes. Probe stdout is bounded to 256 KiB. Raw configuration,
credential values and probe stderr are not copied into observations. These are
native host processes, not a sandbox or an offline guarantee.

The private profile selects the builtin `build` agent and denies all tools
except native read, selected skill names and the shared bounded artifact
operations (`write_file`, `delete_file`, `export_changes`). Shell, native
editing, delegation, sharing, snapshots, formatter and language-server work
are disabled. Project configuration and external skill auto-discovery are
disabled; selected capabilities use explicit captured `skills.paths`. Existing
unselected MCP servers are disabled only in the child profile; saved native
configuration is never rewritten.

Configured permissions or legacy tool settings refuse rather than being
silently replaced. Global `AGENTS.md` (including a dangling symlink), plugins,
instruction lists, overridden builtin `build`, `title`, `summary` or
`compaction` roles, or a custom default agent also refuse. Unselected named
agents are not invoked. Native internal title/summary/compaction model calls
can still occur; delegation denial is not a promise of one model call or a
hard spending cap. Inherited
`OPENCODE_CONFIG_CONTENT`, `OPENCODE_CONFIG_DIR` or `OPENCODE_PERMISSION`
overrides refuse; their values are not read into diagnostics. The final native
effective configuration must match the bounded profile, including managed
configuration precedence, or execution stops before inference.

Native installation and same-user host access remain outside isolation.
APMX's existing project-policy, unresolved-policy and Git-remote refusal
boundaries still apply. Do not strip policy, remove real remotes or alter
authentication to make an example run.

## Observations and completion

OpenCode JSONL has step, text, terminal tool and error observations, not a
Copilot-style final exit envelope. The adapter requires a matched final model
step with reason `stop`; `tool-calls` alone is intermediate. Any native error,
failed tool, malformed bounded frame or mixed session prevents completion,
even when the operating-system exit code is zero. The shared engine still
requires successful process termination, confirmed cleanup, intact frozen
resources, captured declared outputs and passing independent checks.
The shared artifact server's durable failure state also blocks completion:
OpenCode's MCP wrapper can turn an `isError` response into a completed native
tool observation, so tool-event status is not the artifact-operation authority.

Public text uses the shared bounded/redacted diagnostic path. Reasoning, tool
arguments/results, skill bodies and arbitrary JSON metadata are not published.
`OpenCode > Loaded skill: NAME` denotes a completed native skill-tool
observation, not an assessment of the result or proof of causal improvement.

This native JSONL version does not report model identity. Requested model
selection is recorded separately; observed models remain unknown rather than
being inferred from the request, configuration or a successful process exit.
The adapter does not manufacture a native exit-code envelope either.

[Bounded repair](repair.md) retains the same budgets, frozen inputs/checks,
attempt lineage and fail-closed retry decisions. Startup/native/protocol errors
are not repaired by changing the goal, model or provider.
