# -*- coding: utf-8 -*-

"""
Tests for usage aggregation and prompt-cache simulation helpers.
"""

import pytest

from kiro.account_manager import Account, AccountManager
from kiro.usage_tracking import PromptCacheTracker, extract_credits_used


def test_extract_credits_used_accepts_numeric_and_dict():
    assert extract_credits_used(1.5) == 1.5
    assert extract_credits_used({"credits_used": 2.25}) == 2.25
    assert extract_credits_used({"usage": 0.75}) == 0.75
    assert extract_credits_used({"usage": "unknown"}) == 0.0


def test_prompt_cache_tracker_reports_read_after_update():
    tracker = PromptCacheTracker()
    long_system = "You are a helpful coding assistant with stable project context. " * 240
    system = [{
        "type": "text",
        "text": long_system,
        "cache_control": {"type": "ephemeral"},
    }]
    messages = [{"role": "user", "content": "hello"}]

    profile = tracker.build_anthropic_profile(
        model="claude-sonnet-4.5",
        messages=messages,
        system=system,
        total_input_tokens=4096,
    )

    assert profile is not None
    first = tracker.compute("acct-1", profile)
    assert first.enabled is True
    assert first.cache_creation_input_tokens > 0
    assert first.cache_read_input_tokens == 0

    tracker.update("acct-1", profile)
    second = tracker.compute("acct-1", profile)
    assert second.cache_read_input_tokens > 0
    assert second.cache_creation_input_tokens < first.cache_creation_input_tokens


@pytest.mark.asyncio
async def test_account_manager_record_usage_aggregates_without_request_count():
    manager = AccountManager("missing.json", "state.json")
    account = Account(id="acct-1")
    manager._accounts[account.id] = account

    await manager.record_usage(account.id, {
        "input_tokens": 100,
        "output_tokens": 25,
        "total_tokens": 125,
        "credits_used": 0.5,
        "simulated_cache_read_input_tokens": 80,
        "simulated_cache_creation_input_tokens": 20,
    })

    assert account.stats.total_requests == 0
    assert account.stats.input_tokens == 100
    assert account.stats.output_tokens == 25
    assert account.stats.total_tokens == 125
    assert account.stats.credits_used == 0.5
    assert account.stats.simulated_cache_read_input_tokens == 80
    assert account.stats.simulated_cache_creation_input_tokens == 20
