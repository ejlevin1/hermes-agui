"""Translator rules: frames are the shapes captured in S7 (evidence/q4_*.sse), synthetic content only."""

import json

from ag_ui.core import EventType as T

from hermes_agui.translate import FrontendCall, Translator

RID = "run_x"


def d(delta, seq=0):
    return {"event": "message.delta", "run_id": RID, "delta": delta, "seq": seq}


def started(tool, tcid):
    return {
        "event": "tool.started",
        "run_id": RID,
        "tool": tool,
        "preview": "",
        "tool_call_id": tcid,
    }


def completed(tool, tcid, error=False):
    return {
        "event": "tool.completed",
        "run_id": RID,
        "tool": tool,
        "duration": 0,
        "error": error,
        "tool_call_id": tcid,
        "preview": "",
    }


def done(output="", kind="run.completed", **kw):
    return {"event": kind, "run_id": RID, "output": output, **kw}


def types(evs):
    return [e.type for e in evs]


def feed_all(tr, frames):
    out = []
    for f in frames:
        out += tr.feed(f)
    return out


def test_start_event():
    (e,) = Translator("t", "r").start()
    assert (e.type, e.thread_id, e.run_id) == (T.RUN_STARTED, "t", "r")


def test_text_deltas_share_one_message():
    tr = Translator("t", "r")
    evs = feed_all(tr, [d("Hel"), d("lo"), d("")])
    assert types(evs) == [
        T.TEXT_MESSAGE_START,
        T.TEXT_MESSAGE_CONTENT,
        T.TEXT_MESSAGE_CONTENT,
    ]
    assert {e.message_id for e in evs} == {evs[0].message_id}
    assert [e.delta for e in evs[1:]] == ["Hel", "lo"]


def test_text_closed_before_run_finished():
    tr = Translator("t", "r")
    feed_all(tr, [d("hi")])
    evs = tr.close()
    assert types(evs) == [T.TEXT_MESSAGE_END, T.RUN_FINISHED]


def test_completed_closes_text_and_close_has_no_second_end():
    tr = Translator("t", "r")
    evs = feed_all(tr, [d("hi"), done("hi")])
    assert types(evs)[-1] == T.TEXT_MESSAGE_END
    assert tr.terminal == "completed"
    assert types(tr.close()) == [T.RUN_FINISHED]


def test_failed_closes_text_then_run_error():
    tr = Translator("t", "r")
    evs = feed_all(tr, [d("par"), done(kind="run.failed", error="boom")])
    assert types(evs)[-1] == T.TEXT_MESSAGE_END
    close = tr.close()
    assert types(close) == [T.RUN_ERROR]
    assert close[0].message == "boom"


def test_cancelled_and_interrupted_finish_cleanly():
    for kind in ("run.cancelled", "run.interrupted"):
        tr = Translator("t", "r")
        feed_all(tr, [d("x"), done(kind=kind)])
        assert tr.terminal == kind.split(".")[1]
        assert types(tr.close()) == [T.RUN_FINISHED]


def test_output_surfaced_when_no_deltas_streamed():
    tr = Translator("t", "r")
    evs = tr.feed(done("only final"))
    assert types(evs) == [
        T.TEXT_MESSAGE_START,
        T.TEXT_MESSAGE_CONTENT,
        T.TEXT_MESSAGE_END,
    ]
    tr2 = Translator("t", "r")
    assert types(feed_all(tr2, [d("a"), done("a")])).count(T.TEXT_MESSAGE_CONTENT) == 1


def test_backend_tool_progress_with_empty_args_then_result():
    tr = Translator("t", "r")
    evs = feed_all(
        tr,
        [
            d("Let me check. "),
            started("spike_backend_echo", "c1"),
            completed("spike_backend_echo", "c1"),
        ],
    )
    assert types(evs) == [
        T.TEXT_MESSAGE_START,
        T.TEXT_MESSAGE_CONTENT,
        T.TEXT_MESSAGE_END,  # text closed before the tool call
        T.TOOL_CALL_START,
        T.TOOL_CALL_ARGS,
        T.TOOL_CALL_END,
        T.TOOL_CALL_RESULT,
    ]
    assert evs[3].tool_call_name == "spike_backend_echo"
    assert evs[4].delta == "{}"
    assert json.loads(evs[6].content) == {"status": "completed"}
    errs = feed_all(
        Translator("t", "r"), [started("x", "c2"), completed("x", "c2", error=True)]
    )
    assert json.loads(errs[-1].content) == {"status": "error"}


def test_frontend_tool_returns_marker_not_events():
    tr = Translator("t", "r")
    assert tr.feed(started("call_frontend_tool", "cf1")) == [FrontendCall("cf1")]
    assert (
        tr.feed(completed("call_frontend_tool", "cf1")) == []
    )  # browser supplies the result


def test_frontend_call_events_use_broker_name_and_args():
    tr = Translator("t", "r")
    feed_all(tr, [d("Filling the form. ")])
    evs = tr.frontend_call("cf1", "set_fields", {"values": {"title": "Dune"}})
    assert types(evs) == [
        T.TEXT_MESSAGE_END,
        T.TOOL_CALL_START,
        T.TOOL_CALL_ARGS,
        T.TOOL_CALL_END,
    ]
    assert evs[1].tool_call_name == "set_fields" and evs[1].tool_call_id == "cf1"
    assert json.loads(evs[2].delta) == {"values": {"title": "Dune"}}
    assert types(tr.suspend()) == [T.RUN_FINISHED]


def test_suspend_closes_open_text():
    tr = Translator("t", "r")
    feed_all(tr, [d("x")])
    assert types(tr.suspend()) == [T.TEXT_MESSAGE_END, T.RUN_FINISHED]


def test_fail_closes_text_then_error():
    tr = Translator("t", "r")
    feed_all(tr, [d("x")])
    evs = tr.fail("bad", "c")
    assert types(evs) == [T.TEXT_MESSAGE_END, T.RUN_ERROR] and evs[1].code == "c"


def test_unknown_frames_ignored():
    tr = Translator("t", "r")
    assert tr.feed({"event": "reasoning.available", "text": "hmm"}) == []
    assert tr.feed({"event": "replay.truncated"}) == []
    assert tr.feed({"event": "tool.completed", "tool_call_id": "never-started"}) == []


def test_transcript_messages():
    tr = Translator("t", "r")
    feed_all(tr, [d("Let me look. "), started("b", "c1"), completed("b", "c1")])
    tr.frontend_call("cf1", "set_fields", {"a": 1})
    msgs = tr.drain_messages()
    assert [m["role"] for m in msgs] == ["assistant", "assistant", "tool", "assistant"]
    assert msgs[0]["content"] == "Let me look. "
    assert msgs[1]["toolCalls"][0]["function"]["name"] == "b"
    assert msgs[2]["toolCallId"] == "c1"
    assert msgs[3]["toolCalls"][0]["function"] == {
        "name": "set_fields",
        "arguments": '{"a": 1}',
    }
    assert tr.drain_messages() == []


def test_full_s7_frontend_sequence():
    """Captured /v1/runs frames of evidence/q4_runs_fe.sse (text, call_frontend_tool, final answer, run.completed)."""
    frames = [
        d("Filling the form. "),
        started("call_frontend_tool", "call_mock_fe_0_0"),
        completed("call_frontend_tool", "call_mock_fe_0_0"),
        d("\n\nFinal answer after tools."),
        done("Final answer after tools."),
    ]
    tr = Translator("t", "r")
    out = []
    for f in frames:
        for it in tr.feed(f):
            if isinstance(it, FrontendCall):
                out += tr.frontend_call(it.tool_call_id, "set_fields", {"x": 1})
                out += tr.suspend()
                break
            out.append(it)
        else:
            continue
        break
    assert types(out) == [
        T.TEXT_MESSAGE_START,
        T.TEXT_MESSAGE_CONTENT,
        T.TEXT_MESSAGE_END,
        T.TOOL_CALL_START,
        T.TOOL_CALL_ARGS,
        T.TOOL_CALL_END,
        T.RUN_FINISHED,
    ]
