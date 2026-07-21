"""
Reads/writes the cached MahaRERA guest access token so a session solved once
(by a human, via a CAPTCHA in a real browser window -- see session_auth.py)
doesn't need to be re-solved for every project looked up within its ~100
minute lifetime.

Also usable directly from the command line:
    python token_cache.py get             # prints the token if still fresh, else prints nothing, exit 1
    python token_cache.py set <token>      # saves the token with the current timestamp
    python token_cache.py minutes-left     # prints how many minutes of freshness remain (0 if none)
"""
import json
import os
import sys
from datetime import datetime, timedelta

MAX_AGE_MINUTES = 90  # kept a bit under the real ~100 min token lifetime as a safety margin


def _cache_path() -> str:
    return os.path.normpath(os.path.join(os.path.dirname(__file__), ".guest_token_cache.json"))


def load() -> dict | None:
    path = _cache_path()
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


def save(token: str) -> None:
    with open(_cache_path(), "w", encoding="utf-8") as f:
        json.dump({"token": token, "saved_at": datetime.now().isoformat()}, f)


def invalidate() -> None:
    """Deletes the cached token, forcing the next lookup to solve a fresh session."""
    path = _cache_path()
    if os.path.exists(path):
        os.remove(path)


def minutes_left() -> int:
    data = load()
    if not data:
        return 0
    try:
        saved_at = datetime.fromisoformat(data["saved_at"])
    except (KeyError, ValueError):
        return 0
    remaining = timedelta(minutes=MAX_AGE_MINUTES) - (datetime.now() - saved_at)
    return max(0, int(remaining.total_seconds() // 60))


def load_valid() -> str | None:
    """Returns the cached token if it's still fresh, else None."""
    data = load()
    if data and minutes_left() > 0:
        return data["token"]
    return None


def main():
    if len(sys.argv) < 2:
        print("usage: token_cache.py [get|set <token>|minutes-left]", file=sys.stderr)
        sys.exit(2)

    cmd = sys.argv[1]

    if cmd == "get":
        token = load_valid()
        if token:
            print(token)
            sys.exit(0)
        sys.exit(1)

    elif cmd == "set":
        if len(sys.argv) < 3:
            print("usage: token_cache.py set <token>", file=sys.stderr)
            sys.exit(2)
        save(sys.argv[2])
        print("saved")

    elif cmd == "minutes-left":
        print(minutes_left())

    else:
        print(f"unknown command: {cmd}", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
