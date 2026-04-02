import re
from collections import deque
from typing import Any, Dict, Iterable, List, Optional, Set


AGGREGATE_RE = re.compile(
    r"(?i)(bridge-aggregation|port-?channel|bundle-ether|^ae[\d/.-]*$|^po[\d/.-]*$|^bond[\d/.-]*$|^lag[\d/.-]*$)"
)
PHYSICAL_RE = re.compile(
    r"(?i)(gigabitethernet|fastethernet|ethernet|xgigabitethernet|ten-gigabitethernet|twentyfivegige|fortygige|hundredgige|^gi[\d/.-]+$|^ge[\d/.-]+$|^eth[\d/.-]+$|^fa[\d/.-]+$|^te[\d/.-]+$|^xe-[\d/.-]+$|^et-[\d/.-]+$)"
)
UPLINK_RE = re.compile(r"(?i)(uplink|trunk|core|aggregation|agg\b|dist\b|distribution|interconnect|\bto[-_ ])")


def _normalized_id(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def candidate_id(candidate: Dict[str, Any]) -> str:
    return str(candidate.get("candidate_id") or f"{candidate.get('device_id', '')}:{candidate.get('port_id', '')}")


def build_port_stack_members(mappings: Iterable[Dict[str, Any]]) -> Dict[str, Set[str]]:
    members: Dict[str, Set[str]] = {}

    for item in mappings or []:
        if not isinstance(item, dict):
            continue
        status = str(item.get("ifStackStatus") or "").strip().lower()
        if status and status != "active":
            continue

        high = _normalized_id(item.get("port_id_high"))
        low = _normalized_id(item.get("port_id_low"))
        if not high or not low:
            continue

        members.setdefault(high, set()).add(low)

    return members


def candidate_related_local_port_ids(
    candidate: Dict[str, Any],
    stack_members_by_device: Dict[str, Dict[str, Set[str]]],
) -> Set[str]:
    port_id = _normalized_id(candidate.get("port_id"))
    device_id = _normalized_id(candidate.get("device_id"))

    related = {port_id} if port_id else set()
    related.update(stack_members_by_device.get(device_id, {}).get(port_id, set()))
    return related


def classify_candidate(
    candidate: Dict[str, Any],
    stack_members_by_device: Dict[str, Dict[str, Set[str]]],
) -> Dict[str, Any]:
    interface = str(candidate.get("interface") or "").strip()
    description = str(candidate.get("description") or "").strip()
    device_id = _normalized_id(candidate.get("device_id"))
    port_id = _normalized_id(candidate.get("port_id"))
    stack_members = stack_members_by_device.get(device_id, {}).get(port_id, set())

    is_aggregate = bool(stack_members) or bool(AGGREGATE_RE.search(interface)) or bool(AGGREGATE_RE.search(description))
    is_physical = bool(PHYSICAL_RE.search(interface))
    uplink_like = bool(UPLINK_RE.search(interface)) or bool(UPLINK_RE.search(description))

    return {
        "is_aggregate": is_aggregate,
        "is_physical": is_physical,
        "uplink_like": uplink_like,
        "stack_members": sorted(stack_members),
    }


def build_candidate_graph(
    candidates: List[Dict[str, Any]],
    links_by_device: Dict[str, List[Dict[str, Any]]],
    stack_members_by_device: Dict[str, Dict[str, Set[str]]],
) -> Dict[str, Set[str]]:
    graph: Dict[str, Set[str]] = {candidate_id(item): set() for item in candidates}
    candidates_by_device: Dict[str, List[Dict[str, Any]]] = {}

    for item in candidates:
        device_id = _normalized_id(item.get("device_id"))
        if not device_id:
            continue
        candidates_by_device.setdefault(device_id, []).append(item)

    for item in candidates:
        source_id = candidate_id(item)
        device_id = _normalized_id(item.get("device_id"))
        related_local_ports = candidate_related_local_port_ids(item, stack_members_by_device)
        if not device_id or not related_local_ports:
            continue

        for link in links_by_device.get(device_id, []):
            if not isinstance(link, dict):
                continue
            if str(link.get("active") or "").strip() not in {"1", "true", "True"}:
                continue

            local_port_id = _normalized_id(link.get("local_port_id"))
            remote_device_id = _normalized_id(link.get("remote_device_id"))
            if local_port_id not in related_local_ports or not remote_device_id or remote_device_id == device_id:
                continue

            for target in candidates_by_device.get(remote_device_id, []):
                graph[source_id].add(candidate_id(target))

    return graph


def reachable_nodes(graph: Dict[str, Set[str]], start: str) -> Set[str]:
    if start not in graph:
        return set()

    seen = {start}
    queue = deque([start])

    while queue:
        current = queue.popleft()
        for neighbor in graph.get(current, set()):
            if neighbor in seen:
                continue
            seen.add(neighbor)
            queue.append(neighbor)

    return seen


def shortest_path(graph: Dict[str, Set[str]], start: str, end: str) -> List[str]:
    if start == end:
        return [start]

    queue = deque([start])
    parents = {start: None}

    while queue:
        current = queue.popleft()
        for neighbor in graph.get(current, set()):
            if neighbor in parents:
                continue
            parents[neighbor] = current
            if neighbor == end:
                path = [end]
                while parents[path[-1]] is not None:
                    path.append(parents[path[-1]])
                return list(reversed(path))
            queue.append(neighbor)

    return []


def score_edge_candidate(
    candidate: Dict[str, Any],
    graph: Dict[str, Set[str]],
    stack_members_by_device: Dict[str, Dict[str, Set[str]]],
) -> int:
    cid = candidate_id(candidate)
    meta = classify_candidate(candidate, stack_members_by_device)

    score = 0
    if not graph.get(cid):
        score += 220
    else:
        score -= 220

    if meta["is_physical"]:
        score += 120

    if meta["is_aggregate"]:
        score -= 220
    else:
        score += 80

    if meta["uplink_like"]:
        score -= 100
    else:
        score += 30

    if candidate.get("interface"):
        score += 5

    return score


def pick_edge_candidate(
    canonical_candidate: Optional[Dict[str, Any]],
    candidates: List[Dict[str, Any]],
    links_by_device: Dict[str, List[Dict[str, Any]]],
    stack_members_by_device: Dict[str, Dict[str, Set[str]]],
) -> Dict[str, Any]:
    if not canonical_candidate:
        return {"selected": None, "graph": {}, "path": [], "scores": {}, "candidates": []}

    canonical_vlan = str(canonical_candidate.get("vlan") or "").strip()
    filtered = []
    for item in candidates:
        if canonical_vlan and str(item.get("vlan") or "").strip() != canonical_vlan:
            continue
        filtered.append(item)

    if not filtered:
        filtered = list(candidates)

    graph = build_candidate_graph(filtered, links_by_device, stack_members_by_device)
    canonical_id = candidate_id(canonical_candidate)
    reachable = reachable_nodes(graph, canonical_id) if canonical_id in graph else {canonical_id}
    reachable_candidates = [item for item in filtered if candidate_id(item) in reachable]
    if not reachable_candidates:
        reachable_candidates = [canonical_candidate]

    scores = {
        candidate_id(item): score_edge_candidate(item, graph, stack_members_by_device)
        for item in reachable_candidates
    }
    canonical_score = scores.get(canonical_id, score_edge_candidate(canonical_candidate, graph, stack_members_by_device))

    leaf_candidates = [
        item for item in reachable_candidates
        if candidate_id(item) != canonical_id and not graph.get(candidate_id(item))
    ]
    pool = leaf_candidates or [item for item in reachable_candidates if candidate_id(item) != canonical_id]

    if not pool:
        return {
            "selected": canonical_candidate,
            "graph": graph,
            "path": [canonical_id],
            "scores": scores,
            "candidates": reachable_candidates,
        }

    ordered = sorted(
        pool,
        key=lambda item: (
            scores.get(candidate_id(item), 0),
            str(item.get("updated_at") or ""),
            str(item.get("interface") or ""),
        ),
        reverse=True,
    )
    best = ordered[0]
    best_score = scores.get(candidate_id(best), 0)
    second_score = scores.get(candidate_id(ordered[1]), 0) if len(ordered) > 1 else None
    ambiguous = second_score is not None and second_score == best_score

    if ambiguous or best_score <= canonical_score:
        selected = canonical_candidate
    else:
        selected = best

    selected_id = candidate_id(selected)
    path = shortest_path(graph, canonical_id, selected_id) or [selected_id]

    return {
        "selected": selected,
        "graph": graph,
        "path": path,
        "scores": scores,
        "candidates": reachable_candidates,
    }
