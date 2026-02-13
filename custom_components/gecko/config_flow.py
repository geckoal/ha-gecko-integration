"""Config flow for Gecko."""

import logging
from typing import Any

from homeassistant.helpers import config_entry_oauth2_flow, aiohttp_client

from .const import DOMAIN, OAUTH2_AUTHORIZE, OAUTH2_CLIENT_ID, OAUTH2_TOKEN
from .oauth_implementation import GeckoPKCEOAuth2Implementation

_LOGGER = logging.getLogger(__name__)


class ConfigFlow(
    config_entry_oauth2_flow.AbstractOAuth2FlowHandler, domain=DOMAIN
):
    """Config flow to handle Gecko OAuth2 authentication."""

    DOMAIN = DOMAIN

    async def async_step_user(self, user_input=None):
        """Handle a flow initialized by the user."""
        # Register the hardcoded OAuth implementation if not already registered
        await self.async_register_implementation()
        return await super().async_step_user(user_input)

    async def async_register_implementation(self):
        """Register the OAuth implementation."""
        # Check if already registered to avoid duplicates
        implementations = await config_entry_oauth2_flow.async_get_implementations(
            self.hass, DOMAIN
        )
        if DOMAIN not in implementations:
            config_entry_oauth2_flow.async_register_implementation(
                self.hass,
                DOMAIN,
                GeckoPKCEOAuth2Implementation(
                    self.hass,
                    DOMAIN,
                    client_id=OAUTH2_CLIENT_ID,
                    authorize_url=OAUTH2_AUTHORIZE,
                    token_url=OAUTH2_TOKEN,
                ),
            )

    async def async_oauth_create_entry(self, data: dict):
        """Create an entry after OAuth authentication."""
        # Get available vessels from the cloud API
        try:
            # Create a simple API client using just the access token for initial API calls
            from .api import ConfigFlowGeckoApi
            api_client = ConfigFlowGeckoApi(
                self.hass,
                data["token"]["access_token"]
            )

            # Get user ID and account information
            user_id, account_data, account_id = await self._resolve_user_and_account(data, api_client)

            # Get vessels for the account
            vessels = await api_client.async_get_vessels(account_id)

            if not vessels:
                self.logger.warning("No vessels found for account %s", account_id)
                return self.async_create_entry(
                    title=f"Gecko - {account_data.get('name', 'Account')}",
                    data={
                        **data,
                        "vessels": [],
                        "account_id": account_id,
                        "user_id": user_id,
                        "account_info": account_data
                    }
                )

            # Fetch spa configuration for each vessel
            vessels_with_config = []
            for vessel in vessels:
                try:
                    monitor_id = vessel.get("monitorId") or vessel.get("vesselId")
                    if monitor_id:
                        spa_config = await api_client.async_get_spa_configuration(account_id, str(monitor_id))
                        vessel_with_config = {
                            **vessel,
                            "spa_configuration": spa_config
                        }
                        vessels_with_config.append(vessel_with_config)
                    else:
                        _LOGGER.warning("No monitor ID found for vessel %s", vessel.get("name"))
                        vessels_with_config.append(vessel)  # Add without config
                except Exception as config_err:
                    _LOGGER.warning("Failed to get spa config for vessel %s: %s", vessel.get("name"), config_err)
                    vessels_with_config.append(vessel)  # Add without config

            # Create one main entry for the account with all vessels and their configurations
            return self.async_create_entry(
                title=f"Gecko - {account_data.get('name', 'Account')} ({len(vessels_with_config)} vessels)",
                data={
                    **data,
                    "vessels": vessels_with_config,
                    "account_id": account_id,
                    "user_id": user_id,
                    "account_info": account_data
                }
            )
        except Exception as err:
            self.logger.error("Failed to get vessels from Gecko API: %s", err)
            return self.async_abort(reason="api_error")

    async def _resolve_user_and_account(self, data: dict, api_client) -> tuple[str, dict, str]:
        """Resolve user ID and account information."""
        try:
            # Step 1: Get user ID from Auth0 userinfo endpoint
            user_id = await api_client.async_get_user_id()

            # Step 2: Call our own API's /v2/user/:userId endpoint to get account information
            user_data = await api_client.async_get_user_info(user_id)

            account_data = user_data.get("account", {})
            account_id = str(account_data.get("accountId", ""))

            if not account_id:
                raise ValueError("No account ID found in user data")

            return user_id, account_data, account_id

        except Exception as err:
            raise ConnectionError(f"Failed to resolve user and account: {err}") from err

    async def _get_user_id_from_api(self, api_client) -> str | None:
        """Try to get user ID from API calls."""
        # First, try to extract user ID directly from the JWT token
        user_id = api_client.extract_user_id_from_token()
        if user_id:
            return user_id

        # If token extraction fails, try OAuth userinfo endpoint
        try:
            userinfo = await api_client.async_get_oauth_userinfo()
            return userinfo.get("sub")
        except Exception:
            return None

    def _extract_user_id_from_token(self, token: dict[str, Any]) -> str | None:
        """Extract user ID from the OAuth token."""
        # Try direct fields in token first
        for field in ['user_id', 'userId', 'uid', 'id', 'sub']:
            if field in token:
                return str(token[field])

        # Check user_info nested object
        user_info = token.get("user_info", {})
        if isinstance(user_info, dict):
            for field in ['user_id', 'id', 'userId', 'uid']:
                if field in user_info:
                    return str(user_info[field])

        return None

    @property
    def logger(self) -> logging.Logger:
        """Return logger."""
        return logging.getLogger(__name__)


ConfigFlow.version = 1
