import re

import httpx
from pydantic_ai import Agent

from demo_core.models import get_model
from demo_core.settings import GatewaySettings

_TAG_RE = re.compile(r"<[^>]+>")
_WHITESPACE_RE = re.compile(r"\s+")
_MAX_FETCHED_CHARS = 5000


def _condition_reference_url(condition_name: str) -> str:
    """Wikipedia's URL pattern is a reliable, deterministic way to look up a condition by
    name — unlike e.g. Mayo Clinic, which sits behind an Akamai WAF that 403s any plain HTTP
    client regardless of URL correctness, Wikipedia returns real content to a plain httpx
    request and tolerates most casing/spacing via redirects. Built here in code rather than
    left to the model, so it can't mangle the slug."""
    return f"https://en.wikipedia.org/wiki/{condition_name.strip().replace(' ', '_')}"


async def _fetch_url(url: str) -> str:
    try:
        async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
            response = await client.get(url)
            response.raise_for_status()
    except httpx.HTTPError as exc:
        return f"Could not fetch {url}: {exc}"

    text = _WHITESPACE_RE.sub(" ", _TAG_RE.sub(" ", response.text)).strip()
    return text[:_MAX_FETCHED_CHARS]


def build_web_research_agent(settings: GatewaySettings, model_choice: tuple[str, str]) -> Agent:
    """A dedicated research agent the main rx-assistant agent can delegate to (see
    rx_assistant.agent.build_agent's SubAgents wiring) for a known source — a medication's own
    med_url, or a condition looked up by name — that the local database and the main agent's
    own knowledge don't cover.

    Deliberately narrow: its two tools each make exactly one fetch, so a delegation is always
    one call plus a summary — it does not go searching elsewhere on the web."""
    agent = Agent(
        get_model(*model_choice, settings),
        name="rx_assistant_web_research_agent",
        instructions=(
            "You look up one known source and summarize it for a pharmacy assistant. For a "
            "medication, call fetch_page with its exact URL. For a condition, call "
            "fetch_condition_reference with just the condition's name — it looks up the "
            "right page for you. Call exactly one of these, once, then summarize the "
            "relevant parts concisely. Do not search elsewhere or use outside knowledge to "
            "fill gaps; if the page doesn't cover something, say so."
        ),
    )

    @agent.tool_plain
    async def fetch_page(url: str) -> str:
        """Fetch and return the text content of a specific URL, e.g. a medication's product
        page (its med_url)."""
        return await _fetch_url(url)

    @agent.tool_plain
    async def fetch_condition_reference(condition_name: str) -> str:
        """Fetch and return the text content of a reference page for a medical condition,
        looked up by name."""
        return await _fetch_url(_condition_reference_url(condition_name))

    return agent
