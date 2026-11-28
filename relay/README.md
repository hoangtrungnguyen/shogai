# Shōgai Relay

Where people, and their AI agents, ask you a question with a key you gave them. Your Shōgai app picks the
questions up and sends your replies back. The relay never reaches into your app, and nothing is answered
automatically.

```
 someone with your key ──▶ relay page / API ──▶ relay.db ◀── your Shōgai app
   (a person or an agent)    (key required)                  (fetches questions every minute,
                                                               sends your replies)
```

## Who sees what

- **Asking needs one of your keys.** You make keys in your Shōgai app (*Requests* → *Keys*), one per person
  or group. The relay stores only a key's SHA-256: never the key itself or the label you gave it.
- **A question and its answer are shown only to whoever sent it.** They need the key *and* the question's own
  token, which they get when they ask (the relay page keeps it in their browser; agents keep it themselves).
  Someone else holding the same key sees none of it. You see everything in your own app.
- Your app's account token opens only your own keys and questions.
- **Revoking a key** stops it at once and deletes the questions sent with it. **Disconnecting** your app
  deletes your account, keys and questions on the relay.
- Whoever runs the relay can read the questions and answers in its database: they are not end-to-end
  encrypted. Run your own relay, or use one run by someone you trust.

## Run it

With Docker (Compose v2):

```bash
cd relay
cp .env.example .env    # set SHOGAI_RELAY_SIGNUP_CODE
docker compose up -d --build
```

The relay answers on http://127.0.0.1:8090 (this machine only; `RELAY_PORT` changes the port). To serve the
internet, put it behind HTTPS: point a domain at the machine, set `RELAY_DOMAIN` in `.env`, and add the
`https` profile. Caddy then gets and renews the certificate on ports 80 and 443:

```bash
docker compose --profile https up -d --build
```

The database is `relay/data/relay.db` (SQLite, mounted at `/data`). It holds the questions and replies, and
hashes of keys and tokens. On Linux the folder must be writable by uid 1000.

From source (Python ≥ 3.11 with [uv](https://docs.astral.sh/uv/)), on http://127.0.0.1:8090:

```bash
cd relay
uv run shogai-relay
```

## Connect your Shōgai

1. In Shōgai, open **Requests**, enter the relay's address and its sign-up code, and press **Connect**.
2. Under **Keys**, make a key for someone (**New key**) and send them **Copy link**. The link opens the relay
   with the key filled in. The key rides in the link's `#fragment`, which browsers never send to a server.
3. Their questions show up under **Requests**. Your app checks the relay every minute (`SHOGAI_RELAY_POLL`,
   or press **Sync now**), and sends each reply as soon as you write it. If the relay can't be reached, the
   reply waits and goes out at the next sync.

## Settings

| Variable | Default | |
|---|---|---|
| `SHOGAI_RELAY_SIGNUP_CODE` | — | When set, a Shōgai app needs it to open an account. Empty: anyone who finds the relay can open one (they still can't see anyone else's questions). |
| `SHOGAI_RELAY_DATA_DIR` | `relay/data` (`/data` in Docker) | Where `relay.db` lives |
| `SHOGAI_RELAY_HOST`, `SHOGAI_RELAY_PORT` | `127.0.0.1`, `8090` | For `uv run shogai-relay` |
| `RELAY_PORT` | `8090` | Docker: the host port (bound to 127.0.0.1) |
| `RELAY_DOMAIN` | — | Docker `https` profile: the domain Caddy gets a certificate for |

## API

People and agents asking, with `Authorization: Bearer <key>` (or `X-Shogai-Key: <key>`). `/llms.txt`
explains this for AI agents, and `/docs` describes the whole API.

| Method & path | |
|---|---|
| `GET /api/key` | whom the key lets you ask (their name and headline) |
| `POST /api/requests` | `{question, name?}` → `{id, token, status, …}`; keep the token |
| `GET /api/requests/{id}` | header `X-Request-Token: <token>` → `status`, `delivered`, `reply` |

Shōgai apps, with `Authorization: Bearer <account token>`:

| Method & path | |
|---|---|
| `POST /api/owner/signup` | `{code}` → `{owner_id, owner_token}` |
| `GET /api/owner` | the account, its key ids, how many questions wait |
| `PUT /api/owner/profile` | `{name, headline}`, shown to key holders |
| `DELETE /api/owner` | delete the account, its keys and questions |
| `POST /api/owner/keys` | `{key_hash}` (SHA-256, hex) → `{key_id}` |
| `DELETE /api/owner/keys/{id}` | revoke a key; its questions are deleted |
| `GET /api/owner/requests?after=N` | questions after sequence number N (marks them delivered) |
| `POST /api/owner/requests/{id}/answer` | `{status: "replied" \| "declined", reply}` |
| `DELETE /api/owner/requests/{id}` | delete a question |

## Limits

20 questions an hour per key and 30 per address, 500 unanswered per person, 5 sign-ups an hour per address,
and 30 unknown keys per 10 minutes per address. They live in memory, so a restart resets them.

## Tests

```bash
cd relay
uv run pytest
```

The Shōgai app's tests (`backend/tests/test_relay_sync.py`) also run this relay in-process, to check that
the two agree.
