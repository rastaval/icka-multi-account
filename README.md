# icka-multi (Python IRCCloud keep-alive)
# forked from https://github.com/osm/icka
# shoutout to Oscar Linderholm/OSM

Python port of the Go "icka" IRCCloud keep-alive tool, with multi-account support
and simple .env configuration.

## Features

- Logs into IRCCloud using the official web endpoints
- Supports:
  - single account (EMAIL/PASSWORD)
  - multiple accounts via `accounts.txt`
- One-shot mode (good for cron)
- Forever mode (`ICKA_FOREVER=true`) with Go-like sleep intervals (`1h`, `90m`, `1.5h`, etc.)

## Setup

1. Install dependencies (user-local):

```bash
python3 -m pip install --user requests websocket-client

2. Copy example configs:

cp .env.example .env
cp accounts.example.txt accounts.txt


3. Edit .env and accounts.txt with real values.

Test:

python3 ./icka_keepalive.py

Cron example

Run every hour:

0 * * * * cd /home/youruser/icka && /usr/bin/python3 ./icka_keepalive.py >> icka_keepalive.log 2>&1

