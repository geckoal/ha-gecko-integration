"""OAuth2 implementation for the Gecko integration.

This module provides a PKCE-based OAuth2 implementation with a hardcoded
public Client ID. PKCE (Proof Key for Code Exchange) uses cryptographic
code challenges instead of a static client secret, making it secure even
with a public Client ID.

No Application Credentials setup is required - the integration works out of the box!
"""

from homeassistant.helpers import config_entry_oauth2_flow


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
