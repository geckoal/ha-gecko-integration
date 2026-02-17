"""OAuth2 implementation for the Gecko integration.

This module provides a PKCE-based OAuth2 implementation with a hardcoded
public Client ID. PKCE (Proof Key for Code Exchange) uses cryptographic
code challenges instead of a static client secret, making it secure even
with a public Client ID.

No Application Credentials setup is required - the integration works out of the box!
"""

import logging

from homeassistant.helpers import config_entry_oauth2_flow

_LOGGER = logging.getLogger(__name__)


class GeckoPKCEOAuth2Implementation(config_entry_oauth2_flow.LocalOAuth2ImplementationWithPkce):
    """Gecko OAuth2 implementation with PKCE (no client secret required)."""

    @property
    def extra_authorize_data(self) -> dict:
        """Extra data for the authorize URL."""
        data = super().extra_authorize_data  # This includes code_challenge and code_challenge_method
        data.update({
            # offline_access is REQUIRED to receive a refresh_token from Auth0
            # Without it, only an access_token is returned which expires and cannot be renewed
            "scope": "openid profile email offline_access",
            "audience": "https://api.geckowatermonitor.com"
        })
        return data

    async def async_refresh_token(self, token: dict) -> dict:
        """Refresh tokens, preserving the refresh_token if the server omits it.

        Auth0 (and some other OAuth2 providers) may not return a new
        refresh_token in the refresh response when the existing one is still
        valid. The HA core OAuth2 helpers replace the stored token wholesale
        with the response, which drops the refresh_token key. On the next
        refresh cycle the integration crashes with KeyError: 'refresh_token'.

        This override ensures the original refresh_token is carried forward
        when the server does not issue a replacement.
        """
        existing_refresh_token = token.get("refresh_token")

        new_token = await super().async_refresh_token(token)

        if "refresh_token" not in new_token and existing_refresh_token:
            _LOGGER.debug(
                "Auth0 refresh response did not include a new refresh_token; "
                "carrying forward the existing one"
            )
            new_token["refresh_token"] = existing_refresh_token

        return new_token
