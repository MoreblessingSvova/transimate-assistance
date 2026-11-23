# Transimate Assistant — AI Campus Transport Agent

A real-time campus transport information and decision-support agent.

## Run

```
run.bat
```

or:

```
python -m transimate.server --port 7860
```

Then open http://127.0.0.1:7860

No external dependencies — Python 3 standard library only.

## What it does

- **Real-time simulation**: 12 buses across 5 routes, live positions, occupancy, student
  queues at 29 stops, random delays, breakdowns, traffic zones and diversions. One tick
  per second = one simulated minute; clock runs at 08:00 onwards.
- **AI agent** (`transimate/agent.py`): natural-language intent detection, entity
  extraction (routes `R1–R5`, buses `B1–B12`, stop names), and reasoning traces.
- **Web UI**: Assistant chat, Live Map (SVG), Admin Analytics.
- **REST API**:
  - `GET /api/state` — full snapshot
  - `GET /api/chat?q=...&persona=student|parent|admin` — ask the agent
  - `GET /api/events` — SSE live stream
  - `GET /api/admin` — recommended actions
  - `GET /api/stops` — stop positions/names

## Mandatory capabilities (all implemented)

| # | Capability | Example query |
|---|------------|---------------|
| 1 | Which buses are running & best route | "Which buses are running right now, and which one should I take to reach campus?" |
| 2 | Bus location & ETA to pickup | "Where is my bus B1 now, and when will it reach Library?" |
| 3 | Seat availability & crowding | "How many seats are available on B2, is it overcrowded?" |
| 4 | Delays / cancellations & reasons | "Is my bus delayed or cancelled, and why?" |
| 5 | Traffic, roadblocks & diversions | "Is there any traffic or diversion affecting my journey?" |
| 6 | Alternative bus | "My regular bus on R1 is unavailable. Which alternative can I take?" |
| 7 | Students waiting per stop | "How many students are waiting at each pickup stop?" |
| 8 | Overcrowded routes & spare capacity | "Which routes are overcrowded and which nearby routes have space?" |
| 9 | Child bus arrival & schedule | "Has my child's bus reached the pickup point? Is it on schedule?" |
| 10 | Child bus safety issues | "Any safety issue or breakdown affecting my child's bus?" |
| 11 | Longest delays & root causes | "Which routes have the longest delays today and why?" |
| 12 | Unavailable buses & affected routes | "Which buses are unavailable for maintenance and which routes are affected?" |
| 13 | Where an extra bus is needed | "Based on waiting students and seats, where is an additional bus needed?" |
| 14 | Least-disruption breakdown cover | "If a bus breaks down now, which bus can take its passengers with least disruption?" |
| 15 | Fleet redistribution plan | "How should the buses be redistributed right now?" |

## Architecture

```
transimate/
  network.py     routes, stops, geometry helpers
  simulation.py  real-time tick engine + derived analytics/recommendations
  agent.py       NLU, intent routing, capability handlers, reasoning traces
  server.py      HTTP + SSE + static file server
web/
  index.html, styles.css, app.js
```