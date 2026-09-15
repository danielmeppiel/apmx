# Native factory replay

These sanitized terminal replays show one actual macOS ARM64 source run on
2026-09-15, plus separate deterministic checker replays. The executed source was
[`be9c5be19f39284fa5f7b6416de6c37764184f2c`](https://github.com/danielmeppiel/apmx/commit/be9c5be19f39284fa5f7b6416de6c37764184f2c),
not a later documentation/media commit or a published v0.3.0 binary.

Download an HTML file and open it locally in a browser; GitHub's code viewer
does not execute HTML. Each file is self-contained, with no external player,
assets or network requests. The matching `.cast` files contain asciicast v2
terminal events, not video.

| Replay | Files | Actual recording / playback |
| --- | --- | --- |
| Four-contract native run | [HTML](apmx-native-factory.html) / [.cast](apmx-native-factory.cast) | 97.030 seconds / 26.266 seconds, edited to 3x speed with gaps capped at 1.5 seconds |
| Independent checker replays | [HTML](apmx-checker-replays.html) / [.cast](apmx-checker-replays.cast) | 0.611 seconds / 0.606 seconds, 1x captured output |

Paths and email addresses are redacted, and output is grouped into lines.
Playback timing is edited; it is not a real-time performance claim.

## What this demonstrates

The fresh environment used the documented
[approved, hash-verified managed installation](../install.md#managed-environment-installation-optional),
genuine APM 0.30.0 and native Copilot with its existing configured model.
The run completed **four contracts, six checks and five artifacts**, remaining
**UNPROVEN (exit 21)**. Both independent positive checker replays returned `0`.

After the successful run, a separate copy of the actual patch changed `>= 5000`
to `> 5000`. Both original checkers rejected that defect with exit `1`; all
14 required cases ran and the exact 5000-cent threshold failed. This was a
deliberate negative control, not a failed model run or automatic repair.
The original 22 caller files, 196 source files and retained evidence stayed
unchanged.

[Proof metadata](proof-metadata.json) records preparation-time provenance,
environment, artifact and recording hashes, and positive/negative results.
Raw transcripts, execution records and installation configuration are not
included.

This is one macOS ARM64 observation, not Windows/Linux live proof or proof of
the default direct-download route. Local execution is not a sandbox or
production certification and has no hard model-spend cap. Cleanup checks cover
recorded process groups, not unobserved escaped descendants. Passing checks
do not authorize a merge, deployment or release.

[Run the factory yourself](../../examples/contracts/software-factory/README.md#set-up).
