# -*- coding: utf-8 -*-

"""
Usage aggregation and transparent prompt-cache simulation helpers.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from threading import Lock
from typing import Any, Dict, List, Optional

from loguru import logger
from pydantic import BaseModel

from kiro.config import (
    PROMPT_CACHE_DEFAULT_TTL_SECONDS,
    PROMPT_CACHE_MIN_TOKENS,
    PROMPT_CACHE_ONE_HOUR_TTL_SECONDS,
    PROMPT_CACHE_OPUS_MIN_TOKENS,
    PROMPT_CACHE_SIMULATION,
)
from kiro.tokenizer import count_tokens


@dataclass
class UsageRecord:
    """Normalized usage data produced after a response completes."""

    input_tokens: int = 0
    output_tokens: int = 0
    credits_used: float = 0.0
    upstream_cache_read_input_tokens: int = 0
    upstream_cache_creation_input_tokens: int = 0
    simulated_cache_read_input_tokens: int = 0
    simulated_cache_creation_input_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    def to_dict(self) -> Dict[str, Any]:
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "total_tokens": self.total_tokens,
            "credits_used": self.credits_used,
            "upstream_cache_read_input_tokens": self.upstream_cache_read_input_tokens,
            "upstream_cache_creation_input_tokens": self.upstream_cache_creation_input_tokens,
            "simulated_cache_read_input_tokens": self.simulated_cache_read_input_tokens,
            "simulated_cache_creation_input_tokens": self.simulated_cache_creation_input_tokens,
        }


@dataclass
class PromptCacheUsage:
    """Local Anthropic-style prompt cache simulation result."""

    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0
    enabled: bool = False


@dataclass
class _PromptCacheBreakpoint:
    fingerprint: str
    cumulative_tokens: int
    ttl_seconds: int


@dataclass
class PromptCacheProfile:
    """Cacheable request prefix profile."""

    breakpoints: List[_PromptCacheBreakpoint]
    total_input_tokens: int
    model: str


@dataclass
class _PromptCacheEntry:
    expires_at: float
    ttl_seconds: int


@dataclass
class _CacheBlock:
    value: Any
    tokens: int
    ttl_seconds: int = 0
    is_message_end: bool = False


class PromptCacheTracker:
    """
    Simulates Anthropic-style cache read/create trends with local fingerprints.

    This does not claim Kiro upstream performed a real cache read. It tracks
    deterministic request prefixes per account and gives dashboard/client
    visibility into whether the prompt shape is cache-friendly.
    """

    def __init__(self, max_ttl_seconds: int = PROMPT_CACHE_ONE_HOUR_TTL_SECONDS):
        self._lock = Lock()
        self._entries_by_account: Dict[str, Dict[str, _PromptCacheEntry]] = {}
        self._max_ttl_seconds = max(max_ttl_seconds, PROMPT_CACHE_DEFAULT_TTL_SECONDS)

    def build_anthropic_profile(
        self,
        *,
        model: str,
        messages: Optional[List[Any]] = None,
        tools: Optional[List[Any]] = None,
        system: Any = None,
        tool_choice: Any = None,
        total_input_tokens: int = 0,
    ) -> Optional[PromptCacheProfile]:
        if not PROMPT_CACHE_SIMULATION:
            return None

        blocks = _flatten_anthropic_cache_blocks(
            model=model,
            messages=messages or [],
            tools=tools or [],
            system=system,
            tool_choice=tool_choice,
        )
        if not blocks:
            return None

        hasher = hashlib.sha256()
        cumulative_tokens = 0
        active_ttl = 0
        breakpoints: List[_PromptCacheBreakpoint] = []

        for block in blocks:
            canonical = _canonical_json(block.value)
            _write_hash_chunk(hasher, canonical)
            cumulative_tokens += block.tokens

            ttl = block.ttl_seconds
            if ttl > 0:
                active_ttl = ttl
            elif block.is_message_end and active_ttl > 0:
                ttl = active_ttl

            if ttl <= 0:
                continue

            breakpoints.append(_PromptCacheBreakpoint(
                fingerprint=hasher.hexdigest(),
                cumulative_tokens=cumulative_tokens,
                ttl_seconds=ttl,
            ))

        if not breakpoints:
            return None

        return PromptCacheProfile(
            breakpoints=breakpoints,
            total_input_tokens=max(total_input_tokens, cumulative_tokens),
            model=model,
        )

    def compute(self, account_id: str, profile: Optional[PromptCacheProfile]) -> PromptCacheUsage:
        if not PROMPT_CACHE_SIMULATION or not account_id or profile is None or not profile.breakpoints:
            return PromptCacheUsage()

        min_tokens = _min_cacheable_tokens_for_model(profile.model)
        last = profile.breakpoints[-1]
        last_tokens = min(last.cumulative_tokens, profile.total_input_tokens)
        now = time.time()

        with self._lock:
            self._prune_expired(now)
            entries = self._entries_by_account.get(account_id, {})

            if not entries:
                return PromptCacheUsage(
                    cache_creation_input_tokens=last_tokens if last_tokens >= min_tokens else 0,
                    cache_read_input_tokens=0,
                    enabled=True,
                )

            max_cacheable = int(profile.total_input_tokens * 0.85)
            last_tokens = min(last_tokens, max_cacheable)
            matched_tokens = 0

            for breakpoint in reversed(profile.breakpoints):
                if breakpoint.cumulative_tokens < min_tokens:
                    continue
                entry = entries.get(breakpoint.fingerprint)
                if entry is None or entry.expires_at <= now:
                    continue

                entry.expires_at = now + entry.ttl_seconds
                matched_tokens = min(breakpoint.cumulative_tokens, profile.total_input_tokens)
                matched_tokens = min(matched_tokens, last_tokens)
                break

            return PromptCacheUsage(
                cache_creation_input_tokens=max(last_tokens - matched_tokens, 0),
                cache_read_input_tokens=matched_tokens,
                enabled=True,
            )

    def update(self, account_id: str, profile: Optional[PromptCacheProfile]) -> None:
        if not PROMPT_CACHE_SIMULATION or not account_id or profile is None or not profile.breakpoints:
            return

        min_tokens = _min_cacheable_tokens_for_model(profile.model)
        now = time.time()

        with self._lock:
            self._prune_expired(now)
            entries = self._entries_by_account.setdefault(account_id, {})
            for breakpoint in profile.breakpoints:
                if breakpoint.cumulative_tokens < min_tokens:
                    continue
                ttl = min(max(breakpoint.ttl_seconds, PROMPT_CACHE_DEFAULT_TTL_SECONDS), self._max_ttl_seconds)
                entries[breakpoint.fingerprint] = _PromptCacheEntry(
                    expires_at=now + ttl,
                    ttl_seconds=ttl,
                )

    def snapshot(self) -> Dict[str, Any]:
        now = time.time()
        with self._lock:
            self._prune_expired(now)
            entries = sum(len(items) for items in self._entries_by_account.values())
            accounts = sum(1 for items in self._entries_by_account.values() if items)
        return {
            "enabled": PROMPT_CACHE_SIMULATION,
            "tracked_accounts": accounts,
            "entries": entries,
            "min_tokens": PROMPT_CACHE_MIN_TOKENS,
            "opus_min_tokens": PROMPT_CACHE_OPUS_MIN_TOKENS,
            "default_ttl_seconds": PROMPT_CACHE_DEFAULT_TTL_SECONDS,
        }

    def _prune_expired(self, now: float) -> None:
        for account_id, entries in list(self._entries_by_account.items()):
            for fingerprint, entry in list(entries.items()):
                if entry.expires_at <= now:
                    del entries[fingerprint]
            if not entries:
                del self._entries_by_account[account_id]


_PROMPT_CACHE_TRACKER = PromptCacheTracker()


def get_prompt_cache_tracker() -> PromptCacheTracker:
    return _PROMPT_CACHE_TRACKER


def extract_credits_used(usage: Any) -> float:
    """Extract numeric Kiro credits from usage events, if present."""

    if isinstance(usage, (int, float)):
        return float(usage)
    if isinstance(usage, dict):
        for key in ("credits_used", "creditsUsed", "credits", "usage"):
            value = usage.get(key)
            if isinstance(value, (int, float)):
                return float(value)
    return 0.0


async def notify_usage_callback(callback: Any, usage: UsageRecord) -> None:
    """Run an optional usage callback without breaking the response stream."""

    if callback is None:
        return
    try:
        result = callback(usage.to_dict())
        if hasattr(result, "__await__"):
            await result
    except Exception as exc:
        logger.warning(f"Usage callback failed: {exc}")


def _flatten_anthropic_cache_blocks(
    *,
    model: str,
    messages: List[Any],
    tools: List[Any],
    system: Any,
    tool_choice: Any,
) -> List[_CacheBlock]:
    blocks: List[_CacheBlock] = []
    prelude = {
        "kind": "request_prelude",
        "model": model,
        "tool_choice": _to_plain_data(tool_choice),
    }
    blocks.append(_make_block(prelude))

    for index, tool in enumerate(tools):
        tool_data = _to_plain_data(tool)
        value = {
            "kind": "tool",
            "tool_index": index,
            "name": tool_data.get("name"),
            "description": tool_data.get("description"),
            "input_schema": tool_data.get("input_schema"),
            "type": tool_data.get("type"),
        }
        blocks.append(_make_block(value, ttl_seconds=_extract_prompt_cache_ttl(tool_data)))

    _append_system_blocks(blocks, system)

    for index, message in enumerate(messages):
        _append_message_blocks(blocks, index, _to_plain_data(message))

    return blocks


def _append_system_blocks(blocks: List[_CacheBlock], system: Any) -> None:
    system_data = _to_plain_data(system)
    if system_data is None:
        return

    if isinstance(system_data, str):
        _append_prompt_block(blocks, {
            "kind": "system",
            "system_index": 0,
            "block": {"type": "text", "text": system_data},
        })
        return

    if isinstance(system_data, list):
        for index, item in enumerate(system_data):
            block = item if isinstance(item, dict) else {"type": "text", "text": str(item)}
            _append_prompt_block(blocks, {
                "kind": "system",
                "system_index": index,
                "block": block,
            })


def _append_message_blocks(blocks: List[_CacheBlock], message_index: int, message: Dict[str, Any]) -> None:
    role = message.get("role")
    content = message.get("content")
    if isinstance(content, str):
        _append_prompt_block(
            blocks,
            {
                "kind": "message",
                "message_index": message_index,
                "role": role,
                "block_index": 0,
                "block": {"type": "text", "text": content},
            },
            is_message_end=True,
        )
        return

    if isinstance(content, list):
        last_index = len(content) - 1
        for block_index, block in enumerate(content):
            _append_prompt_block(
                blocks,
                {
                    "kind": "message",
                    "message_index": message_index,
                    "role": role,
                    "block_index": block_index,
                    "block": block,
                },
                is_message_end=block_index == last_index,
            )
        return

    if content is not None:
        _append_prompt_block(
            blocks,
            {
                "kind": "message",
                "message_index": message_index,
                "role": role,
                "block_index": 0,
                "block": content,
            },
            is_message_end=True,
        )


def _append_prompt_block(blocks: List[_CacheBlock], wrapper: Dict[str, Any], is_message_end: bool = False) -> None:
    block_value = _to_plain_data(wrapper.get("block"))
    if _is_anthropic_billing_header_block(block_value):
        return

    ttl_seconds = _extract_prompt_cache_ttl(block_value)
    blocks.append(_make_block(
        _strip_position_keys(wrapper),
        ttl_seconds=ttl_seconds,
        is_message_end=is_message_end,
    ))


def _make_block(value: Any, ttl_seconds: int = 0, is_message_end: bool = False) -> _CacheBlock:
    canonical = _canonical_json(_strip_cache_control(value))
    return _CacheBlock(
        value=value,
        tokens=count_tokens(canonical, apply_claude_correction=False),
        ttl_seconds=ttl_seconds,
        is_message_end=is_message_end,
    )


def _extract_prompt_cache_ttl(value: Any) -> int:
    block = _to_plain_data(value)
    if not isinstance(block, dict):
        return 0

    cache_control = block.get("cache_control")
    if not isinstance(cache_control, dict):
        return 0
    if str(cache_control.get("type", "")).lower() != "ephemeral":
        return 0

    ttl_value = cache_control.get("ttl")
    if ttl_value is None:
        return PROMPT_CACHE_DEFAULT_TTL_SECONDS
    if isinstance(ttl_value, (int, float)) and ttl_value > 0:
        ttl_seconds = int(ttl_value)
    else:
        ttl_text = str(ttl_value).strip().lower()
        if ttl_text.endswith("h"):
            ttl_seconds = int(float(ttl_text[:-1]) * 3600)
        elif ttl_text.endswith("m"):
            ttl_seconds = int(float(ttl_text[:-1]) * 60)
        elif ttl_text.endswith("s"):
            ttl_seconds = int(float(ttl_text[:-1]))
        else:
            try:
                ttl_seconds = int(float(ttl_text))
            except ValueError:
                ttl_seconds = PROMPT_CACHE_DEFAULT_TTL_SECONDS

    if ttl_seconds > PROMPT_CACHE_DEFAULT_TTL_SECONDS:
        return PROMPT_CACHE_ONE_HOUR_TTL_SECONDS
    return PROMPT_CACHE_DEFAULT_TTL_SECONDS


def _min_cacheable_tokens_for_model(model: str) -> int:
    if "opus" in model.lower():
        return PROMPT_CACHE_OPUS_MIN_TOKENS
    return PROMPT_CACHE_MIN_TOKENS


def _canonical_json(value: Any) -> str:
    return json.dumps(_to_plain_data(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _to_plain_data(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(exclude_none=True)
    if isinstance(value, list):
        return [_to_plain_data(item) for item in value]
    if isinstance(value, tuple):
        return [_to_plain_data(item) for item in value]
    if isinstance(value, dict):
        return {key: _to_plain_data(item) for key, item in value.items() if item is not None}
    return value


def _strip_position_keys(value: Dict[str, Any]) -> Dict[str, Any]:
    return {
        key: item
        for key, item in value.items()
        if key not in {"tool_index", "system_index", "message_index", "block_index"}
    }


def _strip_cache_control(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _strip_cache_control(item)
            for key, item in value.items()
            if key != "cache_control"
        }
    if isinstance(value, list):
        return [_strip_cache_control(item) for item in value]
    return value


def _write_hash_chunk(hasher: Any, chunk: str) -> None:
    encoded = chunk.encode("utf-8")
    hasher.update(str(len(encoded)).encode("ascii"))
    hasher.update(b"\0")
    hasher.update(encoded)
    hasher.update(b"\0")


def _is_anthropic_billing_header_block(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    block_type = value.get("type")
    if block_type not in (None, "", "text"):
        return False
    text = value.get("text")
    if not isinstance(text, str):
        return False
    return text.lstrip().lower().startswith("x-anthropic-billing-header:")
