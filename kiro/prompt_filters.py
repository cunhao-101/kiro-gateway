# -*- coding: utf-8 -*-

"""
Prompt filtering helpers for client-injected system prompts.

The gateway cannot change Kiro's hidden upstream prompt. These filters only
operate on the system prompt supplied by API clients before it is prepended to
the user message sent to Kiro.
"""

from typing import List

from kiro import config


_OFF_MODES = {"", "off", "false", "0", "no", "disabled"}
_IDENTITY_MODES = {"identity", "cc_identity", "claude_code_identity"}


def filter_system_prompt(prompt: str) -> str:
    """
    Applies the configured prompt filter to a client-supplied system prompt.

    Default behavior is a no-op. The identity filter strips Claude Code branding
    and model metadata while preserving project instructions, tool schemas, and
    safety rules that Claude Code relies on for normal operation.
    """
    if not prompt:
        return prompt

    mode = getattr(config, "PROMPT_FILTER_MODE", "off").strip().lower()
    if mode in _OFF_MODES:
        return prompt
    if mode in _IDENTITY_MODES:
        return strip_claude_code_identity(prompt)

    return prompt


def strip_claude_code_identity(prompt: str) -> str:
    """Removes Claude Code identity and product metadata lines."""
    kept: List[str] = []

    for line in prompt.splitlines():
        if _is_claude_code_identity_line(line):
            continue
        kept.append(line)

    return _collapse_blank_lines("\n".join(kept)).strip()


def _is_claude_code_identity_line(line: str) -> bool:
    stripped = line.strip()
    lower = stripped.lower()

    if not stripped:
        return False

    exact_prefixes = (
        "x-anthropic-billing-header:",
        "you are claude code, anthropic's official cli for claude.",
        "- you are powered by the model named ",
        "- assistant knowledge cutoff is ",
        "- the most recent claude model family is ",
        "- claude code is available as ",
        "- fast mode for claude code uses ",
        "- /help: get help with using claude code",
        "- to give feedback, users should report the issue at ",
        "co-authored-by: claude ",
    )
    if any(lower.startswith(prefix) for prefix in exact_prefixes):
        return True

    line_fragments = (
        "generated with [claude code]",
        "anthropic's official cli for claude",
        "claude.com/claude-code",
        "claude-code/issues",
    )
    if any(fragment in lower for fragment in line_fragments):
        return True

    return False


def _collapse_blank_lines(text: str) -> str:
    lines = []
    blank_count = 0

    for line in text.splitlines():
        if line.strip():
            blank_count = 0
            lines.append(line)
            continue

        blank_count += 1
        if blank_count <= 1:
            lines.append(line)

    return "\n".join(lines)
