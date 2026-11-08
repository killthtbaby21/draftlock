"""Model backends. `ClaudeRewriter` calls the Anthropic API; `MockRewriter`
works offline for tests and for trying the pipeline without a key."""

from __future__ import annotations

import re
import threading
from dataclasses import dataclass, field

DEFAULT_MODEL = "claude-opus-5-5"

# USD per million tokens (input, output, cache read). Used only for the estimate
# printed at the end of a run; check current pricing on the Anthropic website.
PRICES = {
    "claude-opus-5-5": (4.00, 20.00, 0.20),
    "claude-sonnet-5-5": (2.00, 10.00, 0.20),
    "claude-haiku-5-5": (0.10, 0.50, 0.01),
    "claude-fable-5-1": (10.00, 50.00, 0.25),
}

# Models that accept the server-side `fallbacks` parameter.
_FALLBACK_MODELS = ("claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5", "claude-fable-5-1")


class RewriteError(Exception):
    """A single request failed; the paragraph is kept as it was."""


class FatalError(Exception):
    """The whole run cannot continue (bad key, unknown model...)."""


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    requests: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def add(self, u) -> None:
        with self._lock:
            self.requests += 1
            self.input_tokens += getattr(u, "input_tokens", 0) or 0
            self.output_tokens += getattr(u, "output_tokens", 0) or 0
            self.cache_read_tokens += getattr(u, "cache_read_input_tokens", 0) or 0
            self.cache_write_tokens += getattr(u, "cache_creation_input_tokens", 0) or 0

    def cost(self, model: str) -> float | None:
        if model not in PRICES:
            return None
        pin, pout, pread = PRICES[model]
        # Cache writes bill at 1.25x input for the 5-minute TTL.
        return (self.input_tokens * pin + self.cache_write_tokens * pin * 1.25
                + self.cache_read_tokens * pread + self.output_tokens * pout) / 1e6


class ClaudeRewriter:
    def __init__(self, model: str = DEFAULT_MODEL, effort: str = "medium",
                 fallbacks: bool = True, max_retries: int = 4):
        import anthropic  # imported here so --mock/--dry-run work without the SDK configured

        self._anthropic = anthropic
        # Credentials come from the environment: ANTHROPIC_API_KEY, or an
        # `ant auth login` profile. Never hard-code a key.
        self.client = anthropic.Anthropic(max_retries=max_retries)
        self.model = model
        self.effort = effort
        self.fallbacks = fallbacks and model.startswith(_FALLBACK_MODELS)
        self.usage = Usage()

    def rewrite(self, system: str, user: str) -> str:
        a = self._anthropic
        kwargs = dict(
            model=self.model,
            max_tokens=16000,
            # The system prompt is identical for every paragraph, so cache it.
            system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": user}],
            output_config={"effort": self.effort},
        )
        try:
            if self.fallbacks:
                # If a safety classifier declines, the API re-runs the request on
                # Anthropic's recommended fallback model instead of returning a refusal.
                resp = self.client.beta.messages.create(
                    betas=["server-side-fallback-2026-07-01"], fallbacks="default", **kwargs)
            else:
                resp = self.client.messages.create(**kwargs)
        except a.AuthenticationError as e:
            raise FatalError("Authentication failed. Set ANTHROPIC_API_KEY or run `ant auth login`.") from e
        except a.PermissionDeniedError as e:
            raise FatalError(f"This API key cannot use {self.model}: {e.message}") from e
        except a.NotFoundError as e:
            raise FatalError(f"Unknown model '{self.model}'.") from e
        except a.BadRequestError as e:
            raise RewriteError(f"Request rejected: {e.message}") from e
        except a.RateLimitError as e:
            raise RewriteError("Rate limited after retries; try fewer --workers.") from e
        except a.APIStatusError as e:
            raise RewriteError(f"API error {e.status_code}: {e.message}") from e
        except a.APIConnectionError as e:
            raise RewriteError("Network error; check your connection.") from e

        self.usage.add(resp.usage)
        if resp.stop_reason == "refusal":
            raise RewriteError("The model declined this paragraph.")
        if resp.stop_reason == "max_tokens":
            raise RewriteError("Output was cut off (max_tokens).")
        text = "".join(b.text for b in resp.content if b.type == "text").strip()
        if not text:
            raise RewriteError("Empty response.")
        return text


class MockRewriter:
    """Offline stand-in. Makes small, deterministic edits so the pipeline,
    fact lock and report can be exercised without an API key."""

    model = "mock"

    _SWAPS = [
        (r"\bMoreover, ", ""), (r"\bFurthermore, ", ""), (r"\bAdditionally, ", "Also, "),
        (r"\butilize\b", "use"), (r"\bleverage\b", "use"), (r"\bdemonstrates\b", "shows"),
        (r"\bplays a crucial role in\b", "matters for"), (r"\bIn conclusion, ", ""),
        (r"\bI have to\b", "I need to"), (r"\bthat is\b", "which is"),
        (r"此外，", ""), (r"具有重要意义", "很重要"), (r"综上所述，", ""),
    ]

    def __init__(self, drop_facts: bool = False):
        self.drop_facts = drop_facts   # simulate a bad model that loses numbers
        self.usage = Usage()

    def rewrite(self, system: str, user: str) -> str:
        m = re.search(r"<paragraph>\n?(.*?)\n?</paragraph>", user, re.S)
        text = m.group(1) if m else user
        for pat, rep in self._SWAPS:
            text = re.sub(pat, rep, text)
        if self.drop_facts:
            text = re.sub(r"\b\d{4}\b", "that year", text)
        self.usage.requests += 1
        return text
