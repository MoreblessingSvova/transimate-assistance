"""Campus network data model: routes, stops, buses.

Coordinates are on a 1000x700 virtual campus map. Buses travel along
their route's polyline at simulated speed.
"""

import math
import random


def dist(a, b):
    return math.hypot(a[0] - b[0], a[1] - b[1])


ROUTES = [
    {
        "id": "R1",
        "name": "North Gate Express",
        "color": "#2563eb",
        "stops": [
            {"id": "S1", "name": "North Gate", "pos": (60, 80)},
            {"id": "S2", "name": "Library", "pos": (240, 150)},
            {"id": "S3", "name": "Science Block", "pos": (430, 120)},
            {"id": "S4", "name": "Engineering", "pos": (620, 200)},
            {"id": "S5", "name": "Central Plaza", "pos": (820, 300)},
            {"id": "S6", "name": "Admin Block", "pos": (900, 460)},
        ],
    },
    {
        "id": "R2",
        "name": "South Loop",
        "color": "#16a34a",
        "stops": [
            {"id": "S7", "name": "South Gate", "pos": (100, 620)},
            {"id": "S8", "name": "Hostel A", "pos": (280, 560)},
            {"id": "S9", "name": "Hostel B", "pos": (450, 600)},
            {"id": "S10", "name": "Sports Complex", "pos": (650, 520)},
            {"id": "S11", "name": "Central Plaza", "pos": (820, 300)},
            {"id": "S12", "name": "Admin Block", "pos": (900, 460)},
        ],
    },
    {
        "id": "R3",
        "name": "East Village",
        "color": "#d97706",
        "stops": [
            {"id": "S13", "name": "East Gate", "pos": (940, 80)},
            {"id": "S14", "name": "Medical Center", "pos": (830, 180)},
            {"id": "S15", "name": "Lecture Hall 2", "pos": (660, 260)},
            {"id": "S16", "name": "Central Plaza", "pos": (820, 300)},
            {"id": "S17", "name": "Cafeteria", "pos": (760, 420)},
            {"id": "S18", "name": "Admin Block", "pos": (900, 460)},
        ],
    },
    {
        "id": "R4",
        "name": "West Connector",
        "color": "#7c3aed",
        "stops": [
            {"id": "S19", "name": "West Gate", "pos": (60, 380)},
            {"id": "S20", "name": "Lecture Hall 1", "pos": (300, 320)},
            {"id": "S21", "name": "Library", "pos": (240, 150)},
            {"id": "S22", "name": "Science Block", "pos": (430, 120)},
            {"id": "S23", "name": "Lecture Hall 2", "pos": (660, 260)},
            {"id": "S24", "name": "Cafeteria", "pos": (760, 420)},
        ],
    },
    {
        "id": "R5",
        "name": "Ring Service",
        "color": "#dc2626",
        "stops": [
            {"id": "S25", "name": "Library", "pos": (240, 150)},
            {"id": "S26", "name": "Lecture Hall 1", "pos": (300, 320)},
            {"id": "S27", "name": "Sports Complex", "pos": (650, 520)},
            {"id": "S28", "name": "Hostel B", "pos": (450, 600)},
            {"id": "S29", "name": "Hostel A", "pos": (280, 560)},
        ],
    },
]

ROUTE_BY_ID = {r["id"]: r for r in ROUTES}

# Every stop across the network, keyed by id.
ALL_STOPS = {}
for _r in ROUTES:
    for _s in _r["stops"]:
        if _s["id"] not in ALL_STOPS:
            ALL_STOPS[_s["id"]] = {"id": _s["id"], "name": _s["name"], "pos": _s["pos"]}

# Friendly aliases so the agent can match user wording to stops/routes.
STOP_ALIASES = {
    "main gate": "S1", "north gate": "S1", "library": "S2",
    "science block": "S3", "engineering": "S4", "central plaza": "S5",
    "admin block": "S6", "admin": "S6", "south gate": "S7",
    "hostel a": "S8", "hostels": "S8", "hostel b": "S9",
    "sports complex": "S27", "sports": "S27",
    "east gate": "S13", "medical center": "S14", "hospital": "S14",
    "lecture hall 1": "S20", "lecture hall 2": "S15",
    "cafeteria": "S17", "canteen": "S17",
    "west gate": "S19",
}


def route_polyline(route):
    """Full polyline of a route including the return leg to the first stop."""
    pts = [s["pos"] for s in route["stops"]]
    return pts + list(reversed(pts))


def polyline_length(pts):
    return sum(dist(pts[i], pts[i + 1]) for i in range(len(pts) - 1))


def point_at(pts, d):
    """Return (x, y) at distance d along polyline pts."""
    for i in range(len(pts) - 1):
        seg = dist(pts[i], pts[i + 1])
        if d <= seg or i == len(pts) - 2:
            t = 0 if seg == 0 else max(0.0, min(1.0, d / seg))
            return (pts[i][0] + (pts[i + 1][0] - pts[i][0]) * t,
                    pts[i][1] + (pts[i + 1][1] - pts[i][1]) * t)
        d -= seg
    return pts[-1]


def make_buses(rng):
    """Create the fleet. Each bus: id, route, capacity, position along polyline."""
    buses = []
    per_route = {"R1": 3, "R2": 3, "R3": 2, "R4": 2, "R5": 2}
    n = 0
    for route in ROUTES:
        pts = route_polyline(route)
        total = polyline_length(pts)
        for k in range(per_route[route["id"]]):
            n += 1
            cap = rng.choice([40, 45, 50, 55, 60])
            buses.append({
                "id": f"B{n}",
                "route": route["id"],
                "capacity": cap,
                "dist": total * (k / per_route[route["id"]] + rng.uniform(0, 0.1)),
                "load_factor": rng.uniform(0.35, 1.05),  # fraction of capacity occupied
                "status": "running",  # running | delayed | breakdown | maintenance
                "delay_min": 0,
                "delay_reason": None,
                "speed": rng.uniform(22, 34),  # campus map units per sim-minute
                "broken": False,
                "breakdown_reason": None,
                "driver": rng.choice(["A. Silva", "J. Perera", "M. Fernando", "K. Jayasuriya", "N. Bandara", "T. Weerasinghe"]),
            })
    return buses


def route_distance_matrix():
    """Shortest graph distance between stops using shared stop names as junctions."""
    # Build graph: stops are nodes; route legs are edges.
    graph = {}
    for route in ROUTES:
        stops = route["stops"]
        for i in range(len(stops) - 1):
            a, b = stops[i]["id"], stops[i + 1]["id"]
            w = dist(stops[i]["pos"], stops[i + 1]["pos"])
            graph.setdefault(a, []).append((b, w))
            graph.setdefault(b, []).append((a, w))
    return graph


def shortest_distance(graph, start, goal):
    """Dijkstra distance between two stop ids."""
    import heapq
    if start == goal:
        return 0
    pq = [(0, start)]
    seen = set()
    while pq:
        d, u = heapq.heappop(pq)
        if u in seen:
            continue
        seen.add(u)
        if u == goal:
            return d
        for v, w in graph.get(u, []):
            if v not in seen:
                heapq.heappush(pq, (d + w, v))
    return float("inf")