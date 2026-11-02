"""OneBot v11 integration: HTTP outbound sending to NapCat.

This module currently owns only the outbound half of the integration. The
inbound HTTP server is added separately.
"""

from __future__ import annotations

import json
import socket
import urllib.error
import urllib.request


class OnebotAPIError(Exception):
    """A call to the OneBot HTTP API failed."""


def send_private_msg(
    api_base: str,
    access_token: str,
    user_id: int,
    text: str,
    timeout: float = 3.0,
) -> None:
    """Send one private text message through NapCat.

    Returns silently on success. On failure raises ``OnebotAPIError`` whose
    message contains a category (timeout/http/retcode/parse) and a short
    summary such as an HTTP status or retcode, never the token or message
    content.
    """
    url = api_base.rstrip("/") + "/send_private_msg"
    payload = json.dumps(
        {"user_id": user_id, "message": text, "auto_escape": True}
    ).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=payload,
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    try:
        with opener.open(request, timeout=timeout) as response:
            status = response.status
            body = response.read()
    except urllib.error.HTTPError as exc:
        raise OnebotAPIError(f"http: status {exc.code}") from exc
    except urllib.error.URLError as exc:
        if isinstance(exc.reason, (TimeoutError, socket.timeout)):
            raise OnebotAPIError(
                f"timeout: no response within {timeout:g}s"
            ) from exc
        raise OnebotAPIError(f"http: {type(exc.reason).__name__}") from exc
    except TimeoutError as exc:
        raise OnebotAPIError(
            f"timeout: no response within {timeout:g}s"
        ) from exc
    except OSError as exc:
        raise OnebotAPIError(f"http: {type(exc).__name__}") from exc

    if status != 200:
        raise OnebotAPIError(f"http: status {status}")

    try:
        data = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise OnebotAPIError("parse: invalid JSON response") from exc

    if not isinstance(data, dict):
        raise OnebotAPIError("parse: response is not an object")
    retcode = data.get("retcode")
    if isinstance(retcode, bool) or not isinstance(retcode, int):
        raise OnebotAPIError("parse: response has no integer retcode")
    if retcode != 0:
        raise OnebotAPIError(f"retcode: {retcode}")
