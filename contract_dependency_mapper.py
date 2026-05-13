# v0.1.0
# { "Depends": "py-genlayer:latest" }

from genlayer import *
import json

ERROR_EXPECTED = "[EXPECTED]"


def _raise_user_error(message: str) -> None:
    err_cls = getattr(gl, "UserError", Exception)
    raise err_cls(message)


class ContractDependencyMapper(gl.Contract):
    """Builds contract call relationship graphs and risk diagnostics."""

    analyses: str
    next_analysis_id: u256

    def __init__(self):
        """Initialize storage.

        Parameters:
            None.

        Returns:
            None.
        """
        self.analyses = "{}"
        self.next_analysis_id = 1

    def _detect_cycle(self, edges):
        graph = {}
        for e in edges:
            src = str(e.get("from", ""))
            dst = str(e.get("to", ""))
            if src not in graph:
                graph[src] = []
            graph[src].append(dst)

        visited = set()
        stack = set()

        def dfs(node):
            if node in stack:
                return True
            if node in visited:
                return False
            visited.add(node)
            stack.add(node)
            for nei in graph.get(node, []):
                if dfs(nei):
                    return True
            stack.remove(node)
            return False

        for n in graph.keys():
            if dfs(n):
                return True
        return False

    @gl.public.write
    def analyze_system(self, system_spec_json: str) -> str:
        """Analyze call relationships and dependency risks.

        Parameters:
            system_spec_json: JSON object with contracts and calls list.

        Returns:
            Analysis id string.
        """
        try:
            spec = json.loads(system_spec_json)
        except Exception:
            _raise_user_error(f"{ERROR_EXPECTED} invalid system_spec_json")

        contracts = spec.get("contracts", [])
        calls = spec.get("calls", [])
        if not isinstance(contracts, list) or len(contracts) == 0:
            _raise_user_error(f"{ERROR_EXPECTED} contracts must be non empty list")
        if not isinstance(calls, list):
            _raise_user_error(f"{ERROR_EXPECTED} calls must be list")

        contract_set = set()
        for c in contracts:
            name = str(c).strip()
            if len(name) < 2:
                _raise_user_error(f"{ERROR_EXPECTED} invalid contract name")
            contract_set.add(name)

        edges = []
        rw_patterns = []
        reentrancy_signals = []

        for call in calls:
            if not isinstance(call, dict):
                _raise_user_error(f"{ERROR_EXPECTED} call entry must be object")
            src = str(call.get("from", "")).strip()
            dst = str(call.get("to", "")).strip()
            mode = str(call.get("mode", "read")).strip().lower()
            data = str(call.get("data", "")).strip()

            if src not in contract_set or dst not in contract_set:
                _raise_user_error(f"{ERROR_EXPECTED} unknown contract in call")
            if mode not in ["read", "write"]:
                _raise_user_error(f"{ERROR_EXPECTED} invalid call mode")

            edge = {"from": src, "to": dst, "mode": mode, "data": data}
            edges.append(edge)
            rw_patterns.append({"from": src, "to": dst, "access": mode})

            if src == dst and mode == "write":
                reentrancy_signals.append(f"self write call on {src}")
            if mode == "write":
                for other in calls:
                    if isinstance(other, dict) and str(other.get("from", "")).strip() == dst and str(other.get("to", "")).strip() == src:
                        reentrancy_signals.append(f"mutual write path {src}<->{dst}")

        has_cycle = self._detect_cycle(edges)
        if has_cycle:
            reentrancy_signals.append("circular dependency chain detected")

        dedup_signals = []
        seen = set()
        for s in reentrancy_signals:
            if s not in seen:
                seen.add(s)
                dedup_signals.append(s)

        analysis_id = str(self.next_analysis_id)
        self.next_analysis_id += 1

        analyses = json.loads(self.analyses)
        analyses[analysis_id] = {
            "analysis_id": analysis_id,
            "requester": str(gl.message.sender_account),
            "contracts": sorted(list(contract_set)),
            "edges": edges,
            "read_write_patterns": rw_patterns,
            "has_circular_dependency": bool(has_cycle),
            "reentrancy_risk_signals": dedup_signals,
            "dependency_chain_count": len(edges),
            "created_at": str(gl.block.timestamp),
        }
        self.analyses = json.dumps(analyses)
        return analysis_id

    @gl.public.view
    def get_analysis(self, analysis_id: str) -> str:
        """Read one analysis result.

        Parameters:
            analysis_id: Analysis id.

        Returns:
            Analysis JSON string.
        """
        analyses = json.loads(self.analyses)
        key = str(analysis_id)
        if key not in analyses:
            _raise_user_error(f"{ERROR_EXPECTED} analysis not found")
        return json.dumps(analyses[key])

    @gl.public.view
    def get_all_analyses(self) -> str:
        """Read all analyses.

        Parameters:
            None.

        Returns:
            Analyses map JSON string.
        """
        return self.analyses
