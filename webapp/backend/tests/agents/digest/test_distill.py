from pathlib import Path

from app.agents.digest.distill import distill

FIXTURES = Path(__file__).parent / "fixtures"


def _read(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def test_user_prompts_are_verbatim():
    out = distill(_read("short.jsonl"), subagent_blobs={})
    assert "Add a /healthcheck route" in out


def test_assistant_text_is_verbatim():
    out = distill(_read("short.jsonl"), subagent_blobs={})
    assert "I'll add it." in out
    assert "Done." in out


def test_edit_line_shows_path_counts_and_preview():
    out = distill(_read("short.jsonl"), subagent_blobs={})
    # Path + approximate add/remove counts
    assert "Edit webapp/backend/app/main.py (+1 -1)" in out
    # The first added line now appears as a grounding preview
    assert "NEW WITH LOTS OF CONTENT" in out
    # old_string content is never shown
    assert "OLD" not in out


def test_tool_result_collapses_to_status_plus_prefix():
    out = distill(_read("short.jsonl"), subagent_blobs={})
    # Result body kept (short here, fits under 80 chars)
    assert "ok" in out


def test_tier4_events_are_dropped():
    out = distill(_read("short.jsonl"), subagent_blobs={})
    assert "permission-mode" not in out
    assert "acceptEdits" not in out
    assert "ai-title" not in out
    assert "Add healthcheck" not in out  # the ai-title text itself
    assert "file-history-snapshot" not in out


def test_each_line_carries_source_uuid():
    out = distill(_read("short.jsonl"), subagent_blobs={})
    # User prompt line carries its uuid; LLM uses these as anchor candidates
    assert "[u1]" in out
    assert "[a1]" in out
    assert "[a3]" in out
    # Dropped events do NOT appear as anchor candidates
    assert "[ai-title]" not in out


def test_emits_event_uuids_helper():
    from app.agents.digest.distill import distill_with_uuids
    text, uuids = distill_with_uuids(_read("short.jsonl"), subagent_blobs={})
    assert "u1" in uuids
    assert "a1" in uuids
    assert "a3" in uuids
    # The Edit tool_use is collapsed but its uuid is still anchorable
    assert "a2" in uuids


def test_determinism_same_input_same_output():
    a = distill(_read("short.jsonl"), subagent_blobs={})
    b = distill(_read("short.jsonl"), subagent_blobs={})
    assert a == b


def test_adjacent_duplicate_events_are_deduped():
    """Two events with identical payload but different uuids should
    collapse to one line. Some Claude Code hooks double-fire."""
    import json as _json
    dupe = _json.dumps({
        "type": "assistant", "uuid": "dup-a",
        "message": {"content": [{"type": "text", "text": "Same payload"}]},
    })
    dupe2 = _json.dumps({
        "type": "assistant", "uuid": "dup-b",
        "message": {"content": [{"type": "text", "text": "Same payload"}]},
    })
    blob = (dupe + "\n" + dupe2 + "\n").encode("utf-8")
    out = distill(blob, subagent_blobs={})
    # Only one line in the output
    assert out.count("Same payload") == 1


def test_subagent_collapses_to_one_line():
    parent = _read("with_subagent.jsonl")
    child = _read("with_subagent_child.jsonl")
    # The plugin's storage names subagent blobs by tool_use_id (see
    # trace_service.create_or_update_trace). The distiller takes the
    # same dict shape.
    out = distill(parent, subagent_blobs={"tu1": child})
    # One subagent line, not the child's three events
    matches = [ln for ln in out.splitlines() if ln.startswith("[a1]")]
    assert len(matches) == 1
    line = matches[0]
    assert "Subagent[code-reviewer]" in line
    assert "Audit auth" in line
    # The child's final assistant text is the summary
    assert "Two issues" in line
    # The child's interior is NEVER inlined
    assert "webapp/backend/app/auth/oauth.py" not in out


def test_subagent_missing_blob_falls_back_to_action_count():
    parent = _read("with_subagent.jsonl")
    out = distill(parent, subagent_blobs={})  # no child blob given
    line = next(ln for ln in out.splitlines() if ln.startswith("[a1]"))
    # Falls back to a non-empty descriptor so the LLM has SOMETHING to read
    assert "Subagent[code-reviewer]" in line
    assert "Audit auth" in line


def _synth_exploration_blob(reads: int, greps: int) -> bytes:
    """Synthesize a JSONL with N reads + M greps, no intervening text."""
    import json as _json
    lines: list[str] = []
    lines.append(_json.dumps({
        "type": "user", "uuid": "uStart",
        "message": {"content": "Find the auth handler"},
    }))
    for i in range(reads):
        lines.append(_json.dumps({
            "type": "assistant", "uuid": f"r{i}",
            "message": {"content": [{
                "type": "tool_use", "id": f"tr{i}", "name": "Read",
                "input": {"file_path": f"webapp/backend/app/file_{i}.py"},
            }]},
        }))
    for i in range(greps):
        lines.append(_json.dumps({
            "type": "assistant", "uuid": f"g{i}",
            "message": {"content": [{
                "type": "tool_use", "id": f"tg{i}", "name": "Grep",
                "input": {"pattern": "handler", "path": "webapp/backend"},
            }]},
        }))
    lines.append(_json.dumps({
        "type": "assistant", "uuid": "aEnd",
        "message": {"content": [{
            "type": "text", "text": "Found it in oauth.py.",
        }]},
    }))
    return ("\n".join(lines) + "\n").encode("utf-8")


def test_short_exploration_run_not_collapsed():
    # 5 reads is BELOW the threshold of 6
    blob = _synth_exploration_blob(reads=5, greps=0)
    out = distill(blob, subagent_blobs={})
    # All 5 reads still appear individually
    assert out.count("Read webapp/backend/app/file_") == 5
    assert "[exploration:" not in out


def test_long_exploration_run_is_collapsed():
    # 12 reads + 3 greps = 15 consecutive tool calls. The synthetic blob
    # renders to ~680 chars, i.e. ~204 estimated tokens at 0.3/char, so
    # a 150-token target forces the adaptive collapse.
    blob = _synth_exploration_blob(reads=12, greps=3)
    out = distill(blob, subagent_blobs={}, target_tokens=150)
    # The individual Reads are gone, replaced with one collapse line
    assert out.count("Read webapp/backend/app/file_") == 0
    assert "[exploration:" in out
    # Spine survives
    assert "Find the auth handler" in out
    assert "Found it in oauth.py." in out


def test_distill_carries_target_and_hardcap_kwargs():
    # Smoke test that the signature accepts the new kwargs
    blob = _synth_exploration_blob(reads=1, greps=0)
    out = distill(
        blob, subagent_blobs={}, target_tokens=60_000, hard_cap_tokens=200_000,
    )
    assert isinstance(out, str)


def test_truncation_when_over_hardcap_keeps_head_and_tail():
    blob = _synth_exploration_blob(reads=200, greps=0)
    # Force the hard cap to trip by setting an absurdly small cap; the
    # adaptive collapse won't help because every event collapses to one line
    # already after the exploration pass. We bypass collapse by setting the
    # target VERY low and the hard cap also low.
    out = distill(
        blob, subagent_blobs={}, target_tokens=10, hard_cap_tokens=20,
    )
    assert "[… elided" in out
    # First and last events still present
    assert "Find the auth handler" in out
    assert "Found it in oauth.py." in out


def test_spawn_agent_dispatch_renders_like_task():
    # Codex-converted traces dispatch subagents via spawn_agent tool_uses;
    # the converter gives them the same input keys the Task branch reads
    # (subagent_type, description), so the distiller must emit the same
    # Subagent[...] one-liner instead of a bare tool name.
    blob = (
        b'{"type":"assistant","uuid":"a9","message":{"content":[{"type":'
        b'"tool_use","id":"c_spawn","name":"spawn_agent","input":{'
        b'"subagent_type":"default","model":"default",'
        b'"prompt":"Review src/util.ts for edge cases and report back.",'
        b'"description":"Review src/util.ts for edge cases and report back.'
        b'"}}]}}\n'
    )
    out = distill(blob, subagent_blobs={})
    assert (
        "Subagent[default]: Review src/util.ts for edge cases and report"
        in out
    )
    assert "spawn_agent" not in out


def test_codex_shell_renders_command_like_bash():
    # codex_convert emits shell tool_uses with the same input keys as Bash
    # (command, description); the command must survive into the one-liner.
    blob = (
        b'{"type":"assistant","uuid":"a1","message":{"content":[{"type":'
        b'"tool_use","id":"c1","name":"shell","input":{'
        b'"command":"git diff main --stat","description":"/repo"}}]}}\n'
    )
    out = distill(blob, subagent_blobs={})
    assert "shell: git diff main --stat" in out


def test_codex_update_plan_is_dropped_as_scratchpad():
    # update_plan is Codex's TodoWrite: plan churn, not work.
    blob = (
        b'{"type":"assistant","uuid":"a1","message":{"content":[{"type":'
        b'"tool_use","id":"c1","name":"update_plan","input":{'
        b'"plan":[{"step":"x","status":"in_progress"}],"explanation":""}}]}}\n'
    )
    out = distill(blob, subagent_blobs={})
    assert "update_plan" not in out


def test_cursor_shell_renders_command_like_bash():
    # cursor_convert passes Cursor tool names through unchanged; Shell
    # carries the same input keys as Bash, so the command must survive.
    blob = (
        b'{"type":"assistant","uuid":"a1","message":{"content":[{"type":'
        b'"tool_use","id":"cursor-tool-1","name":"Shell","input":{'
        b'"command":"npm test --silent"}}]}}\n'
    )
    out = distill(blob, subagent_blobs={})
    assert "Shell: npm test --silent" in out


def test_cursor_subagent_dispatch_renders_like_task():
    # Cursor's Subagent tool uses the same input keys the Task branch
    # reads (subagent_type, description).
    blob = (
        b'{"type":"assistant","uuid":"a2","message":{"content":[{"type":'
        b'"tool_use","id":"cursor-agent-0","name":"Subagent","input":{'
        b'"subagent_type":"explore","description":"Frontend bug sweep",'
        b'"prompt":"Review the frontend."}}]}}\n'
    )
    out = distill(blob, subagent_blobs={})
    assert "Subagent[explore]: Frontend bug sweep" in out


def test_cursor_readfile_renders_path():
    # Cursor's ReadFile/Read carry path, not file_path; without the path
    # fallback this rendered as a bare tool name.
    blob = (
        b'{"type":"assistant","uuid":"a3","message":{"content":[{"type":'
        b'"tool_use","id":"cursor-tool-2","name":"ReadFile","input":{'
        b'"path":"/repo/src/router.tsx","limit":120}}]}}\n'
    )
    out = distill(blob, subagent_blobs={})
    assert "ReadFile /repo/src/router.tsx" in out
    assert "120" not in out  # extra input keys (limit) must not leak


def test_edit_preview_caps_at_three_lines_and_line_length():
    import json
    long_line = "x" * 200
    new_string = "\n".join([long_line, "line two", "line three", "line four"])
    rec = {
        "type": "assistant", "uuid": "a9",
        "message": {"content": [{
            "type": "tool_use", "name": "Write",
            "input": {"file_path": "x/y.py", "content": new_string},
        }]},
    }
    blob = (json.dumps(rec) + "\n").encode("utf-8")
    out = distill(blob, subagent_blobs={})
    assert "x" * 80 in out          # first line present...
    assert "x" * 81 not in out      # ...but truncated to 80 chars
    assert "line two" in out
    assert "line three" in out
    assert "line four" not in out   # 4th line dropped by the 3-line cap


def _user_record(uuid: str, text: str, **extra) -> bytes:
    import json
    rec = {"type": "user", "uuid": uuid,
           "message": {"content": text}, **extra}
    return (json.dumps(rec) + "\n").encode("utf-8")


def test_meta_user_records_are_dropped():
    # Skill tool bodies are replayed as user messages with isMeta: true.
    # They are not user-authored and must not reach the LLM or the
    # anchorable uuid surface.
    from app.agents.digest.distill import distill_with_uuids
    blob = (
        _user_record("m1", "Base directory for this skill: /x/y/z. " * 50,
                     isMeta=True)
        + _user_record("u1", "fix the login bug")
    )
    text, uuids = distill_with_uuids(blob, subagent_blobs={})
    assert "Base directory for this skill" not in text
    assert "m1" not in uuids
    assert "USER: fix the login bug" in text
    assert "u1" in uuids


def test_task_notification_compacts_to_one_liner():
    payload = (
        "<task-notification>\n"
        "<task-id>abc123</task-id>\n"
        "<tool-use-id>toolu_01X</tool-use-id>\n"
        "<output-file>/tmp/tasks/abc123.output</output-file>\n"
        "<status>completed</status>\n"
        "<summary>Agent \"Review Task 7\" finished</summary>\n"
        "<note>A task-notification fires each time this agent stops with no "
        "live background children of its own.</note>\n"
        "<result>Spec compliance verified. " + "Details. " * 60 + "</result>\n"
        "</task-notification>"
    )
    out = distill(_user_record("t1", payload), subagent_blobs={})
    line = next(ln for ln in out.splitlines() if ln.startswith("[t1]"))
    assert '[background task completed] Agent "Review Task 7" finished' in line
    assert "Spec compliance verified." in line
    # Boilerplate fields never survive
    assert "task-notification fires" not in line
    assert "toolu_01X" not in line
    # The result is prefix-truncated, not inlined wholesale
    assert len(line) < 400


def test_slash_command_renders_compact():
    payload = (
        "<command-name>compact</command-name>"
        "<command-message>compact</command-message>"
        "<command-args>keep the test plan</command-args>"
    )
    out = distill(_user_record("c1", payload), subagent_blobs={})
    assert "USER: /compact keep the test plan" in out
    assert "<command-name>" not in out


def test_command_tags_amid_prose_pass_through():
    # A user genuinely talking ABOUT the tags is not a slash command.
    payload = "why does <command-name>foo</command-name> appear in my trace?"
    out = distill(_user_record("c2", payload), subagent_blobs={})
    assert "appear in my trace?" in out


def test_local_command_stdout_compacts():
    payload = (
        "<local-command-stdout>\x1b[32mAll 12 checks passed\x1b[0m\n"
        + "noise line\n" * 40
        + "</local-command-stdout>"
    )
    out = distill(_user_record("s1", payload), subagent_blobs={})
    line = next(ln for ln in out.splitlines() if ln.startswith("[s1]"))
    assert "[command output] All 12 checks passed" in line
    assert "\x1b[" not in line
    assert len(line) < 200


def test_system_reminder_blocks_are_stripped():
    from app.agents.digest.distill import distill_with_uuids
    prose = "please fix the flaky test"
    payload = (
        f"{prose}<system-reminder>Contents of MEMORY.md: secret context "
        "the digest should never quote</system-reminder>"
    )
    blob = (
        _user_record("u1", payload)
        # A message that is NOTHING but a reminder drops out entirely.
        + _user_record("u2", "<system-reminder>background only</system-reminder>")
    )
    text, uuids = distill_with_uuids(blob, subagent_blobs={})
    assert prose in text
    assert "MEMORY.md" not in text
    assert "u2" not in uuids


def test_giant_user_paste_keeps_head_and_tail():
    # The ask often FOLLOWS a pasted log, so the cap must keep both ends.
    # The paste is multi-line, so assert on the whole (single-event) output
    # rather than splitlines().
    payload = (
        "the test fails with this log: "
        + "E AssertionError\n" * 600
        + "why does only the CI run hit this?"
    )
    out = distill(_user_record("u1", payload), subagent_blobs={})
    assert out.startswith("[u1] USER: the test fails with this log:")
    assert out.endswith("why does only the CI run hit this?")
    assert "chars elided]" in out
    assert len(out) < 2200


def test_edited_paths_collects_edit_tool_targets():
    from app.agents.digest.distill import edited_paths
    paths = edited_paths(_read("short.jsonl"), subagent_blobs={})
    assert paths == {"webapp/backend/app/main.py"}


def test_edited_paths_includes_subagent_edits():
    import json
    from app.agents.digest.distill import edited_paths
    child = {
        "type": "assistant", "uuid": "s1",
        "message": {"content": [{
            "type": "tool_use", "name": "Edit",
            "input": {"file_path": "child/only.ts",
                      "old_string": "a", "new_string": "b"},
        }]},
    }
    child_blob = (json.dumps(child) + "\n").encode("utf-8")
    paths = edited_paths(_read("short.jsonl"), subagent_blobs={"tu1": child_blob})
    assert "webapp/backend/app/main.py" in paths
    assert "child/only.ts" in paths
