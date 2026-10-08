# Textual design: understand and operate the factory

Status: approved for design and implementation on 2026-10-07.
Repository: https://github.com/danielmeppiel/apmx
Starting baseline: ac21f4c9b5a999b7d320186ff43bc9ef52a2d723.
Parallel companion: security-story.md.

## Outcome

Provide a real interactive terminal application, not another stream of styled
log lines. A user should immediately understand which contracts exist, what is
running, what is blocked, what each contract consumes and produces, which checks
matter, and whether the user needs to act.

Use Textual for interaction, focus, layouts and scrollable views. Retain the
existing Rich/plain path for noninteractive terminals and accessibility.
Do not rewrite the runtime in another language or replace canonical execution
and evidence decisions with UI state.

## Information architecture

1. Persistent factory overview: actual dependency graph and stable stage states.
2. Selected contract: inputs, outputs, current activity, attempt allowance,
   elapsed time, checks, dependencies and reason for blocking.
3. Readable live producer and checker activity, with navigation to other stages.
4. Pinned attention area for permission requests, actionable errors and failures.
5. Evidence status that distinguishes creation, verification, authentication
   and receiver acceptance.

Illustrative layout, not a fabricated graph or promised runtime result:

```text
[plan OK] -> [spec OK] -> [build RUN] -> [docs WAIT] -> [review WAIT]

BUILD                            Attempt 1 of 3 | elapsed 00:24
Inputs: captured                 Outputs: awaiting checks

[ Agent activity ] [ Checks ] [ Inputs / Outputs ] [ Evidence ]
Readable live activity for the selected contract...

Tab: navigate   Enter: inspect   /: search   d: diagnostics
```

Render the real DAG, including branches and joins, with selectable contract
cards. Display declared inputs separately from actual captured observations.
Differentiate producer completion from check acceptance, and advisory review
from a release gate. Do not invent progress percentages or model identity.

## Normal and verbose output

- Normal: readable public narration, useful tool activity, actual captured inputs,
  checker commands and live checker output, outcomes and actionable failures.
- Native DEBUG/INFO plumbing is not normal content and must not appear as an
  endless low-contrast grey stream. Diagnostics has its own view and labeling.
- `--verbose` makes native diagnostics available explicitly, preserving existing
  privacy filtering, bounds and separation from the evidence transcript budget.
- Do not hide successful checker output: make it live and accessible in the
  selected contract's checks view. Keep failure context visible.
- Never expose private reasoning, inventory-probe secrets or token values.
- Requested and observed model identity are distinct. Unknown stays unknown.

## Visual and interaction design

- Strong headings and clear contract boundaries; readable body text in both
  light and dark terminals. Dim styling is only for genuinely secondary detail.
- Blue/cyan activity, green accepted results, amber attention and red failure,
  with explicit labels and symbols so color is never the sole signal.
- ASCII arrows and borders comply with current source/output rules. Do not
  assume a Unicode exception; avoid emojis.
- Keyboard-first stage selection, tabs, detail views, log search and copyable
  paths. Scrolling back pauses auto-follow; new messages must not steal position.
- Bounded buffers and virtualization where appropriate; producer output cannot
  starve rendering or control handling. Do not block execution on animation.
- Effects should communicate real state changes only. Support reduced motion.
- Responsive layouts: wide graph, compact graph/list, and narrow stacked cards.
- NO_COLOR means monochrome, not necessarily noninteractive. Provide an explicit
  plain mode for screen readers, TERM=dumb, CI and redirected output.

## Architecture constraints

The engine remains the owner of execution outcome, input capture and retry
semantics. ContractLogger remains the semantic presentation boundary. The TUI
and plain renderer consume the same canonical observations.

Use a bounded, thread-safe/event-loop-safe adapter for the synchronous engine
and process supervisors. Textual widgets do not start independent executions,
recompute acceptance, read secrets or maintain a second evidence policy.

Keep normal output, diagnostics, retained transcripts and artifact bytes separate.
Preserve terminal-injection sanitization and redaction boundaries. Render
untrusted native text as text, not executable markup or terminal control input.

Design and test real stdin ownership: loading consent, execution consent,
native interactive prompts and the TUI keyboard cannot compete for the terminal.
Both existing approvals remain explicit and default-No.

Prompt handoff must not simply suspend the entire live view for every producer
process; the point is to show producer activity while it runs. Suspend/restore
only for genuine foreground interaction, with a reliable return to the UI.

Cancellation must stop/reap the actual supervised child process tree and record
the real outcome. Leaving a view is not silently equivalent to abandoning a
running model. Restore terminal modes on success, failure and Ctrl-C.

On completion, retain an inspectable result view and print a concise plain
summary with copyable artifact/evidence paths when the TUI closes.

## Surface and rollout

Prototype an explicit `--tui` entrypoint and a permanent `--no-tui` escape hatch;
these are proposed flags, not claims about current capability.
After capability and compatibility checks pass, select the TUI automatically
for supported interactive terminals. Plain mode remains stable for automation.
Document deterministic precedence of explicit flags, terminal detection and
accessibility settings. Load Textual lazily where practical.

The plan view may use the same graph vocabulary without executing a model.
No preview, replay or unit test is evidence that a live native harness works.

## Evidence state

Keep these states separate:

- Execution complete.
- Evidence package created.
- Content independently verified.
- Signer authenticated.
- Enterprise policy accepted.

Until a real verifier or receiver result is wired in, mark those latter states
as not checked/unknown. Do not infer them from successful local execution.
The security track owns verification and receiver policy. The TUI only presents
observed results through an agreed interface.

## Mandatory terminal design-review loop

Added by the maintainer on 2026-10-07: the designer must repeatedly inspect
the running design in the terminal itself against high-quality TUI
standards. Source inspection, passing unit tests and static wireframes are
not design acceptance.

For each major screen and interaction milestone:

1. Run the actual application in a real interactive terminal/PTY. Use
   synthetic public events or a clearly labeled replay for rapid visual
   iteration; do not spend new model calls merely to change spacing or
   color.
2. Capture and visually inspect the rendered screen, not only its text log.
   Exercise actual keyboard focus, stage selection, resizing, scrolling,
   prompt handoff and cancellation. Inspect transitions under active output.
3. Compare the result with a written rubric and primary-source examples
   from polished TUIs such as Lazygit, K9s and Textual applications. Borrow
   principles of hierarchy, density and discoverability, not their
   workflows or styling indiscriminately. Do not claim an objective
   "world-class" certification.
4. Record specific defects, implement the corrections, rerun and inspect
   the same scenarios. Repeat until the acceptance gaps are resolved. Keep
   genuine before/after captures and a short rationale for meaningful
   design changes.
5. Repeat the visual pass on the packaged native executable and the
   required real-harness paths, distinguishing those receipts from
   fixture/replay results.

Rubric:

| Dimension | Acceptance question |
| --- | --- |
| At-a-glance understanding | Are current contract, state, blocker and next user action immediately identifiable? |
| Hierarchy and density | Can users distinguish graph, contract identity, agent activity, checker output and attention without reading a wall of text? |
| Contrast | Is body text readable on supported light/dark palettes, with a 4.5:1 target for controlled theme pairs and no essential dim-grey content? |
| Non-color semantics | Are all states understandable in monochrome, with visible keyboard focus? |
| Navigation | Are bindings discoverable, focus predictable, logs searchable and paths copyable without a mouse? |
| Stability | Do incoming messages preserve scroll position and avoid flicker, layout jumps and lost focus? |
| Responsive layout | Do 80x24, 120x40 and 160x50 work, with explicit compact behavior at 60x20 and during live resize? |
| Honest state | Are planned, captured, running, checking, retrying, accepted, failed and blocked distinct, with provenance assurance levels kept separate? |
| Attention and recovery | Do approval, failure and cancellation present a clear action without burying context or leaking child processes? |

Cover loading/execution consent, running producer, noisy output, successful
and failed checks, retries, a branched DAG, native input handoff, completed
evidence, cancelled execution, diagnostics, light/dark/monochrome and
reduced motion. Do not claim terminal accessibility compliance from a
contrast measurement alone.

Textual SVG exports and snapshot tests supplement the loop but do not
replace interactive terminal inspection. If real rendered-screen
capture/inspection is unavailable, report that exact limitation and leave
visual acceptance open. No fabricated screenshots or simulated claims of
human usability testing.

### Milestone 1: prototype, headless-supplemental pass

Scope exercised: the real resolved graph for `examples/contracts/branched-demo`
(a synthetic fork/join factory added for this purpose), driven two ways: a
labeled, non-authoritative fixture-replay driver (`src/apmx/tui/fixtures.py`)
posting the same `RunEvent`/status-change shapes a real engine would (every
surface it drives is banner-marked "FIXTURE REPLAY — not a real run"); and
headless `Pilot`/`App.run_test()` + `export_screenshot()` -> SVG ->
`rsvg-convert` -> PNG, labeled "headless SVG-derived, supplemental" (not a
substitute for interactive PTY inspection).

A genuine interactive-terminal route was also attempted: a real PTY bridged
over a loopback websocket to xterm.js served in this session's `browser`
canvas. The canvas itself works (confirmed via server access logs), but
self-capturing it failed: every `browser` canvas read/screenshot action
requires a `page_id`, produced only by an `open_browser_page` tool that does
not exist here. Conclusion at the time: the harness works for a human, but
this agent could not self-capture it in-session — later resolved in
milestone 2 (below) via a scoped local Playwright instance instead of the
app's browser-canvas tool.

Defects found and fixed from the headless pass (each rerun post-fix to
confirm):

1. **Pending/running glyph collision.** Pending cards reused the
   `running` glyph (`[>]`), misrepresenting idle work as in progress.
   Fixed with a distinct `[ ]` glyph for `pending`.
2. (Additional defects of the same class — ambiguous status/labeling and
   layout issues surfaced by the headless pass — were fixed the same way:
   found, corrected, recaptured, confirmed.)

### Milestone 2: real backend wiring and genuine headless-browser capture

1. **Backend-dependent test gap closed.** Milestone 1's full-suite failures
   (82 failed / 38 errors) were re-investigated once a backend binary was
   located rather than left out of scope: a provisioned, official APM
   0.30.0 binary already exists read-only inside the installed demo kits
   (`.demo/native/apmx-macos-arm64/libexec/apm/apm`). Setting
   `APMX_APM_BACKEND` to that path (no copy, no reinstall) dropped the
   failure count to 1 failed / 2092 passed / 57 skipped. The one remaining
   failure (`test_python_command_preserves_virtual_environment_identity`)
   was isolated to a pre-existing defect in the shared read-only `.venv`'s
   CPython build (a missing sibling `libpython3.12.dylib` on copy) — a
   toolchain defect out of this track's ownership, diagnosed and reported,
   not patched in the shared environment. Building this track's own venv
   from an unaffected interpreter (`/opt/homebrew/opt/python@3.13`) and
   rerunning the full suite there instead: 2149 passed, 11 skipped, 0
   failed.
2. **Genuine headless-browser screenshot capture achieved.** The app's
   `browser` canvas tool cannot self-capture, but that only blocks the
   canvas *wrapper* — a local, scoped Playwright (installed only as a
   devDependency of the scratch `termviz/` harness, nothing global) was
   pointed directly at the already-running xterm.js/PTY-bridge localhost
   page and captured real PNG screenshots. Honestly labeled
   **headless-browser terminal-emulator capture**: a real browser
   rendering real terminal output over a real PTY, not a substitute for
   human-interactive review and not the app's own browser-canvas tool.
3. **Two scratch-harness bugs found and fixed via this capture loop** (both
   in `termviz/` scratch tooling, not shipped code): `pty_bridge.py` failed
   to URL-decode query-string params before JSON-parsing the command; and
   `devrun.py` called a nonexistent `workers.wait_for_idle()` instead of
   `workers.wait_for_complete()`. Both were only visible once a genuine
   browser captured real rendered/crashed output — headless Pilot+SVG
   snapshots could not have caught the query-string bug.
4. **Native packaging feasibility: one target genuinely proven, not
   estimated.** `pyinstaller==6.16.0` (already declared in `pyproject.toml`'s
   `build` extra) built `build/apmx.spec` for the current host
   (`macos-arm64`) in 14.3s, producing a 29 MB bundle (8.2 MB executable).
   Measured cold start (`apmx --version`, 5 runs): ~0.09s real each.
   Launched the frozen binary's `--tui --plan` path inside a genuine
   `pty.fork()` child with real `TERM=xterm-256color`: it emitted genuine
   Textual alternate-screen output — proof the prototype runs correctly
   frozen, not just from source. The remaining four native targets
   (`linux-x86_64`, `linux-arm64`, `macos-x86_64`, `windows-x86_64`) are
   not locally cross-buildable on this macOS-arm64 host (PyInstaller does
   not cross-compile); they ride the project's own CI matrix
   (`native-notice-ci.yml`, driven by `scripts/release.py::TARGETS`) by
   design, not as a local gap.
5. **Genuine cross-size terminal screenshots and an honest visual
   critique.** Captured `shot-80x24.png`/`shot-120x40.png` via the same
   headless-Chromium-to-PTY path. Findings: a real low-contrast
   pending-card border against the black background (fixed before
   milestone 4 sign-off); no clipping/truncation at either size; a minor
   scrollbar-sizing polish item in `GraphView`; and an explicit
   limitation that focus-ring contrast was not captured by this
   fixture-replay pass (no programmatic focus), deferred to milestone 4.
6. **Genuine stdin consent screen proven; cancellation kept test-only
   after review, not shipped as a second supervisor.** Added
   `apmx/tui/consent.py` (`ConsentScreen` only) plus consent and
   process-cancellation-mechanism tests
   (`tests/unit/tui/test_consent.py`). `ConsentScreen` focuses "No
   (default)" on mount; a bare Enter or Escape declines without ever
   touching "Yes" — matching the CLI's existing default-No
   `--allow-host-access` gate, wired to the engine's existing admission
   flow rather than inventing a second consent model (enforced by the
   existing `test_architecture.py` import-boundary guard). A
   `CancellableChild` supervisor prototype was built then **removed**
   after review: it duplicated lifecycle ownership belonging to
   `apmx.contracts.process`'s canonical supervisor, was POSIX-only (no
   Windows path for all five native targets), and its first test raced
   (signalling before the child's handler was confirmed armed). The fixed
   mechanism check now lives only in the test file: each scenario reads
   an explicit "ready" line before signalling, and a grandchild-process
   scenario confirms `killpg` on the group reaps the whole process tree,
   not just the immediate child.
   Deferred to milestone 3 by design: wiring `ConsentScreen` into
   `FactoryApp`'s live rendering path, and the real cancellation path via
   `apmx.contracts.process::supervise_process` rather than a new
   supervisor — `--tui` today is only reachable from the `--plan` (no
   execution) branch.

### Milestone 2 correction: frozen `--tui` live/interactive path was never actually proven

The milestone 2 entry above only proved the frozen binary's `--plan`
(no-execution, preview-only) path rendered Textual output — it never drove
a live run or any keystroke through the frozen executable. The project's
own native CI (`native-notice-ci.yml` + `scripts/smoke.py`) builds all five
archives and exercises them, but `smoke.py` never invokes `--tui` either —
it is real proof of frozen packaging/notices/backend-staging, not of the
frozen TUI's startup or interactivity. Re-verified directly, source-free,
once this gap was flagged:

1. **A fresh, from-spec rebuild for the current commit crashed
   immediately** the first time `--tui` (live path, not `--plan`) reached
   any `TabbedContent`/`TabPane` usage: `ModuleNotFoundError: No module
   named 'textual.widgets._tab_pane'`. Root cause: `textual.widgets`
   resolves those names through a module-level `__getattr__` lazy import
   (`textual/widgets/__init__.py`), so PyInstaller's static import graph
   never sees the backing submodules — this is a genuine, 100%-reproducible
   packaging defect in `build/apmx.spec`, invisible to any source-run test
   or to `--plan`-only smoke coverage, because the Checks/Evidence/Output
   tabs are core to every real `--tui` session.
2. **Fixed** by adding `collect_submodules("textual")` to the spec's
   `hiddenimports`. Bundle size grew honestly from 29 MB to 80 MB (pulls in
   Textual's full widget/driver surface, not just what this app imports) —
   recorded here rather than hand-picking a narrower hidden-import list,
   since the lazy-loader pattern makes a minimal allowlist fragile against
   future Textual versions.
3. **Verified genuinely, source-free, against the rebuilt frozen binary**
   (macOS arm64, current commit): real `pty.fork()` child execing the
   frozen executable directly (not `python -m apmx`, no `textual`/
   `pyinstaller` on the child's `PATH`), forced `TERM=xterm-256color`, the
   real read-only-sourced APM 0.30.0 backend staged into the bundle's
   `libexec/apm/` (the frozen loader intentionally refuses `PATH`/env
   overrides — see `apmx/install/apm_backend.py::locate_backend`, "Never
   consult PATH or source overrides in a frozen release" — so this is the
   only legitimate way to exercise a frozen build's real backend lookup),
   run against the same hermetic 4-contract live factory used for
   milestone 4/5 source evidence. Captured: genuine alternate-screen entry
   (`CSI ?1049h`), a real mid-run `k` keystroke switching to the Checks tab
   with live content rendered, genuine alternate-screen exit
   (`CSI ?1049l`), and the authoritative plain-text `apmx factory: COMPLETE
   (4/4 contracts passed)` summary with a real evidence-record path
   surviving after exit — all emitted by the frozen executable itself, not
   source. Correction: the frozen run's evidence record reports
   `assurance.profile: "native-advisory"`, but this field does **not**
   distinguish frozen from source execution — the genuine source-launched
   runs in milestone 5 (via `.venv/bin/python -m apmx`, separate from this
   frozen proof) report the identical `native-advisory` profile. The
   frozen/source distinction here rests only on the launch mechanism
   actually used (direct frozen-executable `pty.fork()` vs. `python -m
   apmx`), not on anything the evidence record itself asserts.
4. **Scope note for the remaining four native targets**: this proof is
   necessarily macOS-arm64-only (no local cross-build). The other four
   targets' CI builds are real (they build+notice+smoke-check the actual
   archives), but — per the correction above — none of their CI jobs
   invoke `--tui` either, so **frozen-TUI-interactive proof currently
   exists for exactly one of five targets** (this host). The
   `collect_submodules("textual")` fix is target-independent (a spec-file
   change, not a platform-specific code path), so there is no known reason
   it would behave differently on the other four, but that is an inference
   from the fix's mechanism, not an observed proof — it is reported here
   as such rather than rounded up to "all five platforms verified
   interactive."

## Milestone 3 implementation notes

Milestone 3 wires the live `--tui` path through the real engine instead of
the fixture-replay prototype from milestone 1. `src/apmx/commands/tui_live.py`
hosts the bridge:

- `LiveContractLogger` implements the same `ContractLogger` protocol as the
  plain/Rich loggers. It is the only component that calls back into the
  running `LiveFactoryApp`, and does so exclusively via Textual's
  `call_from_thread`, since the real chain runs on a worker thread while the
  app's event loop owns the main thread. It never invents progress, model
  identity or outcomes; native DEBUG/INFO plumbing still never reaches the
  normal activity stream and still respects `chain.py`'s existing
  redaction/bounding rules — the bridge adds no filtering of its own.
- `LiveFactoryApp` extends the prototype UI with a real cancel keybinding
  (`c`). Pressing it only ever sets a cooperative, idempotent flag; it never
  sends an OS signal directly. That flag is read by `supervise_process` via
  the existing `cancel_requested` callable, so a UI-requested cancellation
  reuses exactly the same SIGTERM/SIGKILL and process-group reaping path
  already used for timeouts and KeyboardInterrupt — there is no separate
  "UI cancellation" code path in the process supervisor.
- `launch_live` disables local terminal echo for the duration of the run,
  drives the real `run_chain`/engine on a worker thread, and always restores
  terminal state on exit (including on cancellation), so the UI cannot leak
  in-flight inference state into the restored shell.
- Both existing default-No consent prompts (host access, unproven inputs)
  are unchanged: asked before the live TUI is entered, using the same
  governance and `--allow-*` flags as the plain path.
- The no-`--plan` live path requires a real TTY on both ends; when one is
  missing it refuses with the same `UNPROVEN`/`tui_unavailable` message as
  the existing `--plan`+`--tui` preview path.

### Test coverage (hermetic/replay vs genuine proof)

"Genuine proof" means every part of the stack runs for real — real
`supervise_process`, real checks, real `ContractLogger`, real file I/O, a
real process group — except the AI harness invocation, swapped for a
deterministic local Python script via `RuntimeFactory.get_runtime_by_name`.
This avoids model spend while proving real wiring, distinct from
fixture-replay (`src/apmx/tui/fixtures.py`, used only by milestone 1's
`--replay` path).

- `tests/unit/tui/test_live_bridge.py` (6 tests): event-to-card-status
  mapping for a real two-node chain; cooperative cancellation before and
  between leaves, confirming the chain stops early with the real halted
  outcome; native diagnostic events routed only to the diagnostics pane;
  the cancel keybinding exercised through `run_test()`/`Pilot`; `launch_live`
  disabling local echo and driving the real chain to completion.
- `tests/unit/tui/test_cli_flags.py`: the no-`--plan` live path's TTY
  refusal, proven end-to-end through the real CLI and real APM backend.
- `tests/unit/contracts/test_reliability.py`: a real end-to-end proof that
  `cancel_requested` genuinely terminates and reaps a spawned, running
  child process, mirroring the existing timeout/lingering-children tests.
- `tests/unit/test_windows_process.py`: the Windows job-object equivalent
  (skipped off-Windows, not independently run on this POSIX host).

All new and existing tests pass together against a real APM 0.30.0 backend
supplied via `APMX_APM_BACKEND` in this development environment (normally
bundled/provisioned, not required as an environment variable in CI). `ruff
check` is clean on all touched files.

### Open items carried into milestone 4

- Milestone 3 proves live wiring and cooperative cancellation; it does not
  yet validate behavior across terminal widths, color modes, `NO_COLOR`,
  reduced motion, screen-reader/plain fallback or output redirection — that
  is milestone 4.
- No genuine (real-model) run has been executed yet for this track;
  milestone 3's tests all substitute a deterministic local script for the
  AI harness call. A bounded, explicitly authorized genuine run is planned
  for milestone 5 validation, not before.

### Milestone 4: genuine live-engine visual validation

Unlike milestones 1-2 (fixture-replay captures only), every screenshot here
drives the **real** `apmx` CLI / Textual `LiveFactoryApp` / `ContractLogger`
/ process supervisor / `ContractStreamDecoder` pipeline end-to-end as a
genuine OS subprocess inside a real PTY, with only the AI harness call
swapped for a hermetic local stand-in — the same substitution precedent the
project's own hermetic pytest tests already use. No model call, no network,
no spend. A real 4-contract fork/join factory (`plan → {design, spec} →
build`) is used throughout. Screenshots are headless-browser (Playwright +
Chromium, scoped to this track's own scratch tooling) renders of a real
xterm.js terminal connected to that real PTY.

1. **A real product bug was found and fixed via live-engine dogfooding.**
   Raw PTY output showed the engine completing with
   `native_completion_unobserved`/`finished: unknown` and narration lines
   reading `[x] check None None` for checks that had genuinely passed.
   Two causes: (a) this track's hermetic stand-in script did not emit the
   terminal JSONL `result` line `ContractStreamDecoder._native_result`
   requires — fixed in the scratch harness only; (b) a genuine,
   reproducible regression in shipped code — `engine.py::_run_checks()`
   emits `check_finished` with a `CheckObservation` dataclass, but
   `tui/app.py::_format_event` and `commands/tui_live.py::_present` only
   read the flat keys the invented replay fixtures use, so every real
   check misreported as `None`/`None` and incorrectly flipped to
   `"retrying"`. **Fix** (`src/apmx/tui/app.py` +
   `src/apmx/commands/tui_live.py`): a shared `_check_finished_fields(event)`
   helper reads the real `CheckObservation` shape via `getattr`, falling
   back to the fixture's flat keys — mirroring the pattern
   `core/contract_logger.py` already uses correctly for the CLI's non-TUI
   output. Regression tests added
   (`test_format_event_reads_real_engine_check_finished_shape`; an
   existing live-bridge test strengthened to assert no spurious
   `"retrying"` on an all-passing run). Full suite against the real APM
   0.30.0 backend: 1948 passed, 58 skipped.
2. **Both consent prompts, genuine**: real resolved dependency counts, real
   APM install log lines, both still defaulting to `[y/N]`; the wider
   capture shows the full real 4-contract graph description above the
   second prompt.
3. **Genuine running dashboard**, narrow and wide: the real fork/join DAG
   mid-run (3 genuinely passed/green, 1 genuinely running/orange), title
   bar, detail pane, activity narration sourced from real engine events,
   footer keybindings — post-fix, with correct narration.
4. **Genuine keyboard navigation**: a scripted real `Tab` keypress moving
   focus to a contract card, with the detail pane correctly repopulating
   from the live engine's real metadata.
5. **Genuine `NO_COLOR` live-engine evidence**: the scratch harness's
   `env=<json>` passthrough set `NO_COLOR=1` on the real spawned child
   before `exec`; the running dashboard rendered fully in monochrome,
   confirming Textual's native `NO_COLOR` handling end-to-end through the
   real `LiveFactoryApp`, not just assumed from documentation.
6. **Genuine cooperative-cancellation evidence** — "cancellation reaps
   supervised children; UI exit cannot leak inference" proven true of the
   live TUI's actual behavior, not just an engine-level claim: a scripted
   `c` keypress mid-run, captured ~100ms later still mid-transition and
   ~300ms later with the TUI fully exited and the terminal cleanly
   restored to its pre-run scrollback (no partial inference, no corrupted
   redraw). `pgrep` against the scratch harness's own processes
   immediately after confirmed no matches — the supervised hermetic child
   was fully reaped, not merely hidden by the UI exiting.
7. **Native-prompt/`c`-keybinding safety analysis**: every producer
   subprocess this track's TUI supervises is launched with
   `stdin=subprocess.DEVNULL`, so there is no channel through which a
   native tool/model prompt could ever read from, or be answered by, the
   user's terminal while the TUI owns it — the cancel keybinding cannot
   race with a live native prompt because there is never a prompt to race
   with. Validated both by code reading and by direct observation of a
   scripted cancellation leaving no partial or leaked content on screen.
8. **Remaining honest gaps carried forward**: screen-reader/plain-fallback
   output and output-redirection were validated only via fixture-replay
   and Textual's own plain/headless rendering, not re-captured against the
   live engine this milestone; reduced motion
   (`TEXTUAL_ANIMATIONS=none`) was confirmed by code-reading only. Lower
   risk than the gaps closed this milestone (they exercise Textual's own
   documented env handling, not this track's event-bridging code), but
   noted rather than silently marked complete.

All milestone-4 screenshots are persisted at (absolute paths, session-scoped
scratch storage, not part of the repo):
`~/.copilot/session-state/5072cbd4-7912-45c6-b6ab-d4ba84303cb7/files/termviz/evidence-m4/`.

### Milestone 5: genuine model exercise, Windows regression fix, and the five remaining open items

**Genuine model runs.** Exactly one authorized genuine run per harness was
executed against the real APM 0.30.0 backend, launched via
`.venv/bin/python -m apmx` (the shared toolchain interpreter, not the frozen
native binary — these two launch paths are evidence-distinct; see below),
against a minimal real `handoff.contract.md`:

- **Copilot**: completed genuinely — `outcome: COMPLETE`, `exit_code: 0`,
  real `handoff.json` artifact (sha256 `99dd95d2...`), its `handoff` check
  passed (`returncode: 0`). This is a real, successful native model
  generation and a real passing check, not a replay.
- **OpenCode**: this is a **real CLI invocation and a real error path**, not
  a successful model generation and not proven model spend. OpenCode CLI
  `1.2.24` was launched with its own default `providerID=github-copilot`,
  `modelID=claude-sonnet-4.6` (OpenCode's default, not overridden by this
  track); the native process reported `stop_reason: "native_protocol_error"`
  via an `AI_APICallError` naming an integrator/model mismatch, before any
  model text was generated. The chain record reflects this honestly:
  `complete: false`, node `state: "stopped"`, `outcome: HALTED`,
  `exit_code: 22`, `artifact: null`. Treat this as evidence that the
  harness-selection and native-error-surfacing path works correctly, not as
  evidence the model ran.

Both records were independently reviewed: transcript SHA256s match the
chain records, and the Copilot run's `handoff.json` passes the project's
own trusted `checks/check_handoff.py` checker.

**Frozen vs. source launch, and why `native-advisory` cannot distinguish
them.** Both of the genuine runs above were launched via
`.venv/bin/python -m apmx`, i.e. from source through the shared toolchain
interpreter — deliberately kept separate from the milestone-2 frozen-binary
proof (the packaged native executable). Both report
`assurance.profile: "native-advisory"` in their chain records. That profile
name does **not** by itself prove frozen-vs-source identity: it is reported
identically by genuine source-launched runs (confirmed here) and by the
frozen binary (milestone 2). The frozen/source distinction rests only on
which launch mechanism was actually used for a given run, never on
anything the evidence record itself asserts — the milestone 2 correction
above applies the same fix to that earlier claim.

**Windows CI hang, root-caused and fixed.** The Windows native job was
hanging on exit. Root cause: `commands/tui_live.py` used Textual's
`call_from_thread` to hand a result back to the app's event loop from a
worker thread; on Windows, if the app's loop had already stopped by the
time the worker thread called back in, `call_from_thread` blocked forever
waiting for a reply that would never arrive, hanging the process instead of
exiting. **Fix**: a `_fire_and_forget` helper that posts the message
without blocking when the loop may already be gone. A deterministic
regression test,
`test_fire_and_forget_does_not_block_when_the_apps_loop_has_already_stopped`,
proves this: reverting the fix makes the test fail (hang) deterministically,
confirming the test actually exercises the bug rather than passing
vacuously.

**Final native CI conclusion.** Commit `8caa721` (and the mechanical
`ruff format` follow-up, `6164408`) ran through the full five-native-target
matrix (macOS arm64/x86_64, Linux arm64/x86_64, Windows x86_64) plus
`validate`, against the real APM 0.30.0 backend where applicable. See the
PR for the exact run conclusion at push time; this is the acceptance gate,
not any local repetition.

**The five open items, closed with exact live results** (per the explicit
ask, each re-verified against the real engine/CLI rather than by
code-reading alone):

1. **Cheap/plain fallback and output redirection.** `apmx --from <factory>
   --on copilot --tui --allow-host-access` with both stdin and stdout
   redirected away from a TTY exits non-zero with a clean refusal:
   `[!] apmx: UNPROVEN` / `The live TUI needs an interactive terminal on
   both ends; omit --tui for a normal run.` No hang, no silent fallback,
   no partial TUI launch attempt. Separately, `--no-tui` (or simply
   omitting `--tui`, since it defaults off) under the same redirected,
   non-tty conditions runs the existing plain-CLI path normally to
   completion — confirming "cheap fallback" means the plain path is the
   default and always available, while the live TUI explicitly refuses
   rather than guessing when it cannot safely own a real terminal.
2. **`NO_COLOR` stays interactive.** Confirmed via `terminal_capabilities()`
   (`src/apmx/utils/console.py`): `NO_COLOR` alone maps to `PLAIN_TTY`, not
   `STREAM` — a real TTY with `NO_COLOR` set remains TUI-eligible and
   renders monochrome (also live-captured in milestone 4, item 5).
3. **Reduced motion.** Live-captured: a genuine live-engine run launched
   with `TEXTUAL_ANIMATIONS=none` renders and runs cleanly end-to-end (real
   fork/join graph, real activity narration) — this track has no bespoke
   animation code of its own; it relies entirely on Textual's native
   handling of that env var, and the live capture confirms nothing in this
   track's own code interferes with or depends on animations being
   present.
4. **Input ownership, confirmed both structurally and behaviorally.**
   Structurally: every producer/check subprocess this track supervises is
   spawned with `stdin=subprocess.DEVNULL`
   (`src/apmx/contracts/process.py:129`) — there is no file descriptor
   through which a native tool/model prompt could ever read the terminal,
   so a native prompt cannot race with the TUI's own input handling
   because there is never a channel for it to read from. Behaviorally: a
   scripted keypress (`k`, switching to the Checks tab) sent while the
   hermetic producer was still genuinely mid-execution
   (`phase: Running the producer`, `HERMETIC_COPILOT_DELAY=2.5s`) was
   immediately reflected in the live-captured frame — proving the
   Textual app's own key-event loop, not any child process, is the sole
   consumer of terminal input while a run is active.
5. **Narrow-terminal detail-pane reachability — an honest gap, not a
   pass.** The graph pane (`min-width: 28`) and the detail pane
   (`min-width: 22`) are laid out side-by-side with no responsive
   breakpoint and no horizontal-scroll fallback. Live-captured at three
   widths: at 100 cols and at 52 cols the detail pane is fully visible
   (cramped word-wrap at 52); at 40 cols it is **not reachable at all** —
   it is pushed fully off-screen with no keybinding, scroll, or toggle to
   bring it back. The practical cutoff is the sum of both min-widths (50
   columns); below that, per-contract detail (status, dependencies,
   checks) is simply unavailable until the terminal is widened. This is
   reported as a known limitation for a future milestone, not silently
   marked complete.

Milestone-5 screenshots (`shot-reduced-motion.png`, `shot-narrow-40.png`,
`shot-narrow-52.png`, `shot-input-ownership2.png`) and both genuine chain
records are persisted at (absolute paths, session-scoped scratch storage,
not part of the repo):
`~/.copilot/session-state/5072cbd4-7912-45c6-b6ab-d4ba84303cb7/files/termviz/`
(screenshots at the top level; chain records under
`evidence-m5-live/genuine-model-runs/`).

### A second, distinct Windows CI stall: root cause and fix

A later CI run (`37702976752`) was cancelled by the job-level 35-minute
timeout on `windows-x86_64` only, while all four other native targets
(macOS arm64/x86_64, Linux x86_64/arm64) completed successfully running
the identical test suite including the regression test for the earlier,
already-fixed `call_from_thread` hang. This was a **second, distinct**
stall, not a recurrence of the first.

Exact progress accounting from the job log (27 full 72-dot progress lines
plus one final partial 63-result line, no percentage, immediately before
`The operation was canceled.`) places the stall at test index 2007 of
2172 for the `tests/unit tests/release` selection, which is exactly
`tests/unit/tui/test_live_bridge.py::test_launch_live_disables_terminal_echo_before_the_real_chain_runs`
hanging mid-run — its own result dot never posts, and the next test in
collection order never starts.

**Mechanism:** that test calls `launch_live()`, which calls Textual's
`App.run()` with no `headless=` argument, so it instantiates the real
platform driver. Textual's `LinuxDriver` checks `os.isatty()` on stdin and
falls back to a non-blocking `select()` read loop that tolerates a
non-tty fd fine (why this test passed on macOS/Linux). Textual's
`WindowsDriver` (`textual/drivers/windows_driver.py` / `win32.py`) has no
such fallback: it reads real Win32 console handles with no "no console
attached" non-blocking path, so under a non-interactive CI runner with no
real console it blocks forever.

**Fix:** `launch_live()` gained an optional `headless: bool = False`
parameter (`src/apmx/commands/tui_live.py`), forwarded straight to
`App.run(headless=headless)`. The default is unchanged, so every real
invocation — the only caller is `commands/contracts.py`, already gated on
`invoke_contract` confirming real TTYs on both ends before it ever calls
this function — keeps using the real platform driver and genuinely owns
the terminal exactly as before; this parameter never reaches production.
The test now passes `headless=True`, selecting Textual's own
cross-platform `HeadlessDriver`, which drives the exact same real chain
and the exact same assertions without depending on native OS console
APIs. Verified locally: `tests/unit/tui/test_live_bridge.py` 7/7 pass in
under 3 seconds (previously this test alone could hang indefinitely on
Windows). A full `tests/unit tests/release` run against the real APM
0.30.0 backend (`APMX_APM_BACKEND` pointed at the read-only demo kit's
bundled binary) passed 2113/2172 with 58 skipped and exactly one
pre-existing, unrelated failure
(`tests/release/test_demo.py::test_python_command_preserves_virtual_environment_identity`,
an isolated uv-managed-Python dylib `SIGABRT` on this macOS host,
unrelated to this track's code and not chased as a product regression).

### Cheap-gate proof additions (exact, not broader claims)

- **Plain mode genuinely completes under full redirection, not just
  refuses.** Running the real CLI without `--tui` (`--allow-host-access
  --allow-unproven-inputs`, stdin/stdout/stderr all redirected to files,
  hermetic producer, no model spend) against the same 4-contract
  fork/join factory produced `[+] Factory COMPLETE`, `Contracts: 4/4
  completed`, `Checks: 4/4 passed`, and a retained evidence directory —
  byte-scanning the entire redirected stdout for ANSI escape sequences
  (`\x1b[...`) found **zero**, confirming no alternate-screen or other
  terminal-control codes are ever emitted in the non-TUI path.
- **Detail-pane reachability at the two sizes that actually matter.**
  Live-captured at exactly 60×20 and 80×24 (`shot-60x20-required.png`,
  `shot-80x24-required.png`): the detail pane (Contract/Status/Depends
  on/Needs/Produces) is fully visible and readable at both sizes. The
  previously reported <50-column gap (40 cols) remains an honest,
  disclosed limitation below both of these required sizes, not something
  that needed to expand scope.
- **Reused evidence, not re-captured:** the already-accepted live
  NO_COLOR screenshots are `evidence-m5-live/live-120x40-nocolor.png` and
  `evidence-m4/m4-nocolor-live-120x40.png`; reduced-motion is
  `shot-reduced-motion.png`; input-ownership is `shot-input-ownership2.png`
  — all under the same `~/.copilot/session-state/.../files/termviz/` root
  as above.

## Milestones and acceptance

1. Persist docs/textual-design.md and refine the wireframes against the real
   graph/events. Build a thin Textual prototype before a broad logger refactor.
2. Prove packaging and terminal ownership: native frozen executable, readable
   activity during producer execution, both consents, real prompt handoff,
   Ctrl-C and terminal restoration. Report measured size/startup changes,
   not guessed framework overhead.
3. Implement graph/cards, activity/check views, navigation, diagnostics,
   final summary and plain fallback with regression and architecture guards.
4. Validate wide and narrow terminals, light/dark/monochrome/reduced-motion modes,
   screen-reader/plain output, redirection, noisy streams, forks/joins,
   retries, failures, cancellation and evidence-state honesty.
5. Exercise both Copilot and OpenCode and all five native packaging targets.
   Keep hermetic/native acceptance separate from genuine inference evidence.
6. Open the implementation PR with documentation and validation evidence.
   Do not automatically merge, install over active demo kits or cut a release.

## Track boundaries

This track owns runtime presentation, Textual dependencies, native packaging
adjustments and TUI tests. The security track owns receiving policy/workflows,
public security examples and docs/security-story.md. Coordinate any change to
shared event types, evidence interfaces, pyproject.toml or common CI files before
editing; keep changes additive and avoid duplicate authorities.

## Sources

- https://textual.textualize.io/
- https://textual.textualize.io/guide/workers/
- https://textual.textualize.io/guide/testing/
- https://textual.textualize.io/api/app/
- https://rich.readthedocs.io/en/latest/live.html
