# -*- coding: utf-8 -*-

"""
Unit tests for client system prompt filtering.
"""

from kiro import config
from kiro.converters_core import ThinkingConfig, UnifiedMessage, build_kiro_payload
from kiro.prompt_filters import filter_system_prompt


CLAUDE_CODE_PROMPT = """x-anthropic-billing-header: cc_version=2.1.140; cc_entrypoint=cli;
You are Claude Code, Anthropic's official CLI for Claude.

You are an interactive agent that helps users with software engineering tasks.
 - If the user asks for help or wants to give feedback inform them of the following:
  - /help: Get help with using Claude Code
  - To give feedback, users should report the issue at https://github.com/anthropics/claude-code/issues

### # Environment
 - Primary working directory: /repo/project
 - You are powered by the model named Opus 4.7 (1M context).
 - Assistant knowledge cutoff is January 2026.
 - The most recent Claude model family is Claude 4.X.
 - Claude Code is available as a CLI in the terminal.
 - Fast mode for Claude Code uses Claude Opus with faster output.

### Contents of /Users/example/.claude/CLAUDE.md
Always verify before making claims.

### Tool definitions
Use Edit for file changes.
"""


def test_prompt_filter_off_preserves_prompt(monkeypatch):
    monkeypatch.setattr(config, "PROMPT_FILTER_MODE", "off")

    assert filter_system_prompt(CLAUDE_CODE_PROMPT) == CLAUDE_CODE_PROMPT


def test_identity_filter_removes_claude_code_branding(monkeypatch):
    monkeypatch.setattr(config, "PROMPT_FILTER_MODE", "identity")

    result = filter_system_prompt(CLAUDE_CODE_PROMPT)

    assert "x-anthropic-billing-header" not in result
    assert "You are Claude Code" not in result
    assert "powered by the model" not in result
    assert "/help: Get help with using Claude Code" not in result
    assert "claude-code/issues" not in result


def test_identity_filter_keeps_project_and_tool_context(monkeypatch):
    monkeypatch.setattr(config, "PROMPT_FILTER_MODE", "identity")

    result = filter_system_prompt(CLAUDE_CODE_PROMPT)

    assert "You are an interactive agent" in result
    assert "Primary working directory: /repo/project" in result
    assert "Contents of /Users/example/.claude/CLAUDE.md" in result
    assert "Always verify before making claims." in result
    assert "Tool definitions" in result
    assert "Use Edit for file changes." in result


def test_build_kiro_payload_filters_client_system_prompt(monkeypatch):
    monkeypatch.setattr(config, "PROMPT_FILTER_MODE", "identity")

    result = build_kiro_payload(
        messages=[UnifiedMessage(role="user", content="Fix the bug.")],
        system_prompt=CLAUDE_CODE_PROMPT,
        model_id="claude-sonnet-4.5",
        tools=None,
        conversation_id="conv-123",
        profile_arn="arn:aws:test",
        thinking_config=ThinkingConfig(enabled=False),
    )

    content = result.payload["conversationState"]["currentMessage"]["userInputMessage"]["content"]

    assert "You are Claude Code" not in content
    assert "x-anthropic-billing-header" not in content
    assert "Contents of /Users/example/.claude/CLAUDE.md" in content
    assert "Tool definitions" in content
    assert "Fix the bug." in content
