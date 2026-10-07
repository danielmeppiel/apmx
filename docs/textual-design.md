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
