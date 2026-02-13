
import logging

from homeassistant.helpers import config_entry_oauth2_flow
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import API_BASE_URL, AUTH0_URL_BASE
from gecko_iot_client import GeckoApiClient
from typing import Any

_LOGGER = logging.getLogger(__name__)

class OAuthGeckoApi(GeckoApiClient):
    """Provide gecko authentication tied to an OAuth2 based config entry."""

    def __init__(
        self,
        hass: HomeAssistant,
        oauth_session: config_entry_oauth2_flow.OAuth2Session,
    ) -> None:
        """Initialize OAuthGeckoApi."""
        websession = async_get_clientsession(hass)
        super().__init__(websession,
                         api_url=API_BASE_URL,
                         auth0_url=AUTH0_URL_BASE)
        self._oauth_session = oauth_session

    async def async_get_access_token(self) -> str:
        """Return a valid access token for the Gecko API."""
        await self._oauth_session.async_ensure_token_valid()
        return self._oauth_session.token["access_token"]

class ConfigFlowGeckoApi(GeckoApiClient):
    """Profile gecko authentication before a ConfigEntry exists.

    This implementation directly provides the token without supporting refresh.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        token: str,
    ) -> None:
        """Initialize ConfigFlowGeckoApi."""
        websession = async_get_clientsession(hass)
        super().__init__(websession,
                         api_url=API_BASE_URL,
                         auth0_url=AUTH0_URL_BASE)
        self._token = token

    async def async_get_access_token(self) -> str:
        """Return the access token for the Gecko API."""
        return self._token
