"""Transimate Assistant agent.

Deterministic natural-language agent that answers campus transport
questions using the live simulation. Every answer includes a reasoning
trace (data -> inference -> recommendation) for transparency.
"""

import re

from . import network


class AgentReply:
    def __init__(self, reply, capability, reasoning, data=None, suggestions=None):
        self.reply = reply
        self.capability = capability
        self.reasoning = reasoning
        self.data = data or {}
        self.suggestions = suggestions or []

    def to_dict(self):
        return {
            "reply": self.reply,
            "capability": self.capability,
            "reasoning": self.reasoning,
            "data": self.data,
            "suggestions": self.suggestions,
        }


CAPABILITY_LABELS = {
    "bus_list": "1. Which buses are running & best route",
    "bus_tracking": "2. Bus location & ETA to pickup",
    "capacity": "3. Seat availability & crowding",
    "delays": "4. Delays / cancellations & reasons",
    "traffic": "5. Traffic, roadblocks & diversions",
    "alternatives": "6. Alternative bus when regular is unavailable",
    "stop_waiting": "7. Students waiting at stops",
    "route_overcrowding": "8. Overcrowded routes & spare capacity",
    "child_safety": "9. Child bus arrival & schedule",
    "child_incident": "10. Child bus safety issues",
    "delay_analytics": "11. Longest delays & root causes",
    "maintenance": "12. Unavailable buses & affected routes",
    "additional_bus": "13. Where an extra bus is needed",
    "breakdown_reroute": "14. Least-disruption breakdown cover",
    "redistribution": "15. Fleet redistribution plan",
    "general": "General query",
}


def _has(text, *words):
    return any(w in text for w in words)


def detect_route(text):
    m = re.search(r"\br\s*([1-5])\b", text)
    if m:
        return f"R{m.group(1)}"
    for r in network.ROUTES:
        if r["name"].lower() in text:
            return r["id"]
    return None


def detect_bus(text):
    m = re.search(r"\bb\s*(\d{1,2})\b", text)
    if m:
        return f"B{m.group(1)}"
    return None


def detect_stop(text):
    for alias, sid in sorted(network.STOP_ALIASES.items(), key=lambda kv: -len(kv[0])):
        if alias in text:
            return sid
    for s in network.ALL_STOPS.values():
        if s["name"].lower() in text:
            return s["id"]
    return None


class TransimateAgent:
    def __init__(self, sim):
        self.sim = sim

    # ------------------------------------------------------------------ --
    def chat(self, message, persona="student"):
        text = message.lower().strip()
        sim = self.sim
        route = detect_route(text)
        bus_id = detect_bus(text)
        stop = detect_stop(text)

        # 10/11 - analytics about delays (admin)
        if _has(text, "longest delay", "delays today", "main reasons", "worst delay"):
            return self.delay_analytics()

        # 3 - capacity (requires a bus reference so it doesn't swallow
        #     "spare capacity" or "where is an additional bus needed")
        if _has(text, "seat", "occupancy", "how many people") and (bus_id or _has(text, "my bus")):
            return self.capacity(bus_id, route)

        # 14 - breakdown reroute (hypothetical or real)
        if _has(text, "breaks down", "break down", "broken down") and _has(text, "which", "available", "take its", "cover", "if"):
            return self.breakdown_reroute(bus_id or self._bus_on_route(route))

        # 15 - redistribution
        if _has(text, "redistribut", "reallocate", "rebalance", "re-assign", "reassign"):
            return self.redistribution()

        # 13 - additional bus
        if _has(text, "additional bus", "extra bus", "where is an additional", "more buses"):
            return self.additional_bus()

        # 6 - alternative bus
        if _has(text, "alternative", "instead of", "not running") and route:
            return self.alternatives(route)
        if _has(text, "alternative") and not route:
            return self.alternatives(self._busiest_route())

        # 12 - maintenance / unavailable (after alternatives so "unavailable ... alternative" wins)
        if _has(text, "maintenance", "unavailable due to"):
            return self.maintenance(route)

        # 8 - route overcrowding
        if _has(text, "overcrowd", "spare capacity", "which routes are", "nearest routes with spare"):
            return self.route_overcrowding(route)

        # 5 - traffic / roadblock / diversion
        if _has(text, "traffic", "roadblock", "road block", "diversion", "diverted", "jam"):
            return self.traffic(route)

        # 9/10 - child
        if _has(text, "child", "my son", "my daughter", "kid"):
            if _has(text, "safety", "breakdown", "unexpected", "safe", "issue"):
                return self.child_incident(stop, text)
            return self.child_tracking(stop, text)

        # 7 - students waiting
        if _has(text, "waiting", "how many students", "queue"):
            return self.stop_waiting(stop)

        # 4 - delays
        if _has(text, "delayed", "delay", "late", "cancel"):
            return self.delays(bus_id, route)

        # 2 - bus tracking / ETA
        if _has(text, "where is", "when", "expected", "reach my", "eta", "arrive", "pickup", "pick-up"):
            return self.tracking(bus_id, route, stop)

        # 1 - which buses running / best route to campus
        return self.bus_list(route, stop)

    # ------------------------------------------------------------------ --
    def bus_list(self, route, stop):
        sim = self.sim
        snap = sim.snapshot()
        running = [b for b in snap["buses"] if b["status"] in ("running", "delayed")]
        lines = [f"Right now ({snap['clock']}), {len(running)} of {len(snap['buses'])} buses are on the road."]
        reasons = []
        if route:
            on_route = [b for b in running if b["route"] == route]
            reasons.append(f"On {route} ({network.ROUTE_BY_ID[route]['name']}): {len(on_route)} running.")
            lines.append(f"On route {route}: " + ", ".join(
                f"{b['id']} -> {b['next_stop']} ({b['occupancy']}/{b['capacity']} seats)"
                for b in on_route))
        if stop:
            name = network.ALL_STOPS[stop]["name"]
            servable = [b for b in running if stop in [s["id"] for s in network.ROUTE_BY_ID[b["route"]]["stops"]]]
            best = min(servable, key=lambda b: sim.eta_to_stop(b, stop) or 999) if servable else None
            if best:
                eta = sim.eta_to_stop(best, stop)
                lines.append(
                    f"To reach {name}, take bus {best['id']} (route {best['route']}): "
                    f"arrives in ~{eta} min with {sim.spare_seats(best)} seats free.")
                reasons.append(f"{best['id']} has the shortest ETA ({eta} min) to {name} among {len(servable)} buses serving it.")
            else:
                lines.append(f"No running bus currently serves {name}.")
        else:
            best = min(running, key=lambda b: b["load_factor"]) if running else None
            if best:
                lines.append(
                    f"Least crowded right now: bus {best['id']} on {best['route']} "
                    f"({best['occupancy']}/{best['capacity']}, {int(best['load_factor']*100)}% full).")
        broken = [b for b in snap["buses"] if b["status"] == "breakdown"]
        if broken:
            lines.append("Note: " + ", ".join(f"{b['id']} ({b['route']}) is out of service" for b in broken) + ".")
        return AgentReply(
            " ".join(lines), "bus_list",
            reasons + ["Counted only buses with status running/delayed as 'on the road'."],
            {"running": running, "broken": [b["id"] for b in broken]},
            ["Where is bus " + (running[0]["id"] if running else "B1") + " now?",
             "How many seats are available on " + (running[0]["id"] if running else "B1") + "?",
             "Which routes are overcrowded right now?"],
        )

    def tracking(self, bus_id, route, stop):
        sim = self.sim
        if bus_id:
            bus = next((b for b in sim.snapshot()["buses"] if b["id"] == bus_id), None)
            if not bus:
                return AgentReply(f"I couldn't find bus {bus_id}. Try B1-B12.", "bus_tracking", [])
            pos = bus["position"]
            ns = bus["next_stop"]
            lines = [f"Bus {bus_id} (route {bus['route']}) is at ({pos[0]:.0f}, {pos[1]:.0f}) heading to {ns}.",
                     f"Status: {bus['status']}" + (f", {bus['delay_min']} min behind schedule" if bus["status"] == "delayed" else "") + "."]
            if stop:
                eta = sim.eta_to_stop(sim._bus(bus_id), stop)
                if eta is not None:
                    lines.append(f"It should reach {network.ALL_STOPS[stop]['name']} in about {eta} minutes.")
                else:
                    lines.append(f"Bus {bus_id} does not serve {network.ALL_STOPS[stop]['name']}.")
            return AgentReply(" ".join(lines), "bus_tracking",
                              [f"Located {bus_id} on route polyline at current tick.",
                               f"Next stop computed from distance along route: {ns}."] +
                              ([f"ETA to {stop} = remaining distance / speed (+ delay buffer)."] if stop else []),
                              {"bus": bus})
        if stop:
            name = network.ALL_STOPS[stop]["name"]
            serving = [b for b in sim.running_buses()
                       if stop in [s["id"] for s in network.ROUTE_BY_ID[b["route"]]["stops"]]]
            if not serving:
                return AgentReply(f"No bus is currently serving {name}.", "bus_tracking", [])
            entries = []
            for b in serving:
                eta = sim.eta_to_stop(b, stop)
                entries.append((eta, b))
            entries.sort(key=lambda x: x[0])
            parts = []
            for eta, b in entries[:4]:
                parts.append(f"{b['id']} (route {b['route']}) in ~{eta} min")
            lines = [f"Next buses to reach {name}: " + "; ".join(parts) + "."]
            best = entries[0][1]
            lines.append(f"Take {best['id']}: {sim.spare_seats(best)} seats free, {best['status']}.")
            return AgentReply(" ".join(lines), "bus_tracking",
                              [f"Found {len(serving)} buses serving {name}.",
                               "ETA computed from each bus's position along its route and current speed/delay.",
                               f"Recommended earliest arrival: {best['id']}."],
                              {"etaps": [{"bus": b["id"], "eta": e} for e, b in entries]})
        return self.bus_list(route, stop)

    def capacity(self, bus_id, route):
        sim = self.sim
        if bus_id:
            b = next((x for x in sim.snapshot()["buses"] if x["id"] == bus_id), None)
            if not b:
                return AgentReply(f"I couldn't find bus {bus_id}. Try B1-B12.", "capacity", [])
            pct = int(b["load_factor"] * 100)
            verdict = ("overcrowded - expect a packed ride" if pct >= 100
                       else "filling up, likely to get crowded soon" if pct >= 80
                       else "comfortable, unlikely to be overcrowded")
            return AgentReply(
                f"Bus {bus_id} (route {bus_id and b['route']}) has {b['occupancy']} passengers "
                f"out of {b['capacity']} seats - {sim.spare_seats(sim._bus(bus_id))} seats available ({pct}% full). "
                f"It is {verdict}.",
                "capacity",
                [f"Occupancy estimate {b['occupancy']}/{b['capacity']} from live load simulation.",
                 f"Load factor {pct}% vs thresholds: <80% comfortable, 80-99% filling, >=100% overcrowded."],
                {"bus": b},
                [f"Which routes are overcrowded right now?", f"Where is bus {bus_id} now?"])
        buses = sim.snapshot()["buses"]
        free = [(b["id"], sim.spare_seats(sim._bus(b["id"])), b["route"]) for b in buses if b["status"] in ("running", "delayed")]
        free.sort(key=lambda x: -x[1])
        top = free[:3]
        return AgentReply(
            "Most seats available right now: " + "; ".join(f"{bid} on {r} with {s} free seats" for bid, s, r in top) + ".",
            "capacity",
            ["Ranked all running buses by spare seats = capacity - current occupancy."],
            {"freest": top})

    def delays(self, bus_id, route):
        sim = self.sim
        snap = sim.snapshot()
        if bus_id:
            b = next((x for x in snap["buses"] if x["id"] == bus_id), None)
            if not b:
                return AgentReply(f"I couldn't find bus {bus_id}.", "delays", [])
            if b["status"] == "breakdown":
                return AgentReply(f"Bus {bus_id} is cancelled/broken down: {b['breakdown_reason']}. "
                                  f"Use the alternatives capability to find a replacement.",
                                  "delays", [f"{bus_id} status=breakdown in live snapshot."],
                                  {"bus": b})
            if b["status"] == "delayed":
                return AgentReply(f"Yes - bus {bus_id} (route {b['route']}) is delayed by about {b['delay_min']} minutes. "
                                  f"Reason: {b['delay_reason']}. It is not cancelled; it is still en route to {b['next_stop']}.",
                                  "delays", [f"{bus_id} flagged delayed with {b['delay_min']} min backlog.",
                                             "Delay reason propagated from incident feed."],
                                  {"bus": b})
            return AgentReply(f"Bus {bus_id} is running on schedule - no delay or cancellation reported.",
                              "delays", [f"{bus_id} status=running, delay_min=0."], {"bus": b})
        affected = [b for b in snap["buses"] if b["status"] in ("delayed", "breakdown")]
        if not affected:
            return AgentReply("No delays or cancellations on the network right now - all buses are running on schedule.",
                              "delays", ["Scanned all 12 buses: none flagged delayed/breakdown."])
        lines = ["Currently affected buses:"]
        for b in affected:
            if b["status"] == "breakdown":
                lines.append(f"- {b['id']} ({b['route']}): CANCELLED - {b['breakdown_reason']}.")
            else:
                lines.append(f"- {b['id']} ({b['route']}): delayed ~{b['delay_min']} min - {b['delay_reason']}.")
        return AgentReply(" ".join(lines), "delays",
                          [f"{len(affected)} of {len(snap['buses'])} buses not on schedule.",
                           "Reasons taken from live incident feed."],
                          {"affected": [b["id"] for b in affected]})

    def traffic(self, route):
        sim = self.sim
        zones = sim.traffic_zones
        diversions = [i for i in sim.incidents if i["type"] == "diversion"]
        lines = []
        if zones:
            for z in zones:
                near = [s["name"] for s in network.ALL_STOPS.values() if network.dist(s["pos"], z["pos"]) < z["radius"] + 40]
                lines.append(f"{z['severity'].capitalize()} traffic near {', '.join(near) or 'campus roads'}: {z['reason']}.")
        if diversions:
            for d in diversions:
                lines.append(f"Route {d['route']} diverted: {d['reason']}.")
        if not lines:
            return AgentReply("No traffic incidents, roadblocks, or diversions reported right now - all routes are clear.",
                              "traffic", ["Traffic zone list and diversion incidents are empty at this tick."])
        if route:
            rel = [l for l in lines if route in l] or lines
            extra = "" if any(route in l for l in lines) else f" None affect route {route} directly."
            lines = [f"On route {route}: {extra}"] + rel if extra else lines
        return AgentReply(" ".join(lines), "traffic",
                          [f"{len(zones)} active traffic zones, {len(diversions)} diversions.",
                           "Buses inside a zone run at 35-60% speed; diverted routes at 50%."],
                          {"zones": zones, "diversions": diversions})

    def alternatives(self, route):
        sim = self.sim
        rs = sim.route_status()
        if rs.get(route, {}).get("broken", 0) > 0 or rs.get(route, {}).get("delayed", 0) > 0:
            # find replacement route sharing a stop
            broken = [b["id"] for b in sim.buses if b["route"] == route and b["status"] == "breakdown"]
            route_stops = set(s["id"] for s in network.ROUTE_BY_ID[route]["stops"])
            cands = []
            for rid, st in rs.items():
                if rid == route:
                    continue
                shared = route_stops & set(s["id"] for s in network.ROUTE_BY_ID[rid]["stops"])
                spare = sum(sim.spare_seats(b) for b in sim.buses if b["route"] == rid and b["status"] == "running")
                if shared:
                    cands.append((rid, len(shared), spare))
            cands.sort(key=lambda x: (-x[1], -x[2]))
            if cands:
                rid, shared, spare = cands[0]
                shared_names = [network.ALL_STOPS[s]["name"] for s in (route_stops & set(x["id"] for x in network.ROUTE_BY_ID[rid]["stops"]))]
                return AgentReply(
                    f"Bus on route {route} is unavailable right now. Best alternative: route {rid} "
                    f"({network.ROUTE_BY_ID[rid]['name']}), which shares {shared} stop(s) ({', '.join(shared_names)}) "
                    f"and has ~{spare} spare seats across its running buses. "
                    f"Walk to {shared_names[0]} and board the next {rid} bus.",
                    "alternatives",
                    [f"{route} has disruptions; searched other routes for shared stops.",
                     f"{rid} shares {shared} stops and has the most spare capacity ({spare} seats) among connected routes."],
                    {"alternative_route": rid, "shared_stops": shared_names},
                    [f"Where is the next {rid} bus?", f"Is {route} delayed?"])
        # no disruptions - suggest least crowded route
        best = min(rs.items(), key=lambda kv: kv[1]["avg_load"])
        return AgentReply(
            f"Your regular route {route} is actually running fine right now. "
            f"If you want a less crowded option, route {best[0]} has the lowest load ({int(best[1]['avg_load']*100)}%).",
            "alternatives", ["No breakdown/delay found on the given route; suggested lowest-load route instead."],
            {"alternative_route": best[0]})

    def stop_waiting(self, stop):
        sim = self.sim
        if stop:
            name = network.ALL_STOPS[stop]["name"]
            c = sim.waiting.get(stop, 0)
            serving = [r["id"] for r in network.ROUTES if stop in [s["id"] for s in r["stops"]]]
            return AgentReply(
                f"{c} students are currently waiting at {name} (served by routes {', '.join(serving)}).",
                "stop_waiting",
                [f"Live waiting count for {name} = {c}.",
                 "Arrivals rise at peak hours; nearby buses pick up 2-10 students when passing."],
                {"stop": name, "waiting": c})
        rows = sim.stop_waiting_list()
        top = rows[:6]
        lines = ["Students waiting by stop (highest first):"]
        lines += [f"- {r['name']}: {r['count']}" for r in top]
        return AgentReply(" ".join(lines), "stop_waiting",
                          ["Sorted all stops by current waiting count."],
                          {"stops": rows})

    def route_overcrowding(self, route):
        sim = self.sim
        over = sim.overcrowded_routes()
        rs = sim.route_status()
        lines = []
        if over:
            lines.append("Overcrowded routes right now: " +
                         ", ".join(f"{r} ({int(l*100)}% avg load)" for r, l in over) + ".")
            worst = over[0][0]
            alts = [(rid, st) for rid, st in rs.items() if rid != worst and st["avg_load"] < 0.85]
            alts.sort(key=lambda kv: kv[1]["avg_load"])
            if alts:
                rid, st = alts[0]
                spare = sum(sim.spare_seats(b) for b in sim.buses if b["route"] == rid and b["status"] == "running")
                lines.append(f"Nearby route {rid} ({network.ROUTE_BY_ID[rid]['name']}) has spare capacity: "
                             f"avg load {int(st['avg_load']*100)}%, ~{spare} free seats.")
        else:
            least = sorted(rs.items(), key=lambda kv: kv[1]["avg_load"])[:2]
            lines.append("No route is overcrowded right now. Lowest loads: " +
                         ", ".join(f"{r} ({int(st['avg_load']*100)}%)" for r, st in least) + ".")
        return AgentReply(" ".join(lines), "route_overcrowding",
                          ["Average load per route from live bus load factors.",
                           "Overcrowded = avg load >= 95%; spare capacity = running buses' free seats."],
                          {"overcrowded": over, "routes": rs})

    def child_tracking(self, stop, text):
        sim = self.sim
        hint = stop or self._guess_child_stop(text)
        rep = sim.child_safety(hint) if hint else None
        if not rep or rep.get("safe") is None:
            return self.bus_list(None, stop)
        b = next(x for x in sim.snapshot()["buses"] if x["id"] == rep["bus"])
        lines = [f"Your child's bus {b['id']} (route {b['route']}, driver {b['driver']}) is on the road.",
                 f"Next stop: {b['next_stop']}. Current load: {b['occupancy']}/{b['capacity']} seats."]
        if b["status"] == "delayed":
            lines.append(f"It is running ~{b['delay_min']} min behind schedule ({b['delay_reason']}).")
        else:
            lines.append("It is running according to schedule.")
        return AgentReply(" ".join(lines), "child_safety",
                          [f"Matched parent's stop hint to bus {b['id']} serving it.",
                           "Schedule adherence from bus status/delay fields."],
                          {"bus": b})

    def child_incident(self, stop, text):
        sim = self.sim
        hint = stop or self._guess_child_stop(text)
        rep = sim.child_safety(hint)
        b = next((x for x in sim.snapshot()["buses"] if x["id"] == rep.get("bus")), None)
        if rep.get("safe", False):
            return AgentReply(
                f"No safety issues detected for your child's bus {rep['bus']} (route {rep['route']}, driver {rep['driver']}). "
                f"It is on schedule with {rep['occupancy']}/{rep['capacity']} seats occupied.",
                "child_incident", ["Cross-checked bus status, active incidents, and delay flags: all clear."],
                {"bus": b})
        return AgentReply(
            f"Attention needed for bus {rep['bus']}: " + "; ".join(rep["issues"]) + ". "
            "The transport desk has been notified and a replacement is being arranged if it is a breakdown.",
            "child_incident",
            [f"Checked live incidents affecting bus {rep['bus']} or its route.",
             "Any delay > 0 or breakdown incident is surfaced to parents immediately."],
            {"bus": b, "issues": rep["issues"]})

    def delay_analytics(self):
        sim = self.sim
        rows = sim.longest_delays()
        lines = []
        if rows:
            lines.append("Routes with the longest delays today:")
            for r in rows[:4]:
                reasons = "; ".join(r["reasons"]) if r["reasons"] else "no single dominant cause"
                lines.append(f"- {r['route']}: {r['buses']} bus(es) delayed, total {r['total_delay']} delay-min. Main reasons: {reasons}.")
        else:
            lines.append("No significant delays recorded in the last 2 hours - all routes are on schedule today.")
        traffic = sim.traffic_zones
        if traffic:
            lines.append(f"Current traffic hotspots: {', '.join(t['reason'] for t in traffic)}.")
        return AgentReply(" ".join(lines), "delay_analytics",
                          ["Aggregated delay minutes per route over the rolling 2-hour window.",
                           "Root causes grouped from bus delay_reason and incident feed."],
                          {"delays": rows})

    def maintenance(self, route):
        sim = self.sim
        unavail = sim.unavailable_buses()
        lines = []
        if unavail:
            lines.append("Buses currently unavailable:")
            for u in unavail:
                lines.append(f"- {u['bus']} ({u['route']}): {u['type']} - {u['reason']}.")
            affected = sorted(set(u["route"] for u in unavail))
            lines.append(f"Affected routes: {', '.join(affected)}. Replacement buses are being dispatched (see alternatives).")
        else:
            lines.append("No buses are unavailable right now - the full fleet is operational.")
        broken = [b for b in sim.buses if b["status"] == "breakdown"]
        if broken:
            for b in broken:
                alt = sim.breakdown_reroute(b["id"])
                if alt:
                    lines.append(f"Suggested cover for {b['id']}: bus {alt['bus_id']} can arrive in ~{alt['eta_min']} min.")
        return AgentReply(" ".join(lines), "maintenance",
                          ["Unavailable = breakdown or maintenance status in fleet snapshot.",
                           "For each broken bus, least-disruption cover computed from spare seats and travel time."],
                          {"unavailable": unavail})

    def additional_bus(self):
        sim = self.sim
        rec = sim.additional_bus_recommendation()
        if not rec:
            return AgentReply("Current demand does not justify an additional bus anywhere right now.",
                              "additional_bus", [])
        return AgentReply(
            f"Yes - an additional bus is needed at {rec['stop_name']} (stop {rec['stop_id']}). "
            f"{rec['waiting']} students are waiting there while route {rec['route']} has only ~{rec['spare']} spare seats. "
            f"Recommend dispatching an extra bus on {rec['route']} to {rec['stop_name']}.",
            "additional_bus",
            [f"Scored stops by waiting_count*10 - available_spare_seats on serving route.",
             f"{rec['stop_name']} scores highest ({rec['waiting']} waiting vs {rec['spare']} spare seats on {rec['route']}).",
             "Dispatch recommendation: extra bus on the same route so no transfer is needed."],
            {"recommendation": rec},
            ["Which routes are overcrowded right now?",
             "How should the buses be redistributed right now?"])

    def breakdown_reroute(self, bus_id):
        sim = self.sim
        broken = [b for b in sim.buses if b["status"] == "breakdown"]
        if not broken and not bus_id:
            # hypothetical: assume the busiest running bus fails
            busiest = max((b for b in sim.running_buses()), key=lambda b: b["load_factor"])
            alt = sim.breakdown_reroute(busiest["id"])
            return AgentReply(
                f"If bus {busiest['id']} ({busiest['route']}, currently the fullest at "
                f"{int(busiest['load_factor']*100)}%) broke down right now, the least-disruption cover would be "
                f"bus {alt['bus_id']} (route {alt['route']}): it can reach the affected route in ~{alt['eta_min']} min "
                f"with {alt['spare']} spare seats for the {alt['waiting_on_route']} students waiting along {busiest['route']}. "
                f"No bus is currently broken down - this is a what-if analysis.",
                "breakdown_reroute",
                [f"No live breakdown; simulated failure of busiest bus {busiest['id']}.",
                 "Candidate buses scored by travel time to route start, route familiarity, and spare-seat coverage.",
                 f"Selected {alt['bus_id']} with the lowest disruption score."],
                {"hypothetical_bus": busiest["id"], "cover": alt})
        target = sim._bus(bus_id) if bus_id else broken[0]
        if not target:
            return AgentReply(f"I couldn't find bus {bus_id}.", "breakdown_reroute", [])
        if target["status"] != "breakdown":
            return AgentReply(
                f"Bus {target['id']} is not broken down, but if it were: bus "
                f"{sim.breakdown_reroute(target['id'])['bus_id']} would cover with least disruption.",
                "breakdown_reroute", [f"Ran the least-disruption cover algorithm for hypothetical failure of {target['id']}."])
        alt = sim.breakdown_reroute(target["id"])
        if not alt:
            return AgentReply(f"No available bus can cover {target['id']} right now.", "breakdown_reroute", [])
        return AgentReply(
            f"Bus {target['id']} ({target['route']}) is broken down ({target['breakdown_reason']}). "
            f"Least-disruption cover: bus {alt['bus_id']} (route {alt['route']}) can reach the affected route in "
            f"~{alt['eta_min']} min with {alt['spare']} spare seats for the {alt['waiting_on_route']} students waiting along {target['route']}.",
            "breakdown_reroute",
            [f"{target['id']} broken down; {alt['waiting_on_route']} students waiting on its route.",
             "Candidate buses scored by travel time to route start, route familiarity, and spare-seat coverage.",
             f"Selected {alt['bus_id']} with the lowest disruption score."],
            {"broken_bus": target["id"], "cover": alt})

    def redistribution(self):
        sim = self.sim
        actions = sim.redistribution_plan()
        if len(actions) == 1 and actions[0]["type"] == "monitor":
            return AgentReply(actions[0]["reason"], "redistribution",
                              ["Checked overcrowded routes, spare capacity, and breakdowns: nothing to move."])
        lines = ["Recommended fleet redistribution right now:"]
        for a in actions:
            if a["type"] == "shift_capacity":
                extra = f" Priority action at {a['hot_stop']}." if a.get("hot_stop") else ""
                lines.append(f"- Shift capacity {a['from_route']} -> {a['to_route']}: {a['reason']}.{extra}")
            elif a["type"] == "cover_breakdown":
                lines.append(f"- Cover breakdown: {a['reason']}.")
        return AgentReply(" ".join(lines), "redistribution",
                          ["Compared per-route average load vs spare seats on other routes.",
                           "Matched broken buses with nearest available cover bus.",
                           "Prioritized actions: breakdowns first, then routes over 95% load."],
                          {"actions": actions},
                          ["Where is an additional bus needed right now?",
                           "Which routes are overcrowded right now?"])

    # ------------------------------------------------------------------ --
    def _busiest_route(self):
        rs = self.sim.route_status()
        return max(rs.items(), key=lambda kv: kv[1]["avg_load"])[0]

    def _bus_on_route(self, route):
        if not route:
            return None
        return next((b["id"] for b in self.sim.buses if b["route"] == route), None)

    def _guess_child_stop(self, text):
        # parent likely means the main boarding stop
        return "S1" if _has(text, "pickup", "pick-up", "morning") else None