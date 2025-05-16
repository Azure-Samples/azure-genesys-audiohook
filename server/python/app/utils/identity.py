"""Identity utilities for the server."""

import functools
import logging
import time

from azure.core.credentials import AccessToken
from azure.core.exceptions import ClientAuthenticationError
from azure.identity import DefaultAzureCredential
from azure.identity.aio import DefaultAzureCredential as DefaultAzureCredentialAsync

# Global token cache
_cached_access_token: AccessToken = None
logger = logging.getLogger(__name__)


# Cache the sync credential for the lifetime of the process.
@functools.lru_cache(maxsize=1)
def get_azure_credential() -> DefaultAzureCredential:
    """Get Azure credential."""
    return DefaultAzureCredential()


# Cache the async credential for the lifetime of the process.
@functools.lru_cache(maxsize=1)
def get_azure_credential_async() -> DefaultAzureCredentialAsync:
    """Get async Azure credential."""
    return DefaultAzureCredentialAsync()


# Cache the token until shortly before its expiration.
def get_access_token(
    scope: str = "https://cognitiveservices.azure.com/.default",
) -> AccessToken:
    """Get Microsoft Entra access token for scope."""
    global _cached_access_token
    # Check if cached token exists and is valid for more than 60 seconds.
    if (
        _cached_access_token is not None
        and time.time() < _cached_access_token.expires_on - 60
    ):
        return _cached_access_token

    try:
        logger.info(f"Getting access token for scope: {scope}")
        token_credential = get_azure_credential()
        token = token_credential.get_token(scope)
        _cached_access_token = token
        logger.info("Successfully obtained access token")
        return token
    except ClientAuthenticationError as e:
        logger.error(f"Authentication failed: {e}")
        raise
    except Exception as e:
        logger.error(f"Failed to get access token: {e}")
        raise


def get_speech_token(resource_id: str) -> str:
    """Create Azure Speech service token."""
    if not resource_id:
        raise ValueError("resource_id cannot be None or empty")
    logger.info(f"Creating speech token for resource: {resource_id}")
    access_token = get_access_token()
    # For Azure Speech we need to include the "aad#" prefix and the "#" (hash) separator between resource ID and Microsoft Entra access token.
    authorization_token = "aad#" + resource_id + "#" + access_token.token
    logger.info("Successfully created speech token")
    return authorization_token
