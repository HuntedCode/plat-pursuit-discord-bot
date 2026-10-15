"""What a user is told when a PlatPursuit API call does not succeed.

PlatPursuit answers bot requests with messages written for people ("No linked profile found.",
"Discord ID already linked to a PSN profile."), in one of several shapes: `message`, `error`,
DRF's `detail`, or serializer errors (`non_field_errors`, or a list per field). Cogs used to
read one shape, or none, and fall back to "API error. Please try again later." for everything
else. `api_error_message` reads them all, and decides when the server's words must not be shown:
a 500's text is never useful, an authentication failure is the bot's problem rather than the
user's, and a rate limit comes back as an HTML page.
"""
import json
import logging

import aiohttp

logger = logging.getLogger('psn_api')

RATE_LIMITED_MESSAGE = "You're doing that too often. Please wait a minute and try again."
UNAVAILABLE_MESSAGE = 'PlatPursuit is having trouble right now. Please try again in a few minutes.'
BOT_ACCESS_MESSAGE = "The bot can't talk to PlatPursuit right now. Please let a moderator know."
REQUEST_FAILED_MESSAGE = "PlatPursuit couldn't do that. Please try again, or ask a moderator if it keeps happening."
#: Server text longer than this is cut, so a surprise payload cannot flood a reply.
MAX_MESSAGE_LENGTH = 300
#: How much of an error body goes into the log line.
LOGGED_BODY_LENGTH = 500


def message_from_body(data, *, allow_detail: bool = True) -> str | None:
    """The human-readable message in a PlatPursuit error body, whichever shape it takes."""
    if isinstance(data, list):
        return _first_text(data)
    if not isinstance(data, dict):
        return None
    for key in ('message', 'error'):
        if text := _text(data.get(key)):
            return text
    if text := _first_text(data.get('non_field_errors')):
        return text
    for key, value in data.items():  # field errors: {'psn_username': ['PSN username must be ...']}
        if key not in ('detail', 'non_field_errors') and (text := _first_text(value)):
            return text
    if allow_detail:
        return _text(data.get('detail'))
    return None


def _text(value) -> str | None:
    if isinstance(value, str) and value.strip():
        text = value.strip()
        return text if len(text) <= MAX_MESSAGE_LENGTH else text[:MAX_MESSAGE_LENGTH - 3].rstrip() + '...'
    return None


def _first_text(value) -> str | None:
    if isinstance(value, list) and value:
        return _text(value[0])
    return None


async def api_error_message(resp: aiohttp.ClientResponse, *, action: str) -> str:
    """The message for a non-success response from PlatPursuit, logged with its status and body.

    `action` names the call for the log line, for example "refresh" or "generate-code".
    """
    status = resp.status
    try:
        body = await resp.text()
    except (aiohttp.ClientError, UnicodeDecodeError):
        body = ''
    try:
        data = json.loads(body) if body else None
    except ValueError:
        data = None

    if status in (401, 403) and message_from_body(data, allow_detail=False) is None:
        # Only DRF's own `detail` (a bad or missing API key, a missing permission): the bot's
        # problem, not the user's. A 403 that carries a message (a private PSN profile) is shown.
        logger.error(f"PlatPursuit refused the bot on {action} ({status}): {body[:LOGGED_BODY_LENGTH]!r}")
        return BOT_ACCESS_MESSAGE
    logger.warning(f"PlatPursuit API {status} on {action}: {body[:LOGGED_BODY_LENGTH]!r}")
    if status == 429:
        return RATE_LIMITED_MESSAGE
    if status == 500:
        return UNAVAILABLE_MESSAGE  # PlatPursuit's 500s say "Internal error.", which helps nobody
    if status > 500:
        # PlatPursuit's own 502 and 503 carry readable text ("PSN is currently unavailable. Try
        # again later."); a proxy's error page does not, and gets the general message.
        return message_from_body(data, allow_detail=False) or UNAVAILABLE_MESSAGE
    return message_from_body(data) or REQUEST_FAILED_MESSAGE
