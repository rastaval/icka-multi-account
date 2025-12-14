#!/usr/bin/env python3
"""
icka.py

Python port of the Go "icka" keep-alive tool,
with support for multiple accounts and .env configuration,
PLUS batched logins to avoid hitting rate limits.

Config priority:
1) CLI flags
2) Environment variables (ICKA_*)
3) .env file in same directory (also ICKA_*)

New env knobs:
- ICKA_BATCH_SIZE            (default 5)
- ICKA_BATCH_SLEEP_SECONDS   (default 300)
"""

import argparse
import json
import logging
import os
import sys
import time
from typing import List, Tuple, Optional

import requests
import websocket  # pip install websocket-client

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36"
)

ENV_PREFIX = "ICKA_"


# ---------------------------------------------------------------------
# Tiny .env + env helpers
# ---------------------------------------------------------------------


def load_dotenv(path: str = ".env") -> None:
    """
    Minimal .env loader.
    - Only KEY=VALUE lines
    - Lines starting with # are ignored
    - Does NOT overwrite existing environment variables
    """
    if not os.path.isfile(path):
        return

    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip()
            # don't overwrite real env
            if key not in os.environ:
                os.environ[key] = value


def env_get(key: str, default: Optional[str] = None) -> Optional[str]:
    return os.getenv(ENV_PREFIX + key, default)


def env_bool(key: str, default: bool = False) -> bool:
    val = env_get(key)
    if val is None:
        return default
    return val.strip().lower() in ("1", "true", "yes", "on")


def env_int(key: str, default: int) -> int:
    val = env_get(key)
    if val is None:
        return default
    try:
        return int(val.strip())
    except ValueError:
        return default


# ---------------------------------------------------------------------
# HTTP + WebSocket helpers
# ---------------------------------------------------------------------


def http_request(
    method: str,
    url: str,
    form: Optional[dict] = None,
    headers: Optional[dict] = None,
) -> bytes:
    if headers is None:
        headers = {}

    data = form if form is not None else None

    resp = requests.request(method, url, data=data, headers=headers, timeout=20)
    resp.raise_for_status()
    return resp.content


def ws_client(host: str, path: str, user_agent: str) -> websocket.WebSocket:
    headers = {
        "Host": host,
        "Origin": "https://www.irccloud.com",
        "User-Agent": user_agent,
    }
    url = f"wss://{host}{path}"
    ws = websocket.create_connection(
        url, header=[f"{k}: {v}" for k, v in headers.items()]
    )
    return ws


# ---------------------------------------------------------------------
# :D auth flows
# ---------------------------------------------------------------------


def get_auth_token(user_agent: str) -> dict:
    body = http_request(
        "POST",
        "https://api-3.irccloud.com/chat/auth-formtoken",
        form=None,
        headers={"User-Agent": user_agent},
    )
    r = json.loads(body.decode("utf-8", errors="replace"))
    return r  # expects {"token": "...", "success": true}


def get_session(email: str, password: str, token: str, user_agent: str) -> dict:
    form = {
        "email": email,
        "password": password,
        "token": token,
    }
    body = http_request(
        "POST",
        "https://www.irccloud.com/chat/login",
        form=form,
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "User-Agent": user_agent,
            "X-Auth-FormToken": token,
        },
    )
    r = json.loads(body.decode("utf-8", errors="replace"))
    return r  # expects SessionResponse-like dict


def auth_websocket(
    session_cookie: str, host: str, path: str, user_agent: str
) -> bool:
    ws = ws_client(host, path, user_agent)
    try:
        auth_req = {
            "cookie": session_cookie,
            "_method": "auth",
            "_reqid": 1,
        }
        ws.send(json.dumps(auth_req))

        raw = ws.recv()
        r = json.loads(raw)
        success = bool(r.get("success", False))

        ws.close()
        return success
    finally:
        try:
            ws.close()
        except Exception:
            pass


def keep_alive(email: str, password: str, user_agent: str) -> None:
    log = logging.getLogger("icka")

    log.info("(%s) Getting auth token…", email)
    token_resp = get_auth_token(user_agent)
    if not token_resp.get("success"):
        raise RuntimeError("get auth token failed")

    token = token_resp.get("token")
    if not token:
        raise RuntimeError("auth token missing in response")

    log.info("(%s) Logging in…", email)
    session_resp = get_session(email, password, token, user_agent)
    if not session_resp.get("success"):
        raise RuntimeError("get session failed, check email and password")

    ws_host = session_resp["websocket_host"]
    ws_path = session_resp["websocket_path"] + "?exclude_archives=1"
    session_cookie = session_resp["session"]

    log.info("(%s) Authenticating via WebSocket %s%s …", email, ws_host, ws_path)
    ok = auth_websocket(session_cookie, ws_host, ws_path, user_agent)
    if not ok:
        raise RuntimeError("auth websocket request failed")

    log.info("(%s) Successfully kept connection alive", email)


# ---------------------------------------------------------------------
# Multi-account handling
# ---------------------------------------------------------------------


def load_accounts(
    accounts_file: Optional[str],
    email: Optional[str],
    password: Optional[str],
) -> List[Tuple[str, str]]:
    accounts: List[Tuple[str, str]] = []

    if accounts_file:
        if not os.path.isfile(accounts_file):
            raise SystemExit(f"accounts file not found: {accounts_file}")

        with open(accounts_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if ":" in line:
                    em, pw = line.split(":", 1)
                elif "," in line:
                    em, pw = line.split(",", 1)
                else:
                    raise SystemExit(
                        "invalid line in accounts file "
                        f"(expected email:password): {line}"
                    )
                accounts.append((em.strip(), pw.strip()))
    elif email and password:
        accounts.append((email, password))

    if not accounts:
        raise SystemExit(
            "No accounts configured. "
            "Use --email/--password or ICKA_EMAIL/ICKA_PASSWORD "
            "or ICKA_ACCOUNTS_FILE"
        )

    return accounts


def parse_duration_to_seconds(s: str) -> float:
    """
    Very small Go-like duration parser.
    Supports: '90s', '5m', '1.5h', '2h30m', '1h30m', '1h15m30s', '2h', etc.
    """
    import re

    pattern = re.compile(r"(\d+(?:\.\d+)?)([smhd])")
    total = 0.0
    for amount, unit in pattern.findall(s):
        val = float(amount)
        if unit == "s":
            total += val
        elif unit == "m":
            total += val * 60
        elif unit == "h":
            total += val * 3600
        elif unit == "d":
            total += val * 86400

    if total == 0.0:
        raise ValueError(f"Could not parse duration: {s}")
    return total


# ---------------------------------------------------------------------
# Batched processing to avoid 429
# ---------------------------------------------------------------------


def run_accounts_batched(
    accounts: List[Tuple[str, str]],
    user_agent: str,
    batch_size: int,
    batch_sleep_seconds: int,
) -> None:
    log = logging.getLogger("icka")

    total = len(accounts)
    if total == 0:
        return

    if batch_size <= 0:
        batch_size = total

    log.info(
        "Starting batched processing: %d accounts, batch_size=%d, batch_sleep=%ds",
        total,
        batch_size,
        batch_sleep_seconds,
    )

    batch_index = 0
    for i in range(0, total, batch_size):
        batch_index += 1
        batch = accounts[i : i + batch_size]
        start_idx = i + 1
        end_idx = i + len(batch)

        log.info(
            "Batch %d: accounts %d–%d of %d (size=%d)",
            batch_index,
            start_idx,
            end_idx,
            total,
            len(batch),
        )

        for em, pw in batch:
            try:
                keep_alive(em, pw, user_agent)
            except Exception as e:
                log.error("(%s) keep-alive error: %s", em, e)

        # Sleep before next batch, but not after the last
        if end_idx < total and batch_sleep_seconds > 0:
            log.info(
                "Batch %d done (%d/%d). Sleeping %d seconds before next batch…",
                batch_index,
                end_idx,
                total,
                batch_sleep_seconds,
            )
            time.sleep(batch_sleep_seconds)

    log.info("All batches completed (%d accounts).", total)


# ---------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------


def main() -> None:
    # 1) Load .env first so os.environ is ready
    load_dotenv(".env")

    parser = argparse.ArgumentParser(
        description="IRCCloud keep-alive (Python, multi-account, .env-aware, batched)"
    )
    parser.add_argument("--email", help="IRCCloud email (single-account mode)")
    parser.add_argument("--password", help="IRCCloud password (single-account mode)")
    parser.add_argument("--accounts-file", help="File with email:password per line")

    parser.add_argument(
        "--forever",
        action="store_true",
        help="Run forever; sleep between iterations",
    )
    parser.add_argument(
        "--sleep-interval",
        default=env_get("SLEEP_INTERVAL", "1h"),
        help="Sleep interval used in --forever mode (default: 1h, e.g. 90m, 1.5h)",
    )
    parser.add_argument(
        "--user-agent",
        default=env_get("USER_AGENT", DEFAULT_USER_AGENT),
        help="User-Agent string",
    )
    parser.add_argument(
        "--log-level",
        default=env_get("LOG_LEVEL", "INFO"),
        help="Logging level (DEBUG, INFO, WARNING, ERROR)",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=env_int("BATCH_SIZE", 5),
        help="Max accounts per batch (default from ICKA_BATCH_SIZE or 5)",
    )
    parser.add_argument(
        "--batch-sleep-seconds",
        type=int,
        default=env_int("BATCH_SLEEP_SECONDS", 300),
        help=(
            "Sleep in seconds between batches "
            "(default from ICKA_BATCH_SLEEP_SECONDS or 300)"
        ),
    )

    args = parser.parse_args()

    # CLI flags OR env vars
    email = args.email or env_get("EMAIL")
    password = args.password or env_get("PASSWORD")
    accounts_file = args.accounts_file or env_get("ACCOUNTS_FILE")

    # forever: CLI flag OR ICKA_FOREVER
    args.forever = args.forever or env_bool("FOREVER", default=False)

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] [icka] %(message)s",
    )

    accounts = load_accounts(accounts_file, email, password)
    user_agent = args.user_agent

    log = logging.getLogger("icka")
    if accounts_file:
        log.info("Counted %d accounts total from %s", len(accounts), accounts_file)
    else:
        log.info("Counted %d account(s) total (single-account mode)", len(accounts))

    batch_size = args.batch_size
    batch_sleep_seconds = args.batch_sleep_seconds

    if not args.forever:
        # One-shot mode (ideal for cron)
        run_accounts_batched(accounts, user_agent, batch_size, batch_sleep_seconds)
        return

    # Forever mode
    try:
        sleep_seconds = parse_duration_to_seconds(args.sleep_interval)
    except ValueError as e:
        raise SystemExit(f"unable to parse --sleep-interval: {e}")

    logging.info(
        "Running in --forever mode, iteration sleep = %s (%ss)",
        args.sleep_interval,
        sleep_seconds,
    )

    while True:
        run_accounts_batched(accounts, user_agent, batch_size, batch_sleep_seconds)
        logging.info("Iteration complete, sleeping %.1f seconds…", sleep_seconds)
        time.sleep(sleep_seconds)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(0)
