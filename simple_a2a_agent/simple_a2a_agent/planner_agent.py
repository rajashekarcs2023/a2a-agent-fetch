import os
from pathlib import Path

from google.adk.agents import LlmAgent
from google.adk.models.lite_llm import LiteLlm
from google.adk.tools.mcp_tool.mcp_toolset import (
    MCPToolset,
    StdioServerParameters,
)

_PKG_DIR = Path(__file__).resolve().parent
_WEATHER_MCP = str(_PKG_DIR / 'weather_mcp.py')


def create_planner_agent() -> LlmAgent:
    """One LLM agent with weather (local Python MCP) and Airbnb (npx MCP) tools."""
    # Use explicit `gemini/...` so LiteLLM uses Google AI Studio + GOOGLE_API_KEY.
    # A bare name like `gemini-2.5-flash` often routes to Vertex and needs ADC.
    litellm_model = os.getenv('LITELLM_MODEL', 'gemini/gemini-2.5-flash')
    return LlmAgent(
        model=LiteLlm(model=litellm_model),
        name='trip_planner',
        description='Answers US weather and Airbnb accommodation questions using tools only.',
        instruction="""You are a trip-planning assistant with two tool sources:

1) **Weather (US)**: Use weather tools for forecasts and alerts. Prefer `get_forecast_by_city`
   when the user names a US city and state. Weather data comes from api.weather.gov (US locations).

2) **Airbnb**: Use Airbnb MCP tools only for lodging search and listing details. Never invent
   listings, prices, or URLs—only report what tools return. Include markdown links from tool output.

If the user asks for both weather and lodging, call the relevant tools for each part and combine
the results clearly. If a request is outside tool capabilities, say so briefly.""",
        tools=[
            MCPToolset(
                connection_params=StdioServerParameters(
                    command='python',
                    args=[_WEATHER_MCP],
                ),
            ),
            MCPToolset(
                connection_params=StdioServerParameters(
                    command='npx',
                    args=['-y', '@openbnb/mcp-server-airbnb', '--ignore-robots-txt'],
                ),
            ),
        ],
    )
