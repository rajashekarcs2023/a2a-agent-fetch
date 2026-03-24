import json

from typing import Any

import httpx

from geopy.exc import GeocoderServiceError, GeocoderTimedOut
from geopy.geocoders import Nominatim
from mcp.server.fastmcp import FastMCP


mcp = FastMCP('weather')

BASE_URL = 'https://api.weather.gov'
USER_AGENT = 'weather-agent'
REQUEST_TIMEOUT = 20.0
GEOCODE_TIMEOUT = 10.0

http_client = httpx.AsyncClient(
    base_url=BASE_URL,
    headers={'User-Agent': USER_AGENT, 'Accept': 'application/geo+json'},
    timeout=REQUEST_TIMEOUT,
    follow_redirects=True,
)

geolocator = Nominatim(user_agent=USER_AGENT)


async def get_weather_response(endpoint: str) -> dict[str, Any] | None:
    try:
        response = await http_client.get(endpoint)
        response.raise_for_status()
        return response.json()
    except httpx.HTTPStatusError:
        return None
    except httpx.TimeoutException:
        return None
    except httpx.RequestError:
        return None
    except json.JSONDecodeError:
        return None
    except Exception:
        return None


def format_alert(feature: dict[str, Any]) -> str:
    props = feature.get('properties', {})
    return f"""
            Event: {props.get('event', 'Unknown Event')}
            Area: {props.get('areaDesc', 'N/A')}
            Severity: {props.get('severity', 'N/A')}
            Certainty: {props.get('certainty', 'N/A')}
            Urgency: {props.get('urgency', 'N/A')}
            Effective: {props.get('effective', 'N/A')}
            Expires: {props.get('expires', 'N/A')}
            Description: {props.get('description', 'No description provided.').strip()}
            Instructions: {props.get('instruction', 'No instructions provided.').strip()}
            """


def format_forecast_period(period: dict[str, Any]) -> str:
    return f"""
           {period.get('name', 'Unknown Period')}:
             Temperature: {period.get('temperature', 'N/A')}°{period.get('temperatureUnit', 'F')}
             Wind: {period.get('windSpeed', 'N/A')} {period.get('windDirection', 'N/A')}
             Short Forecast: {period.get('shortForecast', 'N/A')}
             Detailed Forecast: {period.get('detailedForecast', 'No detailed forecast            provided.').strip()}
           """


@mcp.tool()
async def get_alerts(state: str) -> str:
    """Get active weather alerts for a specific US state.

    Args:
        state: The two-letter US state code (e.g., CA, NY, TX). Case-insensitive.
    """
    if not isinstance(state, str) or len(state) != 2 or not state.isalpha():
        return 'Invalid input. Please provide a two-letter US state code (e.g., CA).'
    state_code = state.upper()

    endpoint = f'/alerts/active/area/{state_code}'
    data = await get_weather_response(endpoint)

    if data is None:
        return f'Failed to retrieve weather alerts for {state_code}.'

    features = data.get('features')
    if not features:
        return f'No active weather alerts found for {state_code}.'

    alerts = [format_alert(feature) for feature in features]
    return '\n---\n'.join(alerts)


@mcp.tool()
async def get_forecast(latitude: float, longitude: float) -> str:
    """Get the weather forecast for a specific location using latitude and longitude.

    Args:
        latitude: The latitude of the location (e.g., 34.05).
        longitude: The longitude of the location (e.g., -118.25).
    """
    if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
        return 'Invalid latitude or longitude provided. Latitude must be between -90 and 90, Longitude between -180 and 180.'

    point_endpoint = f'/points/{latitude:.4f},{longitude:.4f}'
    points_data = await get_weather_response(point_endpoint)

    if points_data is None or 'properties' not in points_data:
        return f'Unable to retrieve NWS gridpoint information for {latitude:.4f},{longitude:.4f}.'

    forecast_url = points_data['properties'].get('forecast')

    if not forecast_url:
        return f'Could not find the NWS forecast endpoint for {latitude:.4f},{longitude:.4f}.'

    forecast_data = None
    try:
        response = await http_client.get(forecast_url)
        response.raise_for_status()
        forecast_data = response.json()
    except httpx.HTTPStatusError:
        pass
    except httpx.RequestError:
        pass
    except json.JSONDecodeError:
        pass
    except Exception:
        pass

    if forecast_data is None or 'properties' not in forecast_data:
        return 'Failed to retrieve detailed forecast data from NWS.'

    periods = forecast_data['properties'].get('periods')
    if not periods:
        return 'No forecast periods found for this location from NWS.'

    forecasts = [format_forecast_period(period) for period in periods[:5]]

    return '\n---\n'.join(forecasts)


@mcp.tool()
async def get_forecast_by_city(city: str, state: str) -> str:
    """Get the weather forecast for a specific US city and state by first finding its coordinates.

    Args:
        city: The name of the city (e.g., "Los Angeles", "New York").
        state: The two-letter US state code (e.g., CA, NY). Case-insensitive.
    """
    if not city or not isinstance(city, str):
        return 'Invalid city name provided.'
    if (
        not state
        or not isinstance(state, str)
        or len(state) != 2
        or not state.isalpha()
    ):
        return 'Invalid state code. Please provide the two-letter US state abbreviation (e.g., CA).'

    city_name = city.strip()
    state_code = state.strip().upper()
    query = f'{city_name}, {state_code}, USA'

    location = None
    try:
        location = geolocator.geocode(query, timeout=GEOCODE_TIMEOUT)

    except GeocoderTimedOut:
        return f"Could not get coordinates for '{city_name}, {state_code}': The location service timed out."
    except GeocoderServiceError:
        return f"Could not get coordinates for '{city_name}, {state_code}': The location service returned an error."
    except Exception:
        return f"An unexpected error occurred while finding coordinates for '{city_name}, {state_code}'."

    if location is None:
        return f"Could not find coordinates for '{city_name}, {state_code}'. Please check the spelling or try a nearby city."

    latitude = location.latitude
    longitude = location.longitude

    return await get_forecast(latitude, longitude)


async def shutdown_event():
    await http_client.aclose()


if __name__ == '__main__':
    mcp.run(transport='stdio')
