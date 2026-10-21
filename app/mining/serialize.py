"""Convert native PM4Py model objects into JSON representations for the UI.

Runs inside the mining worker process (imports PM4Py object classes lazily).
Identifiers are generated deterministically from structure so repeated runs
produce comparable output.
"""

from __future__ import annotations

from typing import Any


# --------------------------------------------------------------------------- Petri net
def petri_net(net, im, fm) -> dict:
    places = sorted(net.places, key=lambda p: (-(im[p] if p in im else 0), (fm[p] if p in fm else 0), p.name))
    pid = {p: f"p{i}" for i, p in enumerate(places)}
    transitions = sorted(net.transitions, key=lambda t: (t.label is None, t.label or "", t.name))
    tid = {t: f"t{i}" for i, t in enumerate(transitions)}
    ids = {**pid, **tid}
    arcs = sorted(({"source": ids[a.source], "target": ids[a.target], "weight": int(a.weight)} for a in net.arcs),
                  key=lambda a: (a["source"], a["target"]))
    return {
        "type": "petri_net",
        "places": [{"id": pid[p], "name": p.name, "initial": int(im[p]) if p in im else 0,
                    "final": int(fm[p]) if p in fm else 0} for p in places],
        "transitions": [{"id": tid[t], "label": t.label, "silent": t.label is None} for t in transitions],
        "arcs": arcs,
        "has_initial_marking": len(im) > 0,
        "has_final_marking": len(fm) > 0,
    }


def petri_size(rep: dict) -> dict:
    visible = sum(1 for t in rep["transitions"] if not t["silent"])
    return {"places": len(rep["places"]), "transitions": len(rep["transitions"]), "visible_transitions": visible,
            "silent_transitions": len(rep["transitions"]) - visible, "arcs": len(rep["arcs"])}


# --------------------------------------------------------------------------- process tree
_TREE_OPS = {"->": "sequence", "X": "xor", "+": "parallel", "*": "loop", "O": "or"}


def process_tree(tree) -> dict:
    def node(t) -> dict:
        op = str(t.operator) if t.operator is not None else None
        if op is None:
            return {"kind": "silent" if t.label is None else "activity", "label": t.label}
        return {"kind": _TREE_OPS.get(op, op), "operator": op, "children": [node(c) for c in t.children]}

    return {"type": "process_tree", "root": node(tree), "text": str(tree)}


def tree_size(rep: dict) -> dict:
    counts = {"nodes": 0, "leaves": 0, "operators": 0, "silent_leaves": 0}

    def walk(n):
        counts["nodes"] += 1
        if "children" in n:
            counts["operators"] += 1
            for c in n["children"]:
                walk(c)
        else:
            counts["leaves"] += 1
            counts["silent_leaves"] += n["kind"] == "silent"

    walk(rep["root"])
    return counts


# --------------------------------------------------------------------------- heuristics net
def heuristics_net(hn) -> dict:
    nodes, edges = [], []
    starts = {}
    for d in hn.start_activities:
        starts.update(d)
    ends = {}
    for d in hn.end_activities:
        ends.update(d)
    for name in sorted(hn.nodes):
        n = hn.nodes[name]
        nodes.append({"id": name, "occurrences": int(n.node_occ), "start": int(starts.get(name, 0)),
                      "end": int(ends.get(name, 0))})
        for target, conns in n.output_connections.items():
            for e in conns:
                edges.append({"source": name, "target": target.node_name, "dependency": round(float(e.dependency_value), 4),
                              "frequency": int(e.dfg_value)})
    edges.sort(key=lambda e: (e["source"], e["target"]))
    return {"type": "heuristics_net", "nodes": nodes, "edges": edges,
            "start_activities": starts, "end_activities": ends}


def heuristics_size(rep: dict) -> dict:
    return {"nodes": len(rep["nodes"]), "edges": len(rep["edges"])}


# --------------------------------------------------------------------------- BPMN
def bpmn(model) -> dict:
    from pm4py.objects.bpmn.obj import BPMN

    def kind(n) -> str:
        if isinstance(n, BPMN.Task):
            return "task"
        if isinstance(n, BPMN.ExclusiveGateway):
            return "exclusive_gateway"
        if isinstance(n, BPMN.ParallelGateway):
            return "parallel_gateway"
        if isinstance(n, BPMN.InclusiveGateway):
            return "inclusive_gateway"
        if isinstance(n, BPMN.StartEvent):
            return "start_event"
        if isinstance(n, BPMN.EndEvent):
            return "end_event"
        return type(n).__name__

    raw_nodes = list(model.get_nodes())
    raw_nodes.sort(key=lambda n: (kind(n), n.get_name() or "", n.get_id()))
    nid = {n: f"n{i}" for i, n in enumerate(raw_nodes)}
    nodes = []
    for n in raw_nodes:
        item = {"id": nid[n], "kind": kind(n), "name": n.get_name() if kind(n) == "task" else ""}
        if hasattr(n, "get_gateway_direction"):
            item["direction"] = str(n.get_gateway_direction()).split(".")[-1].lower()
        nodes.append(item)
    flows = sorted(({"source": nid[f.get_source()], "target": nid[f.get_target()]} for f in model.get_flows()),
                   key=lambda f: (f["source"], f["target"]))
    return {"type": "bpmn", "nodes": nodes, "flows": flows}


def bpmn_size(rep: dict) -> dict:
    gateways = sum(1 for n in rep["nodes"] if n["kind"].endswith("gateway"))
    tasks = sum(1 for n in rep["nodes"] if n["kind"] == "task")
    return {"nodes": len(rep["nodes"]), "tasks": tasks, "gateways": gateways, "flows": len(rep["flows"])}


# --------------------------------------------------------------------------- POWL
def powl(model) -> dict:
    from pm4py.objects.powl.obj import OperatorPOWL, SilentTransition, StrictPartialOrder, Transition

    def node(m) -> dict:
        if isinstance(m, SilentTransition):
            return {"kind": "silent", "label": None}
        if isinstance(m, Transition):
            return {"kind": "activity", "label": m.label}
        if isinstance(m, OperatorPOWL):
            return {"kind": str(m.operator).split(".")[-1].lower().replace("operator_", ""),
                    "operator": str(m.operator), "children": [node(c) for c in m.children]}
        if isinstance(m, StrictPartialOrder):
            children = list(m.children)
            order = m.order
            edges = []
            for i, a in enumerate(children):
                for j, b in enumerate(children):
                    if i != j and order.is_edge(a, b):
                        edges.append([i, j])
            return {"kind": "partial_order", "children": [node(c) for c in children], "order": edges}
        return {"kind": type(m).__name__, "label": str(m)}

    return {"type": "powl", "root": node(model), "text": str(model)}


def powl_size(rep: dict) -> dict:
    counts = {"nodes": 0, "activities": 0, "partial_orders": 0, "order_edges": 0, "operators": 0}

    def walk(n):
        counts["nodes"] += 1
        if n["kind"] == "activity":
            counts["activities"] += 1
        elif n["kind"] == "partial_order":
            counts["partial_orders"] += 1
            counts["order_edges"] += len(n["order"])
        elif "children" in n:
            counts["operators"] += 1
        for c in n.get("children", []):
            walk(c)

    walk(rep["root"])
    return counts


# --------------------------------------------------------------------------- transition system
def transition_system(ts, params: dict) -> dict:
    states = sorted(ts.states, key=lambda s: str(s.name))
    sid = {s: f"s{i}" for i, s in enumerate(states)}
    trans = sorted(({"source": sid[t.from_state], "target": sid[t.to_state], "label": str(t.name)} for t in ts.transitions),
                   key=lambda t: (t["source"], t["target"], t["label"]))
    return {"type": "transition_system", "states": [{"id": sid[s], "name": _state_name(s.name),
                                                    "incoming": len(s.incoming), "outgoing": len(s.outgoing)}
                                                   for s in states],
            "transitions": trans, **params}


def _state_name(name: Any) -> str:
    if isinstance(name, (list, tuple)):
        return "[" + ", ".join(map(str, name)) + "]"
    return str(name)


def ts_size(rep: dict) -> dict:
    return {"states": len(rep["states"]), "transitions": len(rep["transitions"])}


# --------------------------------------------------------------------------- declarative
def declare(model: dict) -> dict:
    constraints = []
    for template in sorted(model):
        for acts, vals in model[template].items():
            activities = list(acts) if isinstance(acts, tuple) else [acts]
            constraints.append({"template": template, "activities": activities,
                                "support": int(vals.get("support", 0)), "confidence": int(vals.get("confidence", 0))})
    constraints.sort(key=lambda c: (c["template"], c["activities"]))
    return {"type": "declare", "constraints": constraints}


def declare_to_native(rep: dict) -> dict:
    model: dict = {}
    for c in rep["constraints"]:
        key = tuple(c["activities"]) if len(c["activities"]) > 1 else c["activities"][0]
        model.setdefault(c["template"], {})[key] = {"support": c["support"], "confidence": c["confidence"]}
    return model


def declare_size(rep: dict) -> dict:
    templates = {c["template"] for c in rep["constraints"]}
    return {"constraints": len(rep["constraints"]), "templates": len(templates)}


def log_skeleton(model: dict) -> dict:
    relations = {}
    for k in ("equivalence", "always_after", "always_before", "never_together", "directly_follows"):
        relations[k] = sorted([list(p) for p in model.get(k, [])])
    activ_freq = {a: sorted(int(x) for x in v) for a, v in sorted(model.get("activ_freq", {}).items())}
    return {"type": "log_skeleton", "relations": relations, "activ_freq": activ_freq}


def log_skeleton_to_native(rep: dict) -> dict:
    model = {k: {tuple(p) for p in v} for k, v in rep["relations"].items()}
    model["activ_freq"] = {a: set(v) for a, v in rep["activ_freq"].items()}
    return model


def log_skeleton_size(rep: dict) -> dict:
    out = {k: len(v) for k, v in rep["relations"].items()}
    out["activities"] = len(rep["activ_freq"])
    return out


def temporal_profile(model: dict) -> dict:
    pairs = [{"source": a, "target": b, "mean_s": float(m), "std_s": float(s)} for (a, b), (m, s) in model.items()]
    pairs.sort(key=lambda p: (p["source"], p["target"]))
    return {"type": "temporal_profile", "pairs": pairs}


def temporal_profile_to_native(rep: dict) -> dict:
    return {(p["source"], p["target"]): (p["mean_s"], p["std_s"]) for p in rep["pairs"]}


def temporal_size(rep: dict) -> dict:
    return {"activity_pairs": len(rep["pairs"])}


SIZE_FUNCTIONS = {
    "petri_net": petri_size, "process_tree": tree_size, "heuristics_net": heuristics_size, "bpmn": bpmn_size,
    "powl": powl_size, "transition_system": ts_size, "declare": declare_size, "log_skeleton": log_skeleton_size,
    "temporal_profile": temporal_size,
}


def size_of(rep: dict) -> dict:
    return SIZE_FUNCTIONS[rep["type"]](rep)
