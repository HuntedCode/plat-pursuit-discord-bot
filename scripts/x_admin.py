"""Admin CLI for X (Twitter) announcements. Run inside the bot container.

Usage:
    python scripts/x_admin.py test     # fire test announcement to admin/testing server
    python scripts/x_admin.py latest   # fire latest tweet to production channel and advance baseline

Reads API_KEY and PORT from the container environment (already set by Render).
Hits the bot's localhost FastAPI admin endpoints, no curl required.
"""
import os
import sys
import urllib.request
import urllib.error

VALID_ACTIONS = ('test', 'latest')


def main() -> int:
    if len(sys.argv) != 2 or sys.argv[1] not in VALID_ACTIONS:
        print(f"Usage: python {sys.argv[0]} {{{'|'.join(VALID_ACTIONS)}}}", file=sys.stderr)
        return 2

    action = sys.argv[1]
    api_key = os.getenv('API_KEY')
    port = os.getenv('PORT', '5000')

    if not api_key:
        print("ERROR: API_KEY env var not set", file=sys.stderr)
        return 1

    url = f"http://localhost:{port}/admin/x-announce/{action}"
    req = urllib.request.Request(
        url,
        method='POST',
        headers={'Authorization': f'Bearer {api_key}'},
    )

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = resp.read().decode('utf-8')
            print(f"[{resp.status}] {body}")
            return 0
    except urllib.error.HTTPError as e:
        body = e.read().decode('utf-8') if e.fp else ''
        print(f"[{e.code}] {body}", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"Request failed: {e}", file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
