[日本語](README.md) | **English** | [简体中文](README.zh-CN.md)

# TRPG Agent Table: a tabletop RPG engine where humans and AI agents share the table

This is the server and web frontend for an online tabletop RPG table. The goal is to **seat LLM agents at the same table as
human players while making it structurally impossible for them to cheat, peek at other players' hands, or make up dice results.**

```
human  ── web (GraphQL) ────┐
                            ├──►  server.py (the only process holding the engine) ──► trpg/engine/ (authority: rules / dice / events)
agent  ── MCP tools ────────┘                │
                                             └─► runs/<session>/events.jsonl (append-only, replayable)
"your turn" ◄── kick (outbound webhook; wakes agents per account)
```

## Three design rules

1. **Only the engine produces numbers.** Dice, HP, damage and currency are all computed in `server/trpg/engine/`.
   An agent can only *declare* an action (`table_act`); it has no path to write state.
2. **Information firewall.** `view()` projects the state per seat. Secret goals, clue fragments and private messages are visible
   only to their holder. GM narration and NPC lines go through the same **leak gate**: if they would reveal another seat's secret
   text verbatim, the engine rejects them.
3. **Everything is replayable.** State is a fold over the event log (event sourcing). `op=replay` replays from the start and compares
   the result field by field with the in-memory state; the tests use this heavily as an invariant.

## Where to look

| Topic | Location | Notes |
|---|---|---|
| Event sourcing and replay | `server/trpg/core/replay.py`, `store.py` | One `events.jsonl` per session; restarts lose nothing; replay consistency is a test invariant |
| Projection and information firewall | `server/trpg/engine/projection/` | The same state projected by "who is looking"; spectators get the public projection only |
| MCP adapter | `server/trpg/api/mcp_api/`, `mcp_adapter.py` | 7 tools (`table_view/act/say/tell/join/gm/secret`); the tool set adapts to the table's config |
| GraphQL | `server/trpg/api/graphql/`, `docs/schema.graphql` | The single client endpoint; anonymous read-only access (spectating) is controlled per query |
| Turns and agent wake-ups | `server/trpg/service/wake/`, `trpg/kick/` | Wakes agents per **account**, not per seat; idempotency keys deduplicate; one wake-up carries all the context needed |
| Three-layer rule packs | `server/trpg/rules/packs/`, `server/packs/README.md` | Engine → module → scenario layers; content (skill / item / monster / shop tables) separated from mechanics |
| Scenario DSL | `server/trpg/rules/dsl.py`, `expr.py`, `scenario/` | Goal conditions, triggers and endings are JSON ASTs; no code needed |
| Single-process multi-table hub | `server/trpg/api/hub/`, `docs/HUB.md` | One port serves every table (one sub-path per table); one login works across tables |
| Agent playtest harness | `server/agent_harness/` | Has several agents play tables automatically and reports the move distribution (for tuning scenario pacing) |

## Size

- Engine: about **21k lines** of Python (`server/trpg/`, layered as core / engine / rules / api)
- Tests: **55 test files, about 17k lines**, all passing (including end-to-end tests against a real HTTP server, an MCP client and GraphQL)
- Frontend: a single file with no build step (`frontend/web/index.html`, plain JS, 3 languages, light / dark themes)
- Few dependencies: the standard library plus graphql-core / pg8000 in `vendor/`; the MCP endpoint needs the official `mcp` SDK

## Layout

```
server/              engine and server (Python)
  trpg/                core (event store / replay) · engine (table, projection, turns) · rules · api (GraphQL / MCP / hub)
  packs/               engine-level mechanic packs (d20core, board, death, wound…)
  modules/hyakki_tower demo module "Hyakki Tower" (original setting: characters, map, scenario, rulebook pack)
  tests/               55 test files; tools/run_tests.sh is the single entry point
  docs/                specs (API / AGENT / HUB / SCENARIO_FORMAT…)
frontend/            web client (single index.html + lobby.html) plus a local mock table, mock_server
scenario-template/   templates, format docs and a self-check tool (check_module.sh) for scenario authors
```

## Running it

Requires Python **3.10+**.

```bash
cd server
TRPG_PY=python3.12 bash tools/run_tests.sh              # all tests
TRPG_PY=python3.12 bash tools/run_tests.sh test_board   # a single file (a full board-play walkthrough)
```

Some tests start local HTTP servers (random ports on 127.0.0.1). The MCP assertions need the official SDK:
`pip install "mcp<2"` (without it, those checks fail explicitly as "mcp SDK 可用" and the rest of the suite runs normally).

Start a single table (development mode):

```bash
cd server
S=modules/hyakki_tower/scenarios/sannenme_no_onibarai
cp $S/config.example.json $S/config.json      # fill in auth.gm_token etc. (config.json is not tracked)
python3 server.py --config $S/config.json --home $S --port 8971
# MCP endpoint for agents: http://127.0.0.1:8971/mcp
```

See `server/docs/RUN.md` for more ways to start and stop, and `server/docs/AGENT.md` for connecting an agent.

## About the demo content

`server/modules/hyakki_tower/` (Hyakki Tower) is an **original** setting; its characters, map, scenario and numbers were all written for this project.
Artwork is produced with a local generative model and is not tracked (the repository holds only the asset manifest `assets/index*.json`).
`frontend/fixtures/` is a snapshot taken with `frontend/tools/grab_fixtures.py` after a short local demo session on this module.

## Known limitations

- `frontend/mock/mock_server.py` (the mock table) only emulates the old REST API. The frontend has moved to GraphQL login,
  so the mock table stops at the login dialog; to see the full page, connect to a real table (the hub).
- Code comments and docs are mostly in Chinese (Traditional / Simplified) and Japanese.
