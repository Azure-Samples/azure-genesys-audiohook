"""Auth utilities for the server."""

import os
from functools import wraps

from quart import request


def require_api_key(func):
    @wraps(func)
    async def wrapper(*args, **kwargs):
        api_key = os.getenv("WEBSOCKET_SERVER_API_KEY")
        header_key = request.headers.get("X-Api-Key")
        param_key = request.args.get("key")
        if api_key and (header_key == api_key or param_key == api_key):
            return await func(*args, **kwargs)
        return {
            "error": {"code": "unauthorized", "message": "Invalid or missing API key."}
        }, 401

    return wrapper


def validate_signature(headers):
    # TODO: implement signature validation logic
    pass
