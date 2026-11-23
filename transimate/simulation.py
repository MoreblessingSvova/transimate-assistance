"""Real-time campus transport simulator.

Advances simulated time on each tick: buses move along their routes,
students arrive at stops, delays/traffic/incidents appear and resolve,
loads drift with demand. Provides the live data API the agent reasons over.
"""

import math
import random
import threading
import time

from . import network


class Simulation:
    def __init__(self, seed=42):
        self.rng = random.Random(seed)
        self.lock = threading.Lock()
        self.sim_minute = 8 * 60  # start 08:00 (minutes since midnight)
        self.tick_count = 0
        self.buses = network.make_buses(self.rng)
        self.traffic_zones = []   # {pos, radius, severity, reason, until}
        self.incidents = []       # {type, route, bus, reason, severity, until, detail}
        self.waiting = {}         # stop_id -> int students
        for stop in network.ALL_STOPS:
            self.waiting[stop] = self.rng.randint(0, 6)
        self.boards = []          # recent boardings for analytics
        self.delay_history = []   # {minute, route, delay_min, reason}
        self._next_incident_at = self.sim_minute + self.rng.randint(2, 6)
        self.started_at = time.time()

    # -- time helpers -----------------------------------------------------
    def clock_str(self):
        m = int(self.sim_minute) % (24 * 60)
        return f"{m // 60:02d}:{m % 60:02d}"

    def demand_profile(self):
        """0..1 demand level based on time of day."""
        h = (self.sim_minute / 60) % 24
        if 7.5 <= h < 9.5:
            return 1.0
        if 9.5 <= h < 11.5:
            return 0.75
        if 11.5 <= h < 13.5:
            return 0.85
        if 13.5 <= h < 16.0:
            return 0.8
        if 16.0 <= h < 18.0:
            return 0.95
        return 0.35

    # -- core tick ----------------------------------------------------------
    def tick(self):
        """Advance the simulation by one simulated minute."""
        with self.lock:
            self.tick_count += 1
            self.sim_minute += 1
            self._maybe_spawn_incident()
            self._age_out_incidents()
            for bus in self.buses:
                self._move_bus(bus)
                self._update_load(bus)
            self._update_waiting()
            self._prune_history()

    def _maybe_spawn_incident(self):
        if self.sim_minute < self._next_incident_at:
            return
        self._next_incident_at = self.sim_minute + self.rng.randint(5, 14)
        roll = self.rng.random()
        route = self.rng.choice(network.ROUTES)
        if roll < 0.3:
            # traffic zone
            stop = self.rng.choice(route["stops"])
            self.traffic_zones.append({
                "pos": stop["pos"], "radius": self.rng.randint(60, 110),
                "severity": self.rng.choice(["slowdown", "heavy"]),
                "reason": self.rng.choice(["roadwork", "event traffic", "accident", " VIP movement"]),
                "until": self.sim_minute + self.rng.randint(4, 12),
            })
        elif roll < 0.55:
            # delay on a bus
            bus = self.rng.choice([b for b in self.buses if b["status"] == "running"])
            delay = self.rng.randint(3, 14)
            bus["status"] = "delayed"
            bus["delay_min"] += delay
            bus["delay_reason"] = self.rng.choice([
                "heavy traffic near campus entrance",
                "waiting for late-connecting shuttle",
                "signal fault at intersection",
                "door malfunction causing slow boarding",
            ])
            self.delay_history.append({
                "minute": self.sim_minute, "route": bus["route"],
                "delay_min": delay, "reason": bus["delay_reason"],
            })
        elif roll < 0.7:
            # breakdown
            bus = self.rng.choice([b for b in self.buses if b["status"] in ("running", "delayed")])
            bus["status"] = "breakdown"
            bus["broken"] = True
            bus["breakdown_reason"] = self.rng.choice([
                "engine overheating", "flat tyre", "brake system fault", "electrical failure",
            ])
            bus["delay_min"] = 0
            self.incidents.append({
                "type": "breakdown", "bus": bus["id"], "route": bus["route"],
                "reason": bus["breakdown_reason"], "severity": "high",
                "until": self.sim_minute + self.rng.randint(6, 15),
                "detail": f"Bus {bus['id']} on {bus['route']} broken down",
            })
        elif roll < 0.82:
            # route diversion
            self.incidents.append({
                "type": "diversion", "route": route["id"],
                "reason": self.rng.choice([
                    "roadblock near main entrance", "flooding on lower campus road",
                    "construction diversion", "security checkpoint",
                ]),
                "severity": "medium",
                "until": self.sim_minute + self.rng.randint(4, 10),
                "detail": f"{route['id']} diverted around affected segment",
            })

    def _age_out_incidents(self):
        self.traffic_zones = [t for t in self.traffic_zones if t["until"] > self.sim_minute]
        keep = []
        for inc in self.incidents:
            if inc["type"] == "breakdown" and inc["until"] > self.sim_minute:
                keep.append(inc)
            elif inc["type"] != "breakdown" and inc["until"] > self.sim_minute:
                keep.append(inc)
            elif inc["type"] == "breakdown":
                bus = self._bus(inc["bus"])
                if bus:
                    bus["status"] = "running"
                    bus["broken"] = False
                    bus["breakdown_reason"] = None
                    bus["delay_min"] = self.rng.randint(4, 10)
                    bus["delay_reason"] = "resuming after breakdown repair"
                    bus["status"] = "delayed"
        self.incidents = keep

    def _move_bus(self, bus):
        if bus["status"] in ("breakdown", "maintenance"):
            return
        route = network.ROUTE_BY_ID[bus["route"]]
        pts = network.route_polyline(route)
        total = network.polyline_length(pts)
        # traffic slowdown if inside a zone
        speed_factor = 1.0
        pos = network.point_at(pts, bus["dist"] % total)
        for tz in self.traffic_zones:
            if network.dist(pos, tz["pos"]) < tz["radius"]:
                speed_factor *= 0.35 if tz["severity"] == "heavy" else 0.6
        if any(i["type"] == "diversion" and i["route"] == bus["route"] for i in self.incidents):
            speed_factor *= 0.5
        advance = bus["speed"] * speed_factor
        bus["dist"] = (bus["dist"] + advance) % total
        # delays tick down when moving again
        if bus["status"] == "delayed":
            if bus["delay_min"] > 0 and self.rng.random() < 0.25:
                bus["delay_min"] -= 1
            if bus["delay_min"] == 0:
                bus["status"] = "running"
                bus["delay_reason"] = None

    def _update_load(self, bus):
        """Drift occupancy toward demand-driven target."""
        if bus["status"] in ("breakdown", "maintenance"):
            return
        target = self.demand_profile() * self.rng.uniform(0.65, 1.15)
        lf = bus["load_factor"]
        lf += (target - lf) * 0.08 + self.rng.uniform(-0.02, 0.02)
        bus["load_factor"] = max(0.1, min(1.3, lf))

    def _update_waiting(self):
        demand = self.demand_profile()
        for stop, count in list(self.waiting.items()):
            arrivals = self.rng.random() < 0.45 * demand
            if arrivals:
                self.waiting[stop] = min(25, count + self.rng.randint(1, 4))
            # buses nearby pick up students
            for bus in self.buses:
                if bus["status"] in ("breakdown", "maintenance"):
                    continue
                route = network.ROUTE_BY_ID[bus["route"]]
                pts = network.route_polyline(route)
                pos = network.point_at(pts, bus["dist"])
                for s in route["stops"]:
                    if s["id"] == stop and network.dist(pos, s["pos"]) < 25:
                        take = min(count, self.rng.randint(2, 10))
                        if take > 0:
                            self.waiting[stop] -= take
                            bus["load_factor"] = min(1.35, bus["load_factor"] + take / bus["capacity"])
                            self.boards.append({"minute": self.sim_minute, "stop": stop, "n": take})
                        break
            if self.rng.random() < 0.12:
                self.waiting[stop] = max(0, count - self.rng.randint(1, 3))

    def _prune_history(self):
        cutoff = self.sim_minute - 120
        self.boards = [b for b in self.boards if b["minute"] >= cutoff]
        self.delay_history = [d for d in self.delay_history if d["minute"] >= cutoff]

    def _bus(self, bus_id):
        return next((b for b in self.buses if b["id"] == bus_id), None)

    # -- derived helpers ----------------------------------------------------
    def bus_position(self, bus):
        route = network.ROUTE_BY_ID[bus["route"]]
        pts = network.route_polyline(route)
        total = network.polyline_length(pts)
        return network.point_at(pts, bus["dist"] % total)

    def bus_next_stop(self, bus):
        route = network.ROUTE_BY_ID[bus["route"]]
        stops = route["stops"]
        pts = network.route_polyline(route)
        total = network.polyline_length(pts)
        d = bus["dist"] % total
        # leg length between consecutive stops (outbound then inbound)
        leg = 0
        for i in range(len(stops) - 1):
            leg += network.dist(stops[i]["pos"], stops[i + 1]["pos"])
            if d <= leg:
                return stops[i + 1]
        return stops[0]

    def eta_to_stop(self, bus, stop_id):
        """Minutes until bus reaches stop_id, or None if bus doesn't serve it."""
        route = network.ROUTE_BY_ID[bus["route"]]
        stops = route["stops"]
        n = len(stops)
        if stop_id not in [s["id"] for s in stops]:
            return None
        pts = network.route_polyline(route)
        total = network.polyline_length(pts)
        d = bus["dist"] % total
        # occurrence distances of each stop along the polyline (outbound + return)
        occ = {}
        cur = 0
        for i in range(n):
            occ.setdefault(stops[i]["id"], []).append(cur)
            if i < n - 1:
                cur += network.dist(stops[i]["pos"], stops[i + 1]["pos"])
        for i in range(n - 2, -1, -1):
            cur += network.dist(stops[i]["pos"], stops[i + 1]["pos"])
            occ[stops[i]["id"]].append(cur)
        best = min((c - d) % total for c in occ[stop_id])
        speed = max(8.0, bus["speed"])
        eta = best / speed
        if bus["status"] == "delayed":
            eta += max(1, bus["delay_min"] // 3)
        return round(eta, 1)

    def occupancy(self, bus):
        return round(bus["load_factor"] * bus["capacity"])

    def spare_seats(self, bus):
        return max(0, bus["capacity"] - self.occupancy(bus))

    def running_buses(self):
        return [b for b in self.buses if b["status"] in ("running", "delayed")]

    def route_status(self):
        out = {}
        for r in network.ROUTES:
            buses = [b for b in self.buses if b["route"] == r["id"]]
            delayed = [b for b in buses if b["status"] == "delayed"]
            broken = [b for b in buses if b["status"] == "breakdown"]
            loads = [b["load_factor"] for b in buses if b["status"] != "breakdown"]
            avg = sum(loads) / len(loads) if loads else 0
            out[r["id"]] = {
                "buses": len(buses), "delayed": len(delayed), "broken": len(broken),
                "avg_load": round(avg, 2),
                "status": ("disrupted" if broken else "delayed" if delayed else "ok"),
            }
        return out

    def snapshot(self):
        with self.lock:
            return {
                "clock": self.clock_str(),
                "sim_minute": self.sim_minute,
                "tick": self.tick_count,
                "buses": [
                    {
                        "id": b["id"], "route": b["route"], "capacity": b["capacity"],
                        "occupancy": self.occupancy(b), "load_factor": round(b["load_factor"], 2),
                        "status": b["status"], "delay_min": b["delay_min"],
                        "delay_reason": b["delay_reason"],
                        "breakdown_reason": b["breakdown_reason"],
                        "position": [round(v, 1) for v in self.bus_position(b)],
                        "next_stop": self.bus_next_stop(b)["name"],
                        "driver": b["driver"],
                    }
                    for b in self.buses
                ],
                "routes": self.route_status(),
                "waiting": dict(self.waiting),
                "traffic_zones": self.traffic_zones,
                "incidents": self.incidents,
                "demand": round(self.demand_profile(), 2),
            }

    # -- analytics / recommendations ----------------------------------------
    def stop_waiting_list(self, min_count=0):
        items = sorted(
            [(sid, c, network.ALL_STOPS[sid]["name"]) for sid, c in self.waiting.items()],
            key=lambda x: -x[1],
        )
        return [{"stop_id": s, "count": c, "name": n} for s, c, n in items if c >= min_count]

    def overcrowded_routes(self):
        rs = self.route_status()
        out = []
        for rid, st in rs.items():
            if st["avg_load"] >= 0.95:
                out.append((rid, st["avg_load"]))
        return sorted(out, key=lambda x: -x[1])

    def additional_bus_recommendation(self):
        """Where is an extra bus needed now: highest waiting vs spare capacity."""
        best = None
        for entry in self.stop_waiting_list():
            sid, count, name = entry["stop_id"], entry["count"], entry["name"]
            routes_serving = [r["id"] for r in network.ROUTES if sid in [s["id"] for s in r["stops"]]]
            if not routes_serving:
                continue
            rid = routes_serving[0]
            st = self.route_status()[rid]
            spare = sum(self.spare_seats(b) for b in self.buses if b["route"] == rid and b["status"] == "running")
            score = count * 10 - spare
            if best is None or score > best["score"]:
                best = {"stop_id": sid, "stop_name": name, "waiting": count,
                        "route": rid, "spare": spare, "score": score}
        return best

    def breakdown_reroute(self, bus_id):
        """If bus_id breaks down now, which available bus covers with least disruption."""
        bus = self._bus(bus_id)
        if not bus:
            return None
        route = network.ROUTE_BY_ID[bus["route"]]
        stop_ids = [s["id"] for s in route["stops"]]
        waiting_on_route = sum(self.waiting[s] for s in stop_ids)
        candidates = []
        for b in self.buses:
            if b["id"] == bus_id or b["status"] not in ("running", "delayed"):
                continue
            # cost: distance from candidate's position to broken bus's route start area
            same = 1 if b["route"] == bus["route"] else 0
            pos = self.bus_position(b)
            start = route["stops"][0]["pos"]
            travel = network.dist(pos, start) / max(8, b["speed"])
            spare = self.spare_seats(b)
            # least disruption = same route, close by, spare seats covering waiting
            cost = travel + (0 if same else 6) - min(spare, waiting_on_route) * 0.15
            candidates.append((cost, b, spare))
        candidates.sort(key=lambda x: x[0])
        if not candidates:
            return None
        cost, b, spare = candidates[0]
        return {
            "bus_id": b["id"], "route": b["route"], "spare": spare,
            "eta_min": round(max(0.5, cost), 1), "waiting_on_route": waiting_on_route,
        }

    def redistribution_plan(self):
        """How should buses be redistributed right now."""
        actions = []
        overloaded = self.overcrowded_routes()
        spare_routes = []
        for rid, st in self.route_status().items():
            if st["avg_load"] < 0.7 and st["broken"] == 0:
                spare = sum(self.spare_seats(b) for b in self.buses if b["route"] == rid and b["status"] == "running")
                spare_routes.append((spare, rid, st))
        spare_routes.sort(key=lambda x: -x[0])
        broken = [b for b in self.buses if b["status"] == "breakdown"]
        all_spare = [(rid, st) for rid, st in self.route_status().items() if st["broken"] == 0]
        for rid, load in overloaded:
            route_stops = [s["id"] for s in network.ROUTE_BY_ID[rid]["stops"]]
            hot = [w for w in self.stop_waiting_list() if w["stop_id"] in route_stops]
            need_stop = max(hot, key=lambda w: w["count"]) if hot else None
            target = None
            for spare, srid, st in spare_routes:
                if srid != rid:
                    target = (srid, spare)
                    break
            if target is None:
                # every route is stretched: shift from the least-loaded other route
                others = [(srid, st) for srid, st in all_spare if srid != rid]
                if others:
                    others.sort(key=lambda kv: kv[1]["avg_load"])
                    target = (others[0][0], 0)
            actions.append({
                "type": "shift_capacity",
                "from_route": rid, "to_route": target[0] if target else None,
                "reason": (f"{rid} at {int(load*100)}% load; rebalance capacity from {target[0]} "
                           f"(next-least loaded)" if target and target[1] == 0 else
                           f"{rid} at {int(load*100)}% load; {target[0]} has {target[1]} spare seats" if target
                           else f"{rid} overloaded and no alternative route available - dispatch reserve bus"),
                "priority": "high" if load >= 1.05 else "medium",
                "hot_stop": need_stop["name"] if need_stop else None,
            })
        for b in broken:
            alt = self.breakdown_reroute(b["id"])
            actions.append({
                "type": "cover_breakdown", "bus": b["id"], "route": b["route"],
                "cover_bus": alt["bus_id"] if alt else None,
                "reason": f"{b['id']} broken down ({b['breakdown_reason']}); assign {alt['bus_id']}" if alt else "no cover available",
                "priority": "high",
            })
        if not actions:
            actions.append({
                "type": "monitor", "reason": "No redistribution needed; loads and delays within normal range.",
                "priority": "low",
            })
        return actions

    def longest_delays(self):
        by_route = {}
        for b in self.buses:
            if b["status"] == "delayed" and b["delay_min"] > 0:
                d = by_route.setdefault(b["route"], {"delay": 0, "reasons": set(), "buses": 0})
                d["delay"] += b["delay_min"]
                if b["delay_reason"]:
                    d["reasons"].add(b["delay_reason"])
                d["buses"] += 1
        out = sorted(
            [{"route": r, "total_delay": d["delay"], "buses": d["buses"],
              "reasons": sorted(d["reasons"])} for r, d in by_route.items()],
            key=lambda x: -x["total_delay"],
        )
        return out

    def maintenance_buses(self):
        return [b for b in self.buses if b["status"] == "maintenance"]

    def unavailable_buses(self):
        out = []
        for b in self.buses:
            if b["status"] == "breakdown":
                out.append({"bus": b["id"], "route": b["route"], "reason": b["breakdown_reason"], "type": "breakdown"})
        return out

    def child_bus(self, child_hint):
        """Find the bus serving a stop the parent mentions, else fullest bus."""
        for sid, alias in network.STOP_ALIASES.items():
            if alias == child_hint:
                buses = [b for b in self.running_buses()
                         if child_hint in [s["id"] for s in network.ROUTE_BY_ID[b["route"]]["stops"]]]
                if buses:
                    return min(buses, key=lambda b: self.eta_to_stop(b, child_hint) or 999)
        return max(self.running_buses(), key=lambda b: b["load_factor"])

    def child_safety(self, child_hint):
        bus = self.child_bus(child_hint)
        if not bus:
            return {"safe": False, "reason": "No running bus found"}
        issues = []
        if bus["status"] == "breakdown":
            issues.append(f"bus broken down: {bus['breakdown_reason']}")
        if bus["status"] == "delayed":
            issues.append(f"running {bus['delay_min']} min behind schedule ({bus['delay_reason']})")
        for inc in self.incidents:
            if inc.get("bus") == bus["id"]:
                issues.append(f"incident: {inc['reason']}")
        return {
            "safe": not issues, "issues": issues, "bus": bus["id"], "route": bus["route"],
            "driver": bus["driver"], "occupancy": self.occupancy(bus), "capacity": bus["capacity"],
            "delay_min": bus["delay_min"],
        }