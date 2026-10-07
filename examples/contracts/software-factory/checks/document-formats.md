# Checkout factory document formats

These formats belong to this example, not to APMX generally. They check
structure and reference consistency, not whether every sentence is true.
The request and supplied behavioral cases remain authoritative.

## Common report shape

Every produced report starts with exactly one fenced `json` metadata object,
before its Markdown sections. Use schema `software-factory-document/2` and the
appropriate `document` value. Required H2 sections appear once and contain
nonempty content. Reports are ASCII and at most 16 KiB. Do not add metadata
keys, duplicate JSON keys or additional JSON fences.

Read only the stage-specific format below. Prose wording is not prescribed.

## Planning

Use this metadata shape. `acceptance` must contain every `REQUIRED` ID from
`checks/software_factory_cases.py` exactly once; the list below shows the
current supplied inventory. `targets` distinguish captured existing files from
the proposed new regression test.

```json
{
  "schema": "software-factory-document/2",
  "document": "planning",
  "acceptance": [
    "below", "Free delivery at 5000 cents", "above", "zero", "large",
    "negative", "negative-large", "true", "false", "float", "string",
    "null", "list", "object"
  ],
  "targets": [
    {"path": "src/pricing.py", "state": "existing"},
    {"path": "src/checkout.py", "state": "existing"},
    {"path": "tests/test_free_shipping.py", "state": "new"},
    {"path": "docs/checkout.md", "state": "existing"}
  ]
}
```

Required H2 sections: Goal, Changes, Validation, Risks.

## Specification

Use the same complete acceptance list as Planning. Resolve public function
references against captured source; the checker inspects syntax without
importing the application.

```json
{
  "schema": "software-factory-document/2",
  "document": "specification",
  "acceptance": [
    "below", "Free delivery at 5000 cents", "above", "zero", "large",
    "negative", "negative-large", "true", "false", "float", "string",
    "null", "list", "object"
  ],
  "interfaces": ["src/pricing.py:delivery_fee", "src/checkout.py:checkout"]
}
```

Required H2 sections: Behavior, Interface, Acceptance.

## Implementation report

```json
{"schema":"software-factory-document/2","document":"implementation"}
```

Required H2 sections: Changes, Validation, Limitations.

## Documentation report

```json
{"schema":"software-factory-document/2","document":"documentation"}
```

Required H2 sections: Changes, Validation, Limitations.

## Advisory review

```json
{"schema":"software-factory-document/2","document":"review","findings":[]}
```

Required H2 sections: Assessment, Findings, Limitations.

Zero findings is valid. For an actual finding, replace the empty list with
objects containing exactly `artifact`, `line` and `detail`. `artifact` names
one of `request.md`, `specification.md`, `changes.diff`, `implementation.md`,
`documentation.diff` or `documentation.md`; `line` is an existing 1-based line
in that artifact, not a line in a reconstructed source file. `detail` is
nonempty and at most 800 characters. At most 20 findings are accepted.
Findings and prose are advice, never an acceptance verdict.

## Project page

The real `docs/checkout.md` has required H2 sections Delivery policy, Examples
and Input errors. It is not a report and does not use the JSON metadata above.
In Examples, use exactly these Markdown table columns:

```text
| Case | Subtotal | Delivery | Total | Error |
| --- | --- | --- | --- | --- |
```

Add every canonical case from `checks/software_factory_cases.py` exactly once.
Write inputs and successful numeric results as JSON literals, preserving
types: `true` is not `1`, and `"5000"` is not `5000`. For successful rows,
Error is `-`. For rejected inputs, Delivery and Total are `-` and Error is
the canonical exception name. Do not wrap individual cells in backticks.

Use inline local file links and H2 fragments, for example
`[pricing](../src/pricing.py)` and `[examples](#examples)`. No query strings
or reference-style links. Local targets must be present in the combined
candidate. Absolute HTTP(S) links are allowed but are not fetched or verified.
Do not depend on executing fenced code: the checker never runs it.

The checker compares the complete declared table with the fixed cases and
evaluates those same inputs through the candidate APIs. Another check reruns
both supplied Gherkin and regression suites on the code-plus-docs tree.
These finite checks do not prove that all prose is semantically correct.
