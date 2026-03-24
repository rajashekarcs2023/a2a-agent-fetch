import logging
import os
from urllib.parse import urlparse, urlunparse

import click
import uvicorn

from a2a.server.apps import A2AStarletteApplication
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.tasks import InMemoryTaskStore
from a2a.types import (
    AgentCapabilities,
    AgentCard,
    AgentSkill,
)
from dotenv import load_dotenv, find_dotenv
from google.adk.artifacts import InMemoryArtifactService
from google.adk.memory.in_memory_memory_service import InMemoryMemoryService
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from uagents_core.adapters.a2a import agentverse_sdk

from simple_a2a_agent.executor import PlannerExecutor
from simple_a2a_agent.planner_agent import create_planner_agent


load_dotenv(find_dotenv(usecwd=True))

logging.basicConfig(level=logging.INFO)

DEFAULT_HOST = '0.0.0.0'
DEFAULT_PORT = 10000


def _require_auth_env() -> None:
    if os.getenv('GOOGLE_GENAI_USE_VERTEXAI') == 'TRUE':
        return
    if not os.getenv('GOOGLE_API_KEY'):
        raise ValueError(
            'Set GOOGLE_API_KEY in the environment (or copy example.env to .env), '
            'or set GOOGLE_GENAI_USE_VERTEXAI=TRUE with Vertex credentials.'
        )


def _coerce_client_url(url: str, fallback_port: int) -> str:
    """0.0.0.0 is a bind address; clients must use 127.0.0.1 or a public URL."""
    parsed = urlparse(url.strip())
    if parsed.hostname in (None, '0.0.0.0', '::', '[::]'):
        p = fallback_port if parsed.port is None else parsed.port
        scheme = parsed.scheme or 'http'
        parsed = parsed._replace(scheme=scheme, netloc=f'127.0.0.1:{p}')
    return urlunparse(parsed).rstrip('/')


def _public_base_url(host: str, port: int) -> str:
    """URL advertised in the agent card (must be reachable by API clients)."""
    explicit = os.environ.get('APP_URL', '').strip()
    if explicit:
        return _coerce_client_url(explicit, port)
    if host in ('0.0.0.0', '::', '[::]'):
        return f'http://127.0.0.1:{port}'
    return _coerce_client_url(f'http://{host}:{port}', port)


def build_agent_card(host: str, port: int) -> AgentCard:
    app_url = _public_base_url(host, port)
    skill = AgentSkill(
        id='trip_planner',
        name='Trip planner',
        description='US weather (NWS) and Airbnb search in one agent',
        tags=['weather', 'airbnb', 'travel'],
        examples=[
            'Weather in Los Angeles, CA this week',
            'Find an Airbnb in Austin, TX for two adults, June 10–15',
        ],
    )
    return AgentCard(
        name='Trip Planner Agent',
        description='Single A2A agent: weather + Airbnb via MCP',
        url=app_url,
        version='1.0.0',
        default_input_modes=['text'],
        default_output_modes=['text'],
        capabilities=AgentCapabilities(streaming=True),
        skills=[skill],
    )


def main(host: str = DEFAULT_HOST, port: int = DEFAULT_PORT) -> None:
    # Agentverse: set AGENTVERSE_SDK_INIT_URL in .env (full URL from Launch UI). Do not commit secrets.
    _av = os.environ.get('AGENTVERSE_SDK_INIT_URL', '').strip()
    if _av:
        agentverse_sdk.init(_av)
    _require_auth_env()

    agent_card = build_agent_card(host, port)
    adk_agent = create_planner_agent()
    runner = Runner(
        app_name=agent_card.name,
        agent=adk_agent,
        artifact_service=InMemoryArtifactService(),
        session_service=InMemorySessionService(),
        memory_service=InMemoryMemoryService(),
    )
    agent_executor = PlannerExecutor(runner, agent_card)

    request_handler = DefaultRequestHandler(
        agent_executor=agent_executor, task_store=InMemoryTaskStore()
    )

    a2a_app = A2AStarletteApplication(
        agent_card=agent_card, http_handler=request_handler
    )

    # Behind ngrok or another reverse proxy, set APP_URL to the public https URL and
    # TRUST_PROXY=1 so forwarded headers are honored when the stack needs them.
    trust = os.getenv('TRUST_PROXY', '').lower() in ('1', 'true', 'yes')
    kwargs: dict = {'host': host, 'port': port}
    if trust:
        kwargs['proxy_headers'] = True
        kwargs['forwarded_allow_ips'] = '*'
    uvicorn.run(a2a_app.build(), **kwargs)


@click.command()
@click.option('--host', 'host', default=DEFAULT_HOST)
@click.option('--port', 'port', default=DEFAULT_PORT, type=int)
def cli(host: str, port: int) -> None:
    main(host, port)


if __name__ == '__main__':
    cli()
