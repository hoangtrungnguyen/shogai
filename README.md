# Shōgai · 生涯

**Personal Relation Agent Advisor.** Feed it what you know about the people in your life — photos,
personal thoughts, videos, voice memos, academic papers — and Gemini turns it into one living,
source-cited Markdown profile per person. Then ask the advisor anything about them.

```
                ┌──────────────── Feature 1 · Consume Info ────────────────┐
 images  ──▶ Gemini vision ─┐
 thoughts ─▶ Gemini text  ──┤   one source note per input      Gemini merge
 video/audio ▶ Gemini video ┼──▶ people/<slug>/sources/sN.md ──▶ people/<slug>/profile.md
 papers  ──▶ Gemini read + ─┘   (observations, evidence,        (sections + [sN] citations)
             Google-Search        quotes, fact-check)
             fact-check
                └──────────────────────────────────────────────────────────┘
                ┌──────────────── Feature 2 · Advisor ─────────────────────┐
 you ──▶ streaming Gemini chat agent ──tools──▶ list_people · get_person_profile
                                               search_notes · get_source
                └──────────────────────────────────────────────────────────┘
```

## Features

**Consume Info** (`/consume`)
- Pick a person (or create one), then add any mix of: personal thoughts, image files, videos, audio,
  PDFs / text files, and links (YouTube, arXiv / DOI / PDF URLs, web pages). Paste screenshots with ⌘V.
- Each input is routed to its Gemini processor (up to 3 in parallel):

  | Source | Processor | What Gemini does |
  |---|---|---|
  | Image | `analyzers/image.py` | Vision: scene, mood, people, visible text (OCR of chats/cards); picks a portrait as avatar |
  | Text | `analyzers/text.py` | Extracts every fact, preference, date and feeling from notes, chat exports, documents, web pages |
  | Video / audio | `analyzers/video.py` | Files API upload (or YouTube URL): key moments with timestamps, verbatim quotes, tone |
  | Academic paper | `analyzers/academic.py` | Reads the PDF/URL (how it relates to the person: author / mentioned / topic), then **fact-checks** key claims with Google Search grounding: publication status, authorship, per-claim verdicts, applicability caveats, web sources |

- Every analysis is saved as `sources/sNNN-*.md`; Gemini then merges the new sources into
  `profile.md`, citing each fact as `[s3]` and marking inferences `(likely)` / `(uncertain)`.
- Progress streams live (SSE). Jobs run in the background — navigate away and they keep going.

**Advisor** (`/advisor`)
- Streaming chat. Focus on one or more people (their full profiles go into context), or ask
  open questions — the agent calls tools to list people, read profiles, search all notes, and open
  the source behind a citation.
- Conversations are kept in your browser (localStorage) under *History*.

**Academic resources** (`/resources`)
- Lists the papers, books, textbooks, courses and reports **mentioned in your notes** — nothing else.
  *Scan notes* has Gemini read your original text notes (imported `.md`/`.txt` files and typed
  thoughts) in batches. Every entry must quote its note word for word; the backend checks each quote
  against the note and drops anything it cannot find, so suggested or invented works never appear.
- Each entry shows who it relates to, the exact line, and a link to the note. Titles completed from a
  partial mention (e.g. "80-20" → *The 80/20 Principle*) are marked "details completed by Gemini".
- Papers you consumed are listed with their fact-check verdicts. Scans are cached per note, so only
  new or edited notes are sent to Gemini.

**Profiles** (`/people/<slug>`): rendered profile with clickable citations → source drill-down,
media gallery, raw Markdown editor, *Rebuild profile* (re-merge all sources), delete (moves to `data/.trash`).

## The database: a folder of Markdown

```
data/people/linh-tran/
  profile.md            # YAML frontmatter (name, relationship, tags, summary, avatar…) + profile
  sources/
    s001-text-university-friend-notes.md
    s002-image-birthday-card-for-linh.md
    s003-academic-shared-novel-activities….md   # includes the fact-check table + web sources
  media/                # original uploads and typed thoughts (SHOGAI_KEEP_MEDIA=true)
  history/              # last 20 versions of profile.md (written before every change)
data/library/
  resources.md          # academic resources mentioned in the notes (regenerated on every scan)
  .index.json           # per-note scan cache (content hash → mentions)
data/.trash/            # deleted people / sources
```

Every `profile.md` frontmatter carries these properties (Obsidian shows them as typed fields):

```yaml
priority: 1              # number (1 = highest, as in the imported notes)
follow_up: true          # boolean
phone: '0912345678'      # string (quoted so it stays text)
email: name@example.com  # string
linkedin: https://www.linkedin.com/in/…   # url
facebook: https://www.facebook.com/…      # url
instagram: handle                         # name, without @
github: https://github.com/…              # url
blog: https://…                           # url
related_webs: [https://…]                 # list of urls
```

Edit them on the profile's *Details* card or by hand. They are filled automatically, never
overwriting a value you set, from the person's own notes: note frontmatter (`fb:`, `linked:`, `web:`…),
labelled lines (`Facebook: …`, `phone:: …`) and bare profile links. A bare link counts only when its handle
matches the person's name; other links go to `related_webs`. Profiles created before these properties existed
were upgraded once on startup (a backup of each is in `history/`).

Plain Markdown with relative links — the `data/` folder opens fine in Obsidian or any editor.
Hand edits are welcome (keep `name` in the frontmatter); the `## Source Log` section is regenerated.

## Run it

Prerequisites: Python ≥ 3.11 with [uv](https://docs.astral.sh/uv/), Node ≥ 20.

```bash
cp backend/.env.example backend/.env   # then set GEMINI_API_KEY (already done on this machine)
```

Single server (UI + API on http://127.0.0.1:8000):

```bash
./scripts/start.sh
```

Development with hot reload (UI on http://localhost:5173, API on :8000):

```bash
./scripts/dev.sh
```

Tests (offline; Gemini is faked):

```bash
cd backend && uv run pytest
```

## Run with Docker

One image holds the built UI and the API (http://127.0.0.1:8000). It reads `backend/.env` and mounts `./data`, so
Docker and the scripts above share the same database. Needs Docker with Compose v2.

```bash
./scripts/docker.sh             # build shogai:<VERSION> from this checkout and start it
./scripts/docker.sh logs -f     # any other arguments go to `docker compose`
./scripts/docker.sh down
```

- The port is published on `127.0.0.1` only. Stop `./scripts/start.sh` first, or pick another port:
  `SHOGAI_PORT=8080 ./scripts/docker.sh`.
- Inside the container the data folder is always `/app/data`; change the `./data` mount in `docker-compose.yml`
  to use another folder. On Linux, `./data` must be writable by uid 1000 (or set `user:` in that file).

### Versions

`VERSION` at the repo root is the release version. `./scripts/docker.sh` tags each build `shogai:<VERSION>` (and moves
`shogai:latest` to it), and stamps the git commit (`-dirty` when the checkout has uncommitted changes) and the build
time. The running version shows in the sidebar footer (hover it for commit and build time), in `GET /api/health`
(`version`, `commit`, `built_at`) and as OCI labels (`docker inspect shogai:0.1.0`).

- **Release:** set `VERSION` (e.g. `0.2.0`), then run `./scripts/docker.sh`. Earlier images stay: `docker images shogai`.
- **Roll back:** `SHOGAI_VERSION=0.1.0 docker compose up -d --no-build` runs an image you built before.
- `SHOGAI_IMAGE` renames the image (default `shogai`), e.g. `ghcr.io/you/shogai` before `./scripts/docker.sh push`.
- Plain `docker compose up -d --build` works too; that build is tagged `shogai:latest` only, without commit or build time.

## Configuration (`backend/.env`)

| Variable | Default | Purpose |
|---|---|---|
| `GEMINI_API_KEY` | — | Required. Values in `backend/.env` override variables exported in your shell. |
| `SHOGAI_MODEL_ANALYZE` | `gemini-flash-latest` | Image / text / video / audio / paper analysis + fact-check |
| `SHOGAI_MODEL_SYNTHESIZE` | `gemini-flash-latest` | Merging sources into `profile.md` |
| `SHOGAI_MODEL_ADVISOR` | `gemini-flash-latest` | Chat agent (e.g. `gemini-pro-latest` for deeper answers) |
| `SHOGAI_*_THINKING` | `low` / `medium` / `medium` | Gemini thinking level per stage |
| `SHOGAI_LANGUAGE` | `English` | Language profiles and source notes are written in |
| `SHOGAI_DATA_DIR` | `data` | Database folder (relative to repo root) |
| `SHOGAI_KEEP_MEDIA` | `true` | Keep original uploads next to the profile |
| `SHOGAI_GEMINI_API_VERSION` | `v1beta` | Gemini API version (keys in the `AQ.` format need `v1beta`) |

## API

| Method & path | |
|---|---|
| `GET /api/health` | version + build stamp, config, Gemini status |
| `GET/POST /api/people` | list / create (`{name, relationship}`) |
| `PATCH /api/people/{slug}/properties` | update priority, follow_up, contacts (validated) |
| `GET/PUT/DELETE /api/people/{slug}` | read / replace raw Markdown / move to trash |
| `POST /api/people/{slug}/rebuild` | re-merge all sources (background job) |
| `GET /api/people/{slug}/sources[/{sN}]`, `DELETE …/{sN}` | source notes |
| `GET /api/people/{slug}/media[/{file}]` | original uploads |
| `POST /api/ingest` | multipart: `person`, `thoughts`, `files[]`, `manifest` (JSON `[{kind, note}]`), `links` (JSON `[{url, kind, note}]`) → job |
| `GET /api/library` | academic resources + scan status |
| `POST /api/library/scan[?force=true]` | scan new/edited notes (or all) for resources → job |
| `GET /api/jobs/{id}/events` | job progress as Server-Sent Events (replayable, `Last-Event-ID`) |
| `POST /api/chat` | `{messages, focus}` → SSE stream of `text` / `tool` / `done` / `error` events |

## Privacy

Everything is stored locally in `data/`. The server listens on `127.0.0.1` only (in Docker, the published port is
bound to `127.0.0.1`). Content you consume is
sent to the Gemini API for analysis (uploaded video/audio/PDF files are deleted from Gemini right after
analysis; they would otherwise expire after 48 h). The analyzers are instructed not to infer sensitive
traits (health, religion, sexuality, politics…) from appearance and not to identify people by their face.

## Troubleshooting

- **`401 API key not valid … ACCESS_TOKEN_TYPE_UNSUPPORTED`** — an old `GEMINI_API_KEY` exported in your
  shell profile, or API version `v1`. Shōgai reads `backend/.env` with priority and pins `v1beta`.
- **429** — rate limit / quota; wait and retry. Requests are retried automatically with backoff.
- A source analysis looks wrong → delete it on the *Sources* tab, then *Rebuild profile*.
