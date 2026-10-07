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

Added by the maintainer on 2026-10-07: the designer must repeatedly inspect the
running design in the terminal itself against high-quality TUI standards.
Source inspection, passing unit tests and static wireframes are not design
acceptance.

For each major screen and interaction milestone:

1. Run the actual application in a real interactive terminal/PTY. Use synthetic
   public events or a clearly labeled replay for rapid visual iteration; do not
   spend new model calls merely to change spacing or color.
2. Capture and visually inspect the rendered screen, not only its text log.
   Exercise actual keyboard focus, stage selection, resizing, scrolling,
   prompt handoff and cancellation. Inspect transitions under active output.
3. Compare the result with a written rubric and primary-source examples from
   polished TUIs such as Lazygit, K9s and Textual applications. Borrow principles
   of hierarchy, density and discoverability, not their workflows or styling
   indiscriminately. Do not claim an objective "world-class" certification.
4. Record specific defects, implement the corrections, rerun and inspect the
   same scenarios. Repeat until the acceptance gaps are resolved. Keep genuine
   before/after captures and a short rationale for meaningful design changes.
5. Repeat the visual pass on the packaged native executable and the required
   real-harness paths, distinguishing those receipts from fixture/replay results.

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

Cover loading/execution consent, running producer, noisy output, successful and
failed checks, retries, a branched DAG, native input handoff, completed evidence,
cancelled execution, diagnostics, light/dark/monochrome and reduced motion.
Do not claim terminal accessibility compliance from a contrast measurement alone.

Textual SVG exports and snapshot tests supplement the loop but do not replace
interactive terminal inspection. If real rendered-screen capture/inspection is
unavailable, report that exact limitation and leave visual acceptance open.
No fabricated screenshots or simulated claims of human usability testing.

### Milestone 1 evidence log (prototype, headless-supplemental pass)

Scope exercised: the real resolved graph for `examples/contracts/branched-demo`
(a synthetic fork/join factory added for this purpose), driven two ways:

- A labeled, non-authoritative fixture-replay driver (`src/apmx/tui/fixtures.py`)
  that posts the same `RunEvent`/status-change shapes a real engine would, so
  activity/check/retry/cancellation scenarios could be visually reviewed before
  milestone 3's live wiring exists. Every surface it drives is banner-marked
  "FIXTURE REPLAY — not a real run"; it never claims a captured outcome.
- Headless `Pilot`/`App.run_test()` + `export_screenshot()` -> SVG ->
  `rsvg-convert` -> PNG, inspected via the image viewer. Labeled throughout as
  "headless SVG-derived, supplemental", not a substitute for interactive PTY
  inspection.

A genuine interactive-terminal-rendering route was also attempted per the
mandate: a real PTY running the app, bridged over a loopback websocket to
xterm.js served in this session's `browser` canvas. The canvas opened and a
human viewing it would see a true terminal-emulator rendering (confirmed via
server access logs showing real page loads for `index.html`/`xterm.js` at
open time). However, self-capturing that canvas for automated/agent-side
review failed with a concrete, reproducible error: every `browser` canvas
read/screenshot action (`screenshot_page`, `read_page`, …) requires a
`page_id`, which in this toolset is only produced by an `open_browser_page`
tool that does not exist here. **Conclusion: the harness itself works and is
available for a human to use, but this agent cannot self-capture it in this
session.** This is reported as the exact gap, not papered over; the harness
(`termviz/` scratch dir, outside this repo) is reusable if a human wants to
drive a live visual pass.

Defects found and fixed from the headless pass (all three required rerunning
the same scenarios post-fix to confirm):

1. **Pending/running glyph collision.** Pending cards reused the same
   `STATUS_SYMBOLS["running"]` glyph (`[>]`) as actively-running cards,
   misrepresenting idle work as in progress (rubric: honest state). Fixed by
   giving `pending` its own `[ ]` glyph; `STATUS_SYMBOLS` itself (the
   canonical ASCII vocabulary shared with `ContractLogger`/plain output) was
   left untouched.
2. **Crash on focusing any card.** `CardSelected` was a plain frozen
   `@dataclass`, not a `textual.message.Message` subclass. It imported and
   type-checked fine, but `post_message()` raised `RuntimeError` the first
   time any card was actually focused or clicked — invisible until the app
   was genuinely run and interacted with rather than only import-checked.
   Fixed by making it a real `Message` subclass; regression-covered in
   `tests/unit/tui/test_app.py::test_focusing_a_card_selects_it_without_crashing`.
3. **Fixed-height panes starved the primary graph at 80x24.** With fixed
   `height: 8`/`height: 10` detail/activity panes, the minimum documented
   terminal size showed only 1 of 7 DAG cards with no clear scroll affordance
   (rubric: hierarchy and density). Fixed by switching to proportional `fr`
   units with `min-height` floors (`GraphView: 2fr`, others `1fr`), re-verified
   at 80x24 (3+ cards visible, full detail/activity panes, visible scrollbar)
   and 160x50 (full 7-card DAG, good density).

Not yet covered by this pass (left open, tracked for milestones 2-4): light/
dark/monochrome/`NO_COLOR` comparison, reduced-motion, a true consent/input-
handoff scenario (not implemented until live engine wiring exists), in-app
search, native packaging, and cross-harness/cross-platform validation. No
certification of "world-class" or accessibility compliance is claimed.

### Milestone 2 kickoff: real backend wiring and genuine headless-browser capture

1. **Backend-dependent test gap closed.** The full-suite failures seen during
   milestone 1 (82 failed / 38 errors) were re-investigated after a backend
   binary was located, not left as "out of scope." A provisioned, official
   APM 0.30.0 binary already exists read-only inside the installed demo kits
   (`apmx-demo`/`apmx-demo-next`, under `.demo/native/apmx-macos-arm64/libexec/apm/apm`,
   verified via `apm --version` → `0.30.0 (8c2e0d9)`). Setting
   `APMX_APM_BACKEND` to that absolute path (no copy, no reinstall, demo kits
   untouched) and rerunning the full suite dropped the failure count from
   82 failed/38 errors to **1 failed, 2092 passed, 57 skipped**. The one
   remaining failure
   (`tests/release/test_demo.py::test_python_command_preserves_virtual_environment_identity`)
   was isolated and confirmed unrelated to the APM backend or this track: the
   shared read-only `.venv`'s uv-managed CPython 3.12 interpreter copies (not
   symlinks) `python3` into child venvs without its paired
   `libpython3.12.dylib`, so the child venv's own `python3 -I` aborts
   (`SIGABRT`, `dyld: Library not loaded: @executable_path/../lib/libpython3.12.dylib`)
   — reproduced directly outside pytest's capture to confirm. This is a
   pre-existing toolchain defect in a component this track must treat as
   read-only; it is not fixed here, only diagnosed and reported.
2. **Genuine headless-browser screenshot capture achieved.** The app's
   `browser` canvas tool cannot self-capture (no `open_browser_page`/`page_id`
   support), but that only blocks the canvas *wrapper* — a local, scoped
   Playwright (installed as a devDependency of the scratch `termviz/`
   harness only, Chromium cached under that same directory via
   `PLAYWRIGHT_BROWSERS_PATH=0`, nothing global) was pointed directly at the
   already-running xterm.js/PTY-bridge localhost page
   (`http://127.0.0.1:8080/index.html`) and captured real PNG screenshots,
   inspected with `view`. This is honestly labeled **headless-browser
   terminal-emulator capture** — a real browser rendering real terminal
   output over a real PTY — not a substitute for human-interactive visual
   review, and not the app's own browser-canvas tool.
3. **Two real scratch-harness bugs found and fixed via this capture loop**
   (both in `termviz/` scratch tooling, not shipped code): (a) `pty_bridge.py`
   read the `cmd`/`cols`/`rows` query-string params without URL-decoding
   them, so the percent-encoded JSON command never parsed
   (`json.decoder.JSONDecodeError: Expecting value`) — fixed with
   `urllib.parse.unquote`; (b) `devrun.py`'s fixture-replay driver called
   `app.workers.wait_for_idle()`, which does not exist on this Textual
   version's `WorkerManager` (`AttributeError`) — fixed to
   `app.workers.wait_for_complete()`. Both bugs were only visible once a
   genuine browser captured the real rendered/crashed output; headless
   Pilot+SVG snapshots from milestone 1 could not have caught the
   query-string decoding bug since Pilot never goes through the websocket
   query string at all.
4. After both fixes, a genuine capture of the `branch+replay` fixture at
   1040x700 shows the fork/join DAG with live status coloring (green=passed
   border on `plan.contract.md`, orange=running on `design.contract.md`/
   `spec.contract.md`, default on untouched pending cards), the activity
   stream narration lines, and the footer bindings — matching the intended
   design. Scratch PTY/websocket/HTTP servers and the Playwright browser
   process are stopped after each capture session; no process or dependency
   from this is left running or installed outside the scratch `termviz/`
   directory.
5. **Native packaging feasibility: one target genuinely proven, not
   estimated.** The repo already declares `pyinstaller==6.16.0` under the
   `build` extra (`pyproject.toml`) and ships `build/apmx.spec` +
   `build/entrypoint.py`; `collect_submodules("apmx")` in the spec already
   picks up `apmx.tui` with no spec changes needed. Installed the declared
   extra into this track's own venv and ran
   `python -m PyInstaller --clean --noconfirm build/apmx.spec` for the
   current host target (`macos-arm64`, matching `scripts/release.py`'s
   `TARGETS`): build completed in **14.3s**, producing a **29 MB** `COLLECT`
   bundle (8.2 MB main executable). Measured cold-start (`apmx --version`,
   5 runs): **~0.09s real** each. Critically, launched the frozen binary's
   `--tui --plan` path inside a genuine `pty.fork()` child (not a plain
   pipe — `tui_eligible()` requires `sys.stdin.isatty()` and
   `sys.stdout.isatty()`, which a plain subprocess pipe fails) with a real
   `TERM=xterm-256color` set: the frozen executable emitted genuine Textual
   alternate-screen ANSI output (mouse-tracking enables, the "APMX factory
   (preview)" header, the real card DAG) — proof the Textual prototype runs
   correctly from a frozen interpreter, not just from source. (First attempt
   without a real `TERM` correctly hit the same `tui_unavailable` conservative
   refusal real terminals get over `TERM=dumb`/CI — confirms the guard
   works identically frozen or not.)
6. **Remaining four native targets (`linux-x86_64`, `linux-arm64`,
   `macos-x86_64`, `windows-x86_64`) were not built in this session —
   reported as a genuine blocker, not an assumed restriction.** PyInstaller
   does not cross-compile across OS/arch; the other 4 targets need either a
   matching OS/arch runner or a container. `docker` CLI is present
   (`docker --version` → 29.0.1), but `docker ps`/`docker info` fail with
   `failed to connect to the docker API ... no such file or directory`
   — the Docker Desktop daemon is not running on this host. Starting it was
   judged out of scope: it is a heavyweight shared-machine system service,
   not an isolated per-session dependency, and the task's constraints bar
   system-level changes. The project's own CI
   (`.github/workflows/native-notice-build.yml`, matrix from
   `scripts/release.py::TARGETS`) already builds all five targets on
   dedicated runners; this track did not duplicate that infrastructure
   locally. Net: 1 of 5 targets (`macos-arm64`) has genuine local build +
   measured-size/startup + genuine frozen-TUI-launch evidence from this
   session; the remaining 4 are deferred to that existing CI matrix, not
   silently assumed to work.
7. **Genuine stdin consent + real child-process cancellation: mechanism
   proven, not yet wired to live execution.** Added `apmx/tui/consent.py`
   (`ConsentScreen`, `CancellableChild`) plus 8 new tests
   (`tests/unit/tui/test_consent.py`), all passing against real behaviour:
   - `ConsentScreen` is a modal that focuses "No (default)" on mount; a bare
     Enter at mount, or Escape, declines (`False`) without ever touching the
     "Yes" path. Only an explicit Tab-to-accept-then-Enter (or a direct
     click on "Yes") returns `True`. Matches the existing CLI's default-No
     `--allow-host-access` gate — this module does not invent a second
     consent model, it is a front end a caller can wire to the one the
     engine/`ContractLogger` already own (`_admit()`,
     `advisory_consent_required`, `logger.confirm_factory()`); it does not
     import `apmx.contracts.chain`/`engine`/`process`/`records` itself
     (enforced by the existing `tests/unit/tui/test_architecture.py`
     import-boundary guard, which still passes against this new module).
   - `CancellableChild` spawns one real OS process in its own process group
     (`start_new_session=True`) and its `cancel()` sends a real `SIGTERM`
     to the group, awaits genuine reaping, and escalates to `SIGKILL` only
     if the child is still alive after a timeout. Proven against three real
     child processes, not mocks: a plain sleeping child (confirmed gone via
     `os.kill(pid, 0)` raising `ProcessLookupError` after cancel), a child
     that traps and ignores `SIGTERM` (confirmed the `SIGKILL` escalation
     path actually reaps it), and an already-exited child (confirmed
     `cancel()` is a safe no-op, not a hang or an error).
   - **Not yet done, and intentionally deferred**: wiring either piece into
     `FactoryApp`'s live rendering path. `--tui` today is reachable only
     from the `--plan` (preview, no execution) branch of
     `invoke_contract()` (`src/apmx/commands/contracts.py`); the real
     consent gate and the real supervised-process execution
     (`src/apmx/contracts/process.py::supervise_process`) only run in the
     separate non-planning execution branch today, with no code path that
     reaches both `--tui` and live execution at once. Making that
     connection is an engine-facing architecture decision (does `--tui`
     gain a live-execution mode, and if so how does it call
     `logger.confirm_factory()`/the engine's existing admit/execute flow
     instead of this module reimplementing it) — tracked as the opening
     item for milestone 3, not resolved unilaterally here, per "Canonical
     engine and ContractLogger own semantics."


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
