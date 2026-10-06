"""Synthetic success fixtures; error shape verified with real OpenCode 1.2.24."""

import json
from dataclasses import replace

import pytest

from apmx.contracts.events import EventEmitter
from apmx.contracts.models import ContractLimits
from apmx.contracts.stream import safe_text
from apmx.runtime.opencode_stream import OpenCodeStreamDecoder

pytestmark = pytest.mark.unit


def frame(kind, *, session="fixture-session", **fields):
    part = {
        "id": f"part-{kind}",
        "sessionID": session,
        "messageID": "fixture-message",
        "type": {
            "step_start": "step-start",
            "step_finish": "step-finish",
            "tool_use": "tool",
        }.get(kind, kind),
        **fields,
    }
    return (
        json.dumps({"type": kind, "sessionID": session, "timestamp": 1, "part": part}) + "\n"
    ).encode()


def decoder(**kwargs):
    events = []
    return OpenCodeStreamDecoder(EventEmitter("fixture-run", events.append), **kwargs), events


def complete(stream):
    stream.feed("stdout", frame("step_start"))
    stream.feed("stdout", frame("text", text="Delivered."))
    stream.feed("stdout", frame("step_finish", reason="stop"))


def test_final_stop_is_observed_but_does_not_invent_native_exit_or_model():
    stream, events = decoder()
    wire = frame("step_start") + frame("text", text="Done.") + frame("step_finish", reason="stop")
    for byte in wire:
        stream.feed("stdout", bytes([byte]))
    assert not stream.completion_seen
    stream.finish()
    assert stream.completion_seen and stream.protocol_error is None
    assert stream.native_exit_code is None and stream.observed_models == ()
    assert "Done." in str(events)
    assert "fixture-session" not in str(events)
    count = len(events)
    stream.finish()
    assert len(events) == count


@pytest.mark.parametrize("reason", ["tool-calls", "length", "error", "unknown", None, True])
def test_intermediate_or_abnormal_step_is_not_invocation_completion(reason):
    stream, _ = decoder()
    stream.feed("stdout", frame("step_start"))
    stream.feed("stdout", frame("step_finish", reason=reason))
    stream.finish()
    assert not stream.completion_seen


def test_new_unfinished_step_invalidates_a_previous_stop():
    stream, _ = decoder()
    complete(stream)
    stream.feed("stdout", frame("step_start", id="second-start", messageID="second-message"))
    stream.finish()
    assert not stream.completion_seen


def test_error_observation_outranks_a_terminal_step_and_an_os_zero():
    stream, events = decoder()
    complete(stream)
    # The real default-model rejection exited zero; transport status is not acceptance.
    stream.feed(
        "stdout",
        (
            json.dumps(
                {
                    "type": "error",
                    "sessionID": "fixture-session",
                    "timestamp": 1,
                    "error": {
                        "name": "APIError",
                        "data": {
                            "message": "The requested model is not available for integrator opencode.",
                            "statusCode": 400,
                            "isRetryable": False,
                            "responseHeaders": {"ignored": "PRIVATE_HEADER"},
                            "responseBody": "PRIVATE_BODY",
                        },
                    },
                }
            )
            + "\n"
        ).encode(),
    )
    stream.finish()
    assert stream.protocol_error and not stream.completion_seen
    assert "not available" in str(events) and "PRIVATE" not in str(events)


@pytest.mark.parametrize("status", ["error", "running", "pending", None, True])
def test_noncompleted_tools_never_prove_completion(status):
    stream, _ = decoder()
    stream.feed(
        "stdout",
        frame("tool_use", tool="apmx_artifacts_write_file", state={"status": status}),
    )
    complete(stream)
    stream.finish()
    assert stream.protocol_error and not stream.completion_seen


def test_native_skill_completion_exposes_only_the_valid_skill_name():
    stream, events = decoder()
    stream.feed(
        "stdout",
        frame(
            "tool_use",
            tool="skill",
            state={
                "status": "completed",
                "input": {"name": "python-testing-patterns", "extra": "PRIVATE_INPUT"},
                "output": "PRIVATE_SKILL_CONTENT",
                "metadata": {"private": "PRIVATE_METADATA"},
            },
        ),
    )
    assert [event.data["name"] for event in events if event.kind == "skill_loaded"] == [
        "python-testing-patterns"
    ]
    assert "PRIVATE" not in str(events)


def test_reasoning_and_unknown_payloads_are_never_published():
    stream, events = decoder()
    stream.feed("stdout", frame("reasoning", text="PRIVATE_REASONING"))
    stream.feed("stdout", frame("future_event", content="PRIVATE_EXTRA"))
    complete(stream)
    stream.finish()
    assert stream.completion_seen and "PRIVATE" not in str(events)


@pytest.mark.parametrize(
    "wire",
    [
        b"not json\n",
        b"[]\n",
        b'{"type":"text"}\n',
        b'{"type":"text","sessionID":"fixture-session","part":null}\n',
        frame("text", text=False),
        frame("step_finish", reason="stop"),
    ],
)
def test_malformed_observations_cannot_be_repaired_by_a_later_stop(wire):
    stream, _ = decoder()
    stream.feed("stdout", wire)
    complete(stream)
    stream.finish()
    assert stream.protocol_error and not stream.completion_seen


def test_mixed_native_sessions_fail_closed():
    stream, _ = decoder()
    stream.feed("stdout", frame("step_start"))
    stream.feed("stdout", frame("step_finish", session="other-session", reason="stop"))
    stream.finish()
    assert stream.protocol_error and not stream.completion_seen


def test_oversized_json_is_drained_without_exposing_its_prefix():
    stream, events = decoder(limits=replace(ContractLimits(), frame_bytes=512))
    stream.feed("stdout", frame("text", text="PRIVATE" * 1024))
    complete(stream)
    stream.finish()
    assert stream.protocol_error and not stream.completion_seen
    assert "PRIVATE" not in str(events)


def test_error_text_uses_shared_redaction_before_rendering():
    stream, events = decoder()
    stream.feed(
        "stdout",
        (
            json.dumps(
                {
                    "type": "error",
                    "sessionID": "fixture-session",
                    "error": {
                        "name": "APIError",
                        "data": {"message": "https://user:PRIVATE_PASSWORD@example.test/error"},
                    },
                }
            )
            + "\n"
        ).encode(),
    )
    displayed = "\n".join(safe_text(str(event.data)) for event in events)
    assert "PRIVATE_PASSWORD" not in displayed
