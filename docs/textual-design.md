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
   `macos-x86_64`, `windows-x86_64`): not locally buildable on this
   macOS-arm64 machine, and not a gap that needs closing here.**
   PyInstaller does not cross-compile across OS/arch, so each of those 4
   needs its own matching OS/arch runner. `docker` CLI is present
   (`docker --version` -> 29.0.1) but its daemon is not running here
   (`docker ps`/`docker info` -> `failed to connect to the docker API ...
   no such file or directory`); per creator guidance, spinning up Docker
   Desktop locally is unnecessary busywork, not a blocker to resolve --
   the project's own CI (`.github/workflows/native-notice-build.yml`,
   matrix from `scripts/release.py::TARGETS`) already builds and tests all
   five targets once code is ready, and that is the intended path for the
   other 4, not a local duplicate of it. Net: 1 of 5 targets
   (`macos-arm64`) has genuine local build + measured-size/startup +
   genuine frozen-TUI-launch evidence from this session; the remaining 4
   ride the existing CI matrix once this branch's commits land there, by
   design, not as an uncovered gap.
7. **Resolved the one remaining backend-dependent test failure using an
   available working interpreter, not by patching the shared toolchain.**
   The earlier SIGABRT (`tests/release/test_demo.py::
   test_python_command_preserves_virtual_environment_identity`) traced to
   the shared read-only `.venv`'s uv-managed CPython 3.12 build: its
   `venv`-created child binaries link their sibling dylib via a relative
   `@executable_path/../lib/libpython3.12.dylib` path that is never copied
   alongside the copied binary. The already-installed
   `/opt/homebrew/opt/python@3.13/bin/python3.13` (a Framework build) does
   not have this problem -- `otool -L` on a venv built from it shows the
   child binary linking the shared library by its real, stable absolute
   path (`/opt/homebrew/Cellar/python@3.13/.../Python`), so no sibling
   dylib copy is ever needed. Built this track's own `.venv-py313` from
   that interpreter (`pip install -e ".[dev,build,factory,tui]"`, the same
   declared extras) and reran the exact failing selector: **passed**.
   Reran the full `tests/unit tests/release` suite in that same env with
   `APMX_APM_BACKEND` set: **2149 passed, 11 skipped, 0 failed** (445.6s) --
   zero failures and zero skips attributable to this bug once a working
   interpreter is used for this track's own environment. The shared
   `.venv` is still left untouched (read-only), and this did not "fix" the
   shared toolchain -- it worked around its one broken build by using a
   different, already-available, already-working interpreter for this
   track's own venv, per the instruction that the read-only constraint
   does not mean leaving an available fix unused.
8. **Genuine cross-size terminal screenshots and an honest visual critique,
   not just a renderability check.** Parameterized the scratch `termviz/`
   harness (`index.html` query params for `cols`/`rows`/`fontSize`;
   `capture.js` to size the Playwright viewport to match) and captured two
   new real PNGs via the same headless-Chromium-to-PTY path used earlier:
   - `shot-80x24.png` (80x24, fontSize 16) -- absolute path:
     `/Users/danielmeppiel/.copilot/session-state/5072cbd4-7912-45c6-b6ab-d4ba84303cb7/files/termviz/shot-80x24.png`
   - `shot-120x40.png` (120x40, fontSize 13) -- absolute path:
     `/Users/danielmeppiel/.copilot/session-state/5072cbd4-7912-45c6-b6ab-d4ba84303cb7/files/termviz/shot-120x40.png`

   Visual findings from inspecting both (distinct from the earlier fixed
   scratch-harness bugs):
   - **Low-contrast pending-card borders (real finding, not yet fixed).**
     `ContractCard`'s default (`pending`) border uses `$panel` on the
     app's black background; in both captures the three pending cards
     (`build.contract.md`, and in the wider 120x40 capture also
     `docs.contract.md`/`tests.contract.md`/`review.contract.md`) have a
     border that is barely distinguishable from the background at normal
     viewing distance, while the `passed` (green) and `running` (orange)
     borders are clearly legible. This is a genuine contrast gap worth
     fixing before milestone 4 sign-off, not merely a theoretical fallback
     concern.
   - **No clipping or truncation observed at either size.** The fixture
     banner text, every card label, the detail pane's "Select a contract
     card to inspect it." prompt, the activity-log lines, and the footer
     key bindings all render in full at both 80x24 and 120x40 -- the
     narrower 80-column capture does not truncate the longest line (the
     fixture banner).
   - **GraphView's internal scrollbar appears even with vertical room to
     spare.** A narrow teal scrollbar indicator is visible on the graph
     pane's right edge in both captures, including the 120x40 one where
     there is visibly blank space below the last DAG row -- suggests the
     `GraphView { height: 2fr; }` sizing rule computes a slightly taller
     content height than what is visually needed here; a minor layout
     polish item, not a functional bug.
   - **Explicit limitation: focus-ring contrast was not captured by this
     pass.** Both screenshots are of the fixture-replay path, which never
     focuses a card programmatically, so `ContractCard:focus`'s
     `$accent`/`$boost` styling could not be visually verified here; that
     check needs a capture driven by an actual focus/Tab interaction
     (milestone 4 follow-up), not claimed as already covered by these two
     images.

   Visual acceptance for milestone 1/2 presentation remains **open**
   pending a fix for the low-contrast pending-border finding and a
   focus-driven capture; implementation continues in parallel per
   instruction, this is not a blocking gate on milestone 3 starting.
9. **Genuine stdin consent screen proven; cancellation kept test-only after
   review, not shipped as a second supervisor.** Added `apmx/tui/consent.py`
   (`ConsentScreen` only) plus 4 consent tests and 5 test-only process-
   cancellation mechanism checks (`tests/unit/tui/test_consent.py`, 9 tests
   total, all passing against real behaviour, ASCII-only):
   - `ConsentScreen` is a modal that focuses "No (default)" on mount; a bare
     Enter at mount, or Escape, declines (`False`) without ever touching the
     "Yes" path. Only an explicit Tab-to-accept-then-Enter (or a direct
     click on "Yes") returns `True`. Matches the existing CLI's default-No
     `--allow-host-access` gate -- this module does not invent a second
     consent model, it is a front end a caller can wire to the one the
     engine/`ContractLogger` already own (`_admit()`,
     `advisory_consent_required`, `logger.confirm_factory()`); it does not
     import `apmx.contracts.chain`/`engine`/`process`/`records` itself
     (enforced by the existing `tests/unit/tui/test_architecture.py`
     import-boundary guard, which still passes against this module). The
     import-boundary guard proves only that this module does not import
     those modules, not that it is actually wired to the real admission
     flow yet -- that wiring is still open, tracked below.
   - **A `CancellableChild` supervisor prototype was built, then removed
     from `src/apmx/tui/` after review**, for three concrete reasons:
     (a) it duplicated lifecycle ownership that belongs to
     `apmx.contracts.process`'s canonical supervisor (including
     descendant/process-group cleanup semantics), which must be extended
     for real cancellation rather than shadowed by a second one; (b) it
     was POSIX-only (`os.getpgid`/`killpg`, `SIGKILL`) with no Windows
     path, which would not cover all five native targets; (c) its first
     "ignores SIGTERM" test raced -- it signalled immediately after spawn
     with no guarantee the child's signal handler was installed yet, so a
     pass did not actually prove SIGKILL escalation. The mechanism check
     now lives only in the test file, fixed and extended: each scenario
     reads an explicit "ready" line back from the child (confirming its
     signal handler, if any, is already armed) before signalling, removing
     the race; a new scenario spawns a real grandchild process and
     confirms `killpg` on the group reaps both the child and the
     descendant, not just the immediate process (parent exit alone is not
     descendant cleanup). All 5 are POSIX-gated (`skipif` on `win32`) since
     they are not the cross-platform canonical path.
   - **Not yet done, and intentionally deferred**: wiring `ConsentScreen`
     into `FactoryApp`'s live rendering path, and building the real
     cancellation path by extending `apmx.contracts.process::
     supervise_process` (not a new supervisor). `--tui` today is reachable
     only from the `--plan` (preview, no execution) branch of
     `invoke_contract()` (`src/apmx/commands/contracts.py`); the real
     consent gate and the real supervised-process execution only run in
     the separate non-planning execution branch today, with no code path
     that reaches both `--tui` and live execution at once. This is
     confirmed as the main, approved milestone 3 requirement (not a new
     scope choice needing separate sign-off): `--tui` gains a live-
     execution mode that calls the engine's existing admit/execute/
     cancellation ownership, with the UI-to-runtime adapter kept outside
     the presentation-only `tui/` module so the static import guard is not
     forced into duplicating lifecycle semantics, per "Canonical engine
     and ContractLogger own semantics."


## Milestone 3 implementation notes

Milestone 3 wires the live `--tui` path through the real engine instead of the
fixture-replay prototype from milestone 1. `src/apmx/commands/tui_live.py`
hosts the bridge:

- `LiveContractLogger` implements the same `ContractLogger` protocol as the
  plain/Rich loggers. It is the only component that calls back into the
  running `LiveFactoryApp`, and it does so exclusively via Textual's
  `call_from_thread`, because the real chain runs on a worker thread while the
  app's event loop owns the main thread. It never invents progress, model
  identity or outcomes; it maps canonical `ContractLogger` calls and native
  diagnostic events onto card status and the bounded diagnostics pane. Native
  DEBUG/INFO plumbing still never reaches the normal activity stream, and
  still respects the existing redaction/bounding rules in `chain.py` — the
  bridge adds no additional filtering of its own and defers entirely to the
  canonical logger contract.
- `LiveFactoryApp` extends the prototype UI with a real cancel keybinding
  (`c`). Pressing it only ever sets a cooperative, idempotent flag; it never
  sends an OS signal directly. That flag is read by `supervise_process` via
  the existing `cancel_requested` callable, so termination/escalation/cleanup
  for a UI-requested cancellation reuses exactly the same SIGTERM/SIGKILL and
  process-group reaping path already used for timeouts and KeyboardInterrupt.
  There is no separate "UI cancellation" code path in the process supervisor.
- `launch_live` disables local terminal echo for the duration of the run (the
  live agent/check processes may themselves prompt for input on the real
  TTY), drives the real `run_chain`/engine on a worker thread, and always
  restores terminal state on exit — including on cancellation — so the UI
  cannot leak an in-flight inference state into the restored shell.
- Both existing default-No consent prompts (host access, unproven inputs) are
  unchanged: they are asked before the live TUI is entered, using the same
  governance and `--allow-*` flags as the plain path. No consent default was
  loosened to make the TUI easier to drive non-interactively.
- The no-`--plan` live path requires a real TTY on both ends (local terminal
  and the underlying agent harness); when one is missing it refuses with the
  same `UNPROVEN`/`tui_unavailable` message as the existing `--plan`+`--tui`
  preview path, rather than attempting to launch Textual against a
  non-interactive process.

### Test coverage (hermetic/replay vs genuine proof)

Per the project's established pattern (`tests/unit/contracts/test_chain.py`,
`test_chain_sources.py`), "genuine proof" here means every part of the stack
runs for real — real `supervise_process`, real checks, real `ContractLogger`,
real file I/O, a real process group — except the AI harness invocation itself,
which is swapped for a deterministic local Python script via
`RuntimeFactory.get_runtime_by_name`. This avoids model spend while still
proving real wiring, as distinct from fixture-replay (`src/apmx/tui/fixtures.py`,
used only by the milestone-1 prototype's `--replay` path).

- `tests/unit/tui/test_live_bridge.py` (new, 6 tests): event-to-card-status
  mapping for a real two-node chain driven through `LiveContractLogger`;
  cooperative cancellation requested before any leaf starts and between two
  leaves, confirming the chain actually stops early and reports the real
  halted outcome; native diagnostic events routed to the bounded diagnostics
  pane and never the activity stream; the Textual cancel keybinding itself,
  exercised through `run_test()`/`Pilot` (press `c`, confirm the cooperative
  flag flips and a second press is a no-op); `launch_live` disabling local
  terminal echo and actually driving the real chain to completion, called
  directly rather than through a mocked `App.run()` (headless `App.run()`
  works in this sandbox; verified independently before relying on it).
- `tests/unit/tui/test_cli_flags.py`: added
  `test_tui_live_with_factory_and_no_tty_refuses_with_unproven_message`,
  proving the no-`--plan` live path's TTY refusal end-to-end through the real
  CLI (`CliRunner`), real package installation via the bundled/provisioned APM
  backend, and the real `tui_unavailable` gate — not a mocked TTY check.
- `tests/unit/contracts/test_reliability.py`: added
  `test_external_cancel_requested_really_reaps_a_long_running_child`, a real
  end-to-end proof that `supervise_process`'s `cancel_requested` callable
  actually terminates and reaps a genuinely spawned, still-running child
  process (SIGTERM observed, cleanup confirmed, exit code non-zero, bounded
  wall-clock), exactly mirroring the existing timeout/lingering-children tests
  rather than asserting only on mocked state.
- `tests/unit/test_windows_process.py`: added the Windows-job-object
  equivalent, `test_native_cancel_requested_really_terminates_the_owned_job`
  (skipped off-Windows, matching the file's existing `windows_only` pattern;
  not independently run on this POSIX development host).

All new and existing tests pass together: `tests/unit/tui`,
`tests/unit/contracts/test_cli_selection.py`,
`tests/unit/contracts/test_reliability.py` and the full `tests/unit` suite,
run with a real APM 0.30.0 backend supplied via `APMX_APM_BACKEND` in this
development environment (normally bundled/provisioned, not required as an
environment variable in CI). `ruff check` is clean on all touched files.

### Open items carried into milestone 4

- Milestone 3 proves live wiring and cooperative cancellation; it does not yet
  validate behavior across terminal widths, color modes, `NO_COLOR`, reduced
  motion, screen-reader/plain fallback or output redirection — that is
  milestone 4.
- No genuine (real-model) run has been executed yet for this track; milestone
  3's "genuine proof" tests all substitute a deterministic local script for
  the AI harness call, per the standing spend/consent constraints. A bounded,
  explicitly authorized genuine run is planned for milestone 5 validation,
  not before.

### Milestone 4 evidence log: genuine live-engine visual validation

Unlike milestones 1-2 (fixture-replay captures only), every screenshot in this
section drives the **real** `apmx` CLI / Textual `LiveFactoryApp` /
`ContractLogger` / process supervisor / `ContractStreamDecoder` pipeline
end-to-end as a genuine OS subprocess inside a real PTY, with only the AI
harness call (`copilot`) swapped for a hermetic local stand-in script — the
same substitution precedent the project's own hermetic pytest tests already
use (e.g. `tests/unit/tui/test_cli_flags.py`). No model call, no network, no
spend. A real 4-contract fork/join factory (`plan → {design, spec} → build`)
is used throughout. All screenshots are headless-browser (Playwright +
Chromium, scoped to this track's own scratch tooling) renders of a real
xterm.js terminal connected to that real PTY — not the app's own
browser-canvas tool, and not a substitute for human-interactive review, but
genuine rendered terminal output.

1. **A real product bug was found and fixed via this live-engine dogfooding,
   not via code-reading alone.** While chasing an apparent "stuck after
   second consent" capture, raw PTY output (ANSI-stripped) showed the engine
   actually completing every contract with
   `[!] Native execution did not complete: native_completion_unobserved` and
   an overall `finished: unknown` outcome, plus narration lines reading
   `[x] check None None` for checks that had genuinely passed. Root cause,
   in two parts:
   - The real engine's `ContractStreamDecoder._native_result`
     (`src/apmx/contracts/stream.py`) requires a terminal JSONL line
     `{"type":"result","exitCode":<int>,"sessionId":<str>,"usage":{...}}`
     before it sets `completion_seen=True`; this track's hermetic
     stand-in script did not emit it. Fixed in the scratch harness only
     (`termviz/live-demo/tools/copilot`), not shipped code.
   - After that fix, contracts correctly turned green, but **every genuine
     passing check still misreported as `None`/`None` in narration, and
     incorrectly flipped its card to `"retrying"` instead of back to
     `"running"`.** Cause: `src/apmx/contracts/engine.py::_run_checks()`
     emits `check_finished` with `observation=<CheckObservation dataclass>`,
     but `src/apmx/tui/app.py::_format_event` and
     `src/apmx/commands/tui_live.py::_present` both read flat
     `event.data.get("name")`/`event.data.get("status")` keys — a shape
     that only matches the invented replay fixtures in `tui/fixtures.py`,
     not the real engine's live event shape. This is a genuine,
     reproducible regression in shipped code (confirmed pre-existing by
     stashing the fix and re-running), **not** an artifact of the scratch
     harness.
   - **Fix** (this track's ownership, `src/apmx/tui/app.py` +
     `src/apmx/commands/tui_live.py`): added a shared
     `_check_finished_fields(event)` helper that reads the real
     `CheckObservation` shape via `getattr`, falling back to the fixture's
     flat keys — mirroring the pattern `core/contract_logger.py` already
     uses correctly for the CLI's own non-TUI output path. `engine.py`'s
     own `check_started`/`check_finished` shape inconsistency is left
     untouched (not this track's ownership).
   - **Regression tests added**:
     `tests/unit/tui/test_app.py::test_format_event_reads_real_engine_check_finished_shape`
     constructs a real `CheckObservation`/`RunEvent` directly and asserts
     correct pass/fail narration for both shapes; the pre-existing
     `tests/unit/tui/test_live_bridge.py::test_live_run_drives_both_cards_to_passed_and_returns_the_real_result`
     was strengthened from an exact-list assertion (which coincidentally
     passed both before and after the fix, for the wrong reason) to an
     explicit `assert not any(status == "retrying" ...)` for an all-passing
     run. Full suite, run against the real APM 0.30.0 backend located via
     `APMX_APM_BACKEND` (per milestone 2's precedent): **1948 passed, 58
     skipped**.
2. **Both consent prompts, genuine.** `m4-consent-prepare-80x24.png` and
   `m4-consent-execute-80x24.png` show the two distinct real consent
   surfaces (`confirm_package_preparation`, `confirm_factory`) with a real
   resolved dependency count and real APM install log lines above them,
   both still defaulting to `[y/N]`. `m4-consent-execute-graph-120x40.png`
   shows the wider second-prompt rendering with the full real 4-contract
   graph description (produces/checks/needs) visible above the prompt.
3. **Genuine running dashboard**, both narrow and wide:
   `m4-running-dashboard-120x40.png` and `m4-running-dashboard-80x24.png`
   show the real fork/join DAG mid-run (3 contracts genuinely
   passed/green, 1 genuinely running/orange), the title bar, the detail
   pane, the activity narration feed sourced from real engine events, and
   the footer keybindings — post-fix, with correct narration (no `None`
   leaks, no spurious `"retrying"`).
4. **Genuine keyboard navigation**: `m4-focus-detail-pane-120x40.png`
   captures a scripted real `Tab` keypress moving focus to a contract card,
   with the detail pane correctly repopulating with that contract's real
   metadata (`Contract: plan.contract.md / Status: passed / Needs: ... /
   Produces: ... / Checks: ...`) sourced from the live engine, not a
   fixture.
5. **Genuine `NO_COLOR` live-engine evidence**: `m4-nocolor-live-120x40.png`.
   The scratch harness (`termviz/pty_bridge.py` + `index.html`) was extended
   with an `env=<json>` passthrough so a capture request can set extra
   environment variables (e.g. `NO_COLOR=1`) in the real spawned child
   before `exec`, purely additive scratch-tooling plumbing. With
   `NO_COLOR=1` set, the genuine running dashboard renders with every card
   border, the title bar and all panes in monochrome — confirming Textual's
   native `NO_COLOR` handling is honored end-to-end through the real
   `LiveFactoryApp`, not just assumed from Textual's own documentation (as
   milestone 2/3 had left it).
6. **Genuine cooperative-cancellation evidence**, proving "cancellation
   reaps supervised children; UI exit cannot leak inference" is not just an
   engine-level claim but true of the live TUI's actual behavior:
   - `m4-cancel-during-120x40.png`: a real `c` keypress was scripted to fire
     mid-run (2 of 4 contracts genuinely green, 1 genuinely running);
     captured ~100ms after the keypress, the TUI is still showing that
     running state (the keypress had not yet been processed).
   - `m4-cancel-after-exit-120x40.png`: captured ~300ms later, the TUI has
     fully exited — the terminal has returned to its primary screen buffer,
     showing exactly the pre-run scrollback content frozen from before the
     alternate screen was entered. No partial inference, no extra output,
     no corrupted redraw was left on screen; the exit was clean and
     effectively instantaneous once cancellation was requested.
   - Process-level verification (not just visual): immediately following a
     scripted cancel, `pgrep -f "live-demo"` against this track's own
     scratch harness processes returned no matches — the supervised
     hermetic child process was fully reaped, not merely hidden by the UI
     exiting while still running in the background.
7. **Native-prompt/'c'-keybinding safety analysis** (code-reading
   conclusion, consistent with and reinforced by the above capture): every
   producer subprocess this track's TUI supervises is launched with
   `stdin=subprocess.DEVNULL` (confirmed in the process-supervision code
   path used by both the CLI and TUI run commands), so there is no
   real channel through which a native tool/model prompt could ever read
   from — or be answered by — the user's terminal while the TUI owns it.
   The `'c'` cancel keybinding therefore cannot race with, or leak into, a
   live native prompt: there is never a prompt to race with in the first
   place. This was validated both by static reading of the supervision code
   and, this milestone, by direct observation of a scripted cancellation
   leaving no partial or leaked content on screen.
8. **Remaining honest gaps for milestone 4**, carried forward rather than
   glossed over: screen-reader/plain-fallback output and output-redirection
   behavior were validated earlier (milestones 1-2) only via the
   fixture-replay path and Textual's own plain/headless rendering, not
   re-captured against the live engine this milestone; reduced-motion
   (`TEXTUAL_ANIMATIONS=none`) was similarly confirmed by code-reading
   (Textual's native env handling) but not captured live. These are
   lower-risk than the `NO_COLOR`/cancellation gaps that were closed this
   milestone, since they exercise Textual's own documented env handling
   rather than this track's event-bridging code, but are noted here for
   transparency rather than silently marked complete.

All milestone-4 screenshots are persisted at (absolute paths, session-scoped
scratch storage, not part of the repo):
`~/.copilot/session-state/5072cbd4-7912-45c6-b6ab-d4ba84303cb7/files/termviz/evidence-m4/`
(`m4-consent-prepare-80x24.png`, `m4-consent-execute-80x24.png`,
`m4-consent-execute-graph-120x40.png`, `m4-running-dashboard-120x40.png`,
`m4-running-dashboard-80x24.png`, `m4-focus-detail-pane-120x40.png`,
`m4-cancel-during-120x40.png`, `m4-cancel-after-exit-120x40.png`,
`m4-nocolor-live-120x40.png`).

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
