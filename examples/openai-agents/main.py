"""OpenAI Agents SDK + SAFER demo.

A four-agent setup driven by the official `openai-agents` framework.
The `triage_agent` decides which specialist agent to hand off to;
specialists have their own tools. SAFER instruments the entire run via
`install_safer_for_agents(...)` — both `RunHooks` (per-call lifecycle)
and the global `TracingProcessor` (span-level telemetry) are
registered.

Specialists:
  * math_specialist  — add, multiply, sqrt
  * geo_specialist   — country_capital, country_population, distance_km
  * weather_specialist — current_weather, forecast (real wttr.in HTTP)

What this exercises (no manual SAFER calls beyond the two-line install):
  * `on_agent_start` / `on_agent_end` → SAFER session_start / final_output
  * `on_handoff` → SAFER on_agent_decision(decision_type="handoff")
  * `on_llm_start` / `on_llm_end` → SAFER before/after_llm_call
  * `on_tool_start` / `on_tool_end` → SAFER before/after_tool_use

Default mode is an interactive chat REPL. Pass `--prompt "..."` to run
one shot and exit.

Requirements:
    pip install openai-agents safer-sdk
    export OPENAI_API_KEY=...
    export SAFER_API_URL=http://localhost:8000

Run:
    python examples/openai-agents/main.py                 # interactive
    python examples/openai-agents/main.py --prompt "..."  # one-shot
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import math
import os
import sys
from pathlib import Path

import httpx

# Allow `from _chat import run_repl` even though we run this file directly.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from _chat import run_repl  # noqa: E402

logging.basicConfig(level=logging.INFO)


# ---------- offline reference data (small + stable) ----------

_CAPITALS = {
    "france": "Paris",
    "turkey": "Ankara",
    "germany": "Berlin",
    "japan": "Tokyo",
    "brazil": "Brasília",
    "spain": "Madrid",
    "italy": "Rome",
    "egypt": "Cairo",
    "kenya": "Nairobi",
    "argentina": "Buenos Aires",
    "australia": "Canberra",
    "canada": "Ottawa",
    "india": "New Delhi",
    "south korea": "Seoul",
    "mexico": "Mexico City",
}

# Approximate population (millions) — for demo purposes only.
_POPULATIONS = {
    "france": 68.0,
    "turkey": 85.4,
    "germany": 84.5,
    "japan": 124.6,
    "brazil": 215.3,
    "spain": 47.6,
    "italy": 58.9,
    "egypt": 109.3,
    "kenya": 55.1,
    "argentina": 46.0,
    "australia": 26.0,
    "canada": 39.0,
    "india": 1428.6,
    "south korea": 51.7,
    "mexico": 128.5,
}

# (lat, lon) pairs for capital cities — used by `distance_km`.
_CITY_COORDS = {
    "paris":         (48.8566,   2.3522),
    "ankara":        (39.9334,  32.8597),
    "berlin":        (52.5200,  13.4050),
    "tokyo":         (35.6762, 139.6503),
    "brasília":      (-15.7939, -47.8828),
    "madrid":        (40.4168,  -3.7038),
    "rome":          (41.9028,  12.4964),
    "cairo":         (30.0444,  31.2357),
    "nairobi":       (-1.2921,  36.8219),
    "buenos aires":  (-34.6037, -58.3816),
    "canberra":      (-35.2809, 149.1300),
    "ottawa":        (45.4215, -75.6972),
    "new delhi":     (28.6139,  77.2090),
    "seoul":         (37.5665, 126.9780),
    "mexico city":   (19.4326, -99.1332),
}


def _haversine_km(a: tuple[float, float], b: tuple[float, float]) -> float:
    lat1, lon1 = map(math.radians, a)
    lat2, lon2 = map(math.radians, b)
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return round(2 * 6371.0088 * math.asin(math.sqrt(h)), 1)


# ---------- entry point ----------


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--prompt",
        default=None,
        help="Run a single prompt and exit instead of opening the REPL.",
    )
    args = ap.parse_args()

    if not os.environ.get("OPENAI_API_KEY"):
        sys.exit("OPENAI_API_KEY is required to run this example.")

    # Lazy import — keeps `python -c 'import main'` cheap if the user
    # hasn't installed the agents SDK yet.
    from agents import Agent, Runner, function_tool
    from safer.adapters.openai_agents import install_safer_for_agents

    # --- Math tools --------------------------------------------------

    @function_tool
    def add(a: float, b: float) -> str:
        """Add two numbers and return the result as a string."""
        return f"{a + b}"

    @function_tool
    def multiply(a: float, b: float) -> str:
        """Multiply two numbers and return the result as a string."""
        return f"{a * b}"

    @function_tool
    def sqrt(x: float) -> str:
        """Square root of a non-negative number."""
        if x < 0:
            return "error: sqrt of negative number is undefined"
        return f"{math.sqrt(x):.6g}"

    # --- Geography tools ---------------------------------------------

    @function_tool
    def country_capital(country: str) -> str:
        """Return the capital of the given country (offline lookup)."""
        return _CAPITALS.get(country.strip().lower(), f"unknown country: {country!r}")

    @function_tool
    def country_population(country: str) -> str:
        """Return an approximate population (millions) for the given country."""
        pop = _POPULATIONS.get(country.strip().lower())
        if pop is None:
            return f"unknown country: {country!r}"
        return f"~{pop} million"

    @function_tool
    def distance_km(city_a: str, city_b: str) -> str:
        """Approximate great-circle distance (km) between two known cities."""
        a = _CITY_COORDS.get(city_a.strip().lower())
        b = _CITY_COORDS.get(city_b.strip().lower())
        if a is None or b is None:
            missing = city_a if a is None else city_b
            return f"unknown city: {missing!r}"
        return f"{_haversine_km(a, b)} km"

    # --- Weather tools (real HTTP via wttr.in) ------------------------

    @function_tool
    def current_weather(city: str) -> str:
        """Get current weather for a city (real HTTP via wttr.in)."""
        try:
            resp = httpx.get(
                f"https://wttr.in/{city}",
                params={"format": "%C %t %w %h"},
                timeout=10.0,
                headers={"User-Agent": "safer-agents-demo/1.0"},
            )
        except httpx.RequestError as e:
            return f"network error: {e}"
        if resp.status_code >= 400:
            return f"HTTP {resp.status_code}"
        return resp.text.strip() or "(empty response)"

    @function_tool
    def forecast(city: str, days: int = 3) -> str:
        """Get a multi-day forecast headline for a city (wttr.in)."""
        days = max(1, min(days, 3))
        try:
            resp = httpx.get(
                f"https://wttr.in/{city}",
                params={"format": "j1"},
                timeout=10.0,
                headers={"User-Agent": "safer-agents-demo/1.0"},
            )
        except httpx.RequestError as e:
            return f"network error: {e}"
        if resp.status_code >= 400:
            return f"HTTP {resp.status_code}"
        try:
            data = resp.json()
            entries = data.get("weather", [])[:days]
        except ValueError:
            return "(unexpected response shape)"
        rows = [
            f"{e.get('date')}: max {e.get('maxtempC')}°C / "
            f"min {e.get('mintempC')}°C — "
            f"{(e.get('hourly') or [{}])[0].get('weatherDesc', [{}])[0].get('value', '')}"
            for e in entries
        ]
        return "\n".join(rows) or "(no forecast)"

    # --- Specialist agents -------------------------------------------

    math_agent = Agent(
        name="math_specialist",
        instructions=(
            "You answer arithmetic questions concisely. Use `add`, "
            "`multiply`, or `sqrt` as appropriate. Reply with just the "
            "result."
        ),
        tools=[add, multiply, sqrt],
    )

    geo_agent = Agent(
        name="geo_specialist",
        instructions=(
            "You answer geography questions concisely. Use "
            "`country_capital`, `country_population`, or `distance_km` "
            "as appropriate. Reply with just the result."
        ),
        tools=[country_capital, country_population, distance_km],
    )

    weather_agent = Agent(
        name="weather_specialist",
        instructions=(
            "You answer weather questions concisely. Use "
            "`current_weather` for the current conditions and "
            "`forecast` for upcoming days. Reply with just the result."
        ),
        tools=[current_weather, forecast],
    )

    # --- Triage / orchestrator ---------------------------------------

    triage = Agent(
        name="triage",
        instructions=(
            "You route the user's message to the right specialist:\n"
            "- arithmetic / numeric questions → math_specialist\n"
            "- country capitals / population / distance → geo_specialist\n"
            "- current weather or forecasts → weather_specialist\n"
            "If the user asks several things at once, hand off once per "
            "question. If a question is genuinely off-topic for all "
            "specialists, answer it directly in one sentence."
        ),
        handoffs=[math_agent, geo_agent, weather_agent],
    )

    # --- SAFER integration: ONE install line + per-run hooks ---------
    hooks = install_safer_for_agents(
        agent_id="agents_sdk_demo",
        agent_name="OpenAI Agents Demo",
    )
    # -----------------------------------------------------------------

    async def turn(user_message: str) -> str:
        result = await Runner.run(triage, user_message, hooks=hooks)
        return str(result.final_output).strip()

    if args.prompt:
        print(asyncio.run(turn(args.prompt)))
        return

    def ask(user_message: str) -> str:
        return asyncio.run(turn(user_message))

    run_repl(
        ask,
        banner=(
            "SAFER OpenAI Agents demo — multi-specialist triage "
            "(math / geography / weather)."
        ),
    )


if __name__ == "__main__":
    main()
