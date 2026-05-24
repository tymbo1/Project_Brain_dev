#!/usr/bin/env python3
"""
scos_tools.py — Selyrion Cognitive Operating System: Tool Registry

The nervous system of SCOS. Every callable capability is registered here with:
  - input/output contracts
  - side_effects declaration
  - confidence_required threshold
  - safety level
  - execution tracing (trace_id threads through all tool calls)

Design rule: LLMs propose. Tools execute.
LLMs reason about what to do. Verified tools do it.
No direct state mutation from LLM output.

Usage:
    from scos_tools import registry, new_trace

    trace = new_trace(session_id="replay.abc123")
    result = registry.execute("memory.search", {"query": "kingside attack"}, trace)
    result = registry.execute("graph.query", {"concept": "rook", "depth": 2}, trace)
    trace.finish()

    # List available tools
    registry.list_tools()
"""

import sqlite3, json, time, hashlib, re, sys, os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# ── Paths ─────────────────────────────────────────────────────────────────────

CMS_DB       = str(Path.home() / "resonance_v11.db")
SUPERMODEL_DB = str(Path.home() / "supermodel.db")
CLAUDECODE_DB = str(Path.home() / "claudecode.db")

# ── ANSI ──────────────────────────────────────────────────────────────────────
R    = "\033[0m"; BOLD = "\033[1m"; DIM = "\033[2m"
OK   = "\033[32m"; WARN = "\033[33m"; ERR = "\033[31m"

# ── Execution Trace ───────────────────────────────────────────────────────────

@dataclass
class ToolCall:
    tool_id:     str
    inputs:      dict
    outputs:     dict
    success:     bool
    runtime_ms:  int
    confidence:  float
    error:       str = ""

@dataclass
class ExecutionTrace:
    """
    trace_id threads through all tool calls, parliament deliberations, and
    memory writes in a single cognitive operation. Every step is observable.
    """
    trace_id:    str
    session_id:  str
    intent:      str           = ""
    tool_chain:  list          = field(default_factory=list)   # [ToolCall]
    memory_reads:  list        = field(default_factory=list)   # concept names read
    memory_writes: list        = field(default_factory=list)   # concept names written
    contradictions: list       = field(default_factory=list)
    confidence_flow: list      = field(default_factory=list)   # [(tool, conf)]
    final_output: str          = ""
    started_at:  float         = field(default_factory=time.time)
    finished_at: float         = 0.0

    def record(self, call: ToolCall):
        self.tool_chain.append(call)
        self.confidence_flow.append((call.tool_id, call.confidence))

    def finish(self, output: str = ""):
        self.finished_at = time.time()
        self.final_output = output
        self._save()

    def runtime_ms(self) -> int:
        end = self.finished_at or time.time()
        return int((end - self.started_at) * 1000)

    def _save(self):
        """Persist trace to claudecode.db execution_traces table."""
        try:
            conn = sqlite3.connect(CLAUDECODE_DB, timeout=10)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS execution_traces (
                    id            TEXT PRIMARY KEY,
                    session_id    TEXT,
                    intent        TEXT,
                    tool_chain    TEXT,   -- JSON
                    memory_reads  TEXT,   -- JSON
                    memory_writes TEXT,   -- JSON
                    contradictions TEXT,  -- JSON
                    confidence_flow TEXT, -- JSON
                    final_output  TEXT,
                    runtime_ms    INTEGER,
                    started_at    REAL,
                    finished_at   REAL
                )
            """)
            conn.execute("""
                INSERT OR REPLACE INTO execution_traces VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                self.trace_id, self.session_id, self.intent,
                json.dumps([{"tool": c.tool_id, "success": c.success,
                             "ms": c.runtime_ms, "conf": c.confidence}
                            for c in self.tool_chain]),
                json.dumps(self.memory_reads),
                json.dumps(self.memory_writes),
                json.dumps(self.contradictions),
                json.dumps(self.confidence_flow),
                self.final_output, self.runtime_ms(),
                self.started_at, self.finished_at
            ))
            conn.commit()
            conn.close()
        except Exception as e:
            pass  # Trace persistence failure must never crash the system


def new_trace(session_id: str = "", intent: str = "") -> ExecutionTrace:
    """Create a new execution trace with a unique trace_id."""
    tid = "trace." + hashlib.md5(f"{session_id}{time.time()}".encode()).hexdigest()[:12]
    return ExecutionTrace(trace_id=tid, session_id=session_id, intent=intent)


# ── Tool Contract ─────────────────────────────────────────────────────────────

@dataclass
class ToolContract:
    """Declares what a tool does, needs, produces, and costs."""
    tool_id:              str
    name:                 str
    description:          str
    category:             str           # memory | graph | parliament | retrieve | curriculum
    input_schema:         dict          # {field: type_hint}
    output_schema:        dict          # {field: type_hint}
    side_effects:         list          = field(default_factory=list)
    confidence_required:  float         = 0.0   # min caller confidence to invoke
    safety_level:         str           = "safe"  # safe | moderate | destructive
    requires_parliament:  bool          = False
    requires_verification: bool         = False
    latency_class:        str           = "fast"  # fast | medium | slow
    fn:                   Any           = None    # callable

    def validate_input(self, inputs: dict) -> tuple[bool, str]:
        for key in self.input_schema:
            if key.endswith("?"):
                continue  # optional
            if key not in inputs:
                return False, f"Missing required input: {key}"
        return True, ""


# ── Tool Registry ─────────────────────────────────────────────────────────────

class ToolRegistry:
    """
    Central nervous system. Register tools, execute with tracing,
    validate contracts. All tool invocations go through here.
    """

    def __init__(self):
        self._tools: dict[str, ToolContract] = {}

    def register(self, contract: ToolContract):
        self._tools[contract.tool_id] = contract

    def get(self, tool_id: str) -> ToolContract | None:
        return self._tools.get(tool_id)

    def execute(self, tool_id: str, inputs: dict,
                trace: ExecutionTrace | None = None,
                confidence: float = 1.0) -> dict:
        """
        Execute a tool by ID with full tracing.
        Returns: {"ok": bool, "result": ..., "error": str, "tool_id": str}
        """
        contract = self._tools.get(tool_id)
        if not contract:
            return {"ok": False, "error": f"Unknown tool: {tool_id}", "tool_id": tool_id}

        # Confidence gate
        if confidence < contract.confidence_required:
            return {"ok": False,
                    "error": f"Confidence {confidence:.2f} below required {contract.confidence_required:.2f}",
                    "tool_id": tool_id}

        # Input validation
        ok, err = contract.validate_input(inputs)
        if not ok:
            return {"ok": False, "error": err, "tool_id": tool_id}

        t0 = time.time()
        try:
            result = contract.fn(inputs)
            ms = int((time.time() - t0) * 1000)
            call = ToolCall(tool_id=tool_id, inputs=inputs, outputs=result,
                            success=True, runtime_ms=ms, confidence=confidence)
            if trace:
                trace.record(call)
            return {"ok": True, "result": result, "tool_id": tool_id, "ms": ms}
        except Exception as e:
            ms = int((time.time() - t0) * 1000)
            call = ToolCall(tool_id=tool_id, inputs=inputs, outputs={},
                            success=False, runtime_ms=ms, confidence=confidence, error=str(e))
            if trace:
                trace.record(call)
            return {"ok": False, "error": str(e), "tool_id": tool_id}

    def list_tools(self, category: str = None) -> list[ToolContract]:
        tools = list(self._tools.values())
        if category:
            tools = [t for t in tools if t.category == category]
        return tools

    def print_registry(self):
        cats = {}
        for t in self._tools.values():
            cats.setdefault(t.category, []).append(t)
        for cat, tools in sorted(cats.items()):
            print(f"\n  {BOLD}{cat.upper()}{R}")
            for t in tools:
                safety = OK if t.safety_level == "safe" else (WARN if t.safety_level == "moderate" else ERR)
                print(f"    {safety}●{R} {t.tool_id:35} {DIM}{t.description[:55]}{R}")


# ── Global registry instance ──────────────────────────────────────────────────

registry = ToolRegistry()


# ═══════════════════════════════════════════════════════════════════════════════
# PHASE 1 TOOLS — Core Operational
# ═══════════════════════════════════════════════════════════════════════════════

# ── memory.search ─────────────────────────────────────────────────────────────

def _memory_search(inputs: dict) -> dict:
    """Semantic retrieval from CMS anchors by keyword/concept match."""
    query   = inputs["query"]
    domain  = inputs.get("domain", "%")
    limit   = int(inputs.get("limit", 10))
    min_mat = float(inputs.get("min_maturity", 0.0))

    conn = sqlite3.connect(CMS_DB, timeout=10)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("""
        SELECT canonical, display_name, anchor_type, maturity, domain_tags, sources
        FROM anchors
        WHERE (canonical LIKE ? OR display_name LIKE ?)
          AND domain_tags LIKE ?
          AND maturity >= ?
          AND state = 'active'
        ORDER BY maturity DESC, relation_count DESC
        LIMIT ?
    """, (f"%{query}%", f"%{query}%", f"%{domain}%", min_mat, limit)).fetchall()
    conn.close()

    results = [{"canonical": r["canonical"], "display_name": r["display_name"],
                "type": r["anchor_type"], "maturity": r["maturity"],
                "domain": r["domain_tags"]} for r in rows]
    return {"query": query, "count": len(results), "results": results}

registry.register(ToolContract(
    tool_id="memory.search",
    name="Memory Search",
    description="Semantic retrieval from CMS anchors by concept/keyword",
    category="memory",
    input_schema={"query": "str", "domain?": "str", "limit?": "int", "min_maturity?": "float"},
    output_schema={"query": "str", "count": "int", "results": "list"},
    side_effects=[],
    safety_level="safe",
    latency_class="fast",
    fn=_memory_search,
))


# ── memory.inject ─────────────────────────────────────────────────────────────

def _memory_inject(inputs: dict) -> dict:
    """Add a structured concept to CMS. Confidence-gated — rejects low-quality writes."""
    concept    = inputs["concept"]
    body       = inputs["body"]
    confidence = float(inputs.get("confidence", 0.5))
    domain     = inputs.get("domain", "general")
    anchor_type = inputs.get("anchor_type", "concept")

    if confidence < 0.65:
        return {"ok": False, "reason": f"confidence {confidence:.2f} below 0.65 threshold"}

    canon = concept.lower().strip().replace(" ", "_")
    aid   = "anc." + hashlib.md5(canon.encode()).hexdigest()[:10]
    conn  = sqlite3.connect(CMS_DB, timeout=10)
    existing = conn.execute("SELECT maturity FROM anchors WHERE canonical=?",
                            (canon,)).fetchone()
    if existing:
        conn.execute("UPDATE anchors SET maturity=maturity+0.05, sources=? WHERE canonical=?",
                     (json.dumps({"body": body, "confidence": confidence}), canon))
        action = "updated"
    else:
        conn.execute("""
            INSERT OR IGNORE INTO anchors
                (id, canonical, display_name, anchor_type, maturity, sources, domain_tags, visible, state)
            VALUES (?,?,?,?,?,?,?,1,'active')
        """, (aid, canon, body[:300], anchor_type,
              confidence, json.dumps({"body": body}), f"{domain},injected"))
        action = "created"
    conn.commit()
    conn.close()
    return {"ok": True, "action": action, "canonical": canon, "confidence": confidence}

registry.register(ToolContract(
    tool_id="memory.inject",
    name="Memory Inject",
    description="Add structured concept to CMS (confidence-gated, no unconditional writes)",
    category="memory",
    input_schema={"concept": "str", "body": "str", "confidence": "float",
                  "domain?": "str", "anchor_type?": "str"},
    output_schema={"ok": "bool", "action": "str", "canonical": "str"},
    side_effects=["cms_write"],
    confidence_required=0.65,
    safety_level="moderate",
    latency_class="fast",
    fn=_memory_inject,
))


# ── memory.trace ──────────────────────────────────────────────────────────────

def _memory_trace(inputs: dict) -> dict:
    """Retrieve execution traces for a session — cognitive lineage inspection."""
    session_id = inputs.get("session_id", "")
    limit      = int(inputs.get("limit", 10))
    conn = sqlite3.connect(CLAUDECODE_DB, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("""
        CREATE TABLE IF NOT EXISTS execution_traces (
            id TEXT PRIMARY KEY, session_id TEXT, intent TEXT,
            tool_chain TEXT, memory_reads TEXT, memory_writes TEXT,
            contradictions TEXT, confidence_flow TEXT, final_output TEXT,
            runtime_ms INTEGER, started_at REAL, finished_at REAL
        )
    """)
    rows = conn.execute("""
        SELECT id, session_id, intent, tool_chain, runtime_ms, started_at
        FROM execution_traces
        WHERE session_id LIKE ?
        ORDER BY started_at DESC LIMIT ?
    """, (f"%{session_id}%", limit)).fetchall()
    conn.close()
    traces = [{"trace_id": r["id"], "session": r["session_id"],
               "intent": r["intent"], "runtime_ms": r["runtime_ms"],
               "tool_chain": json.loads(r["tool_chain"] or "[]")} for r in rows]
    return {"count": len(traces), "traces": traces}

registry.register(ToolContract(
    tool_id="memory.trace",
    name="Memory Trace",
    description="Retrieve execution traces — inspect cognitive lineage for a session",
    category="memory",
    input_schema={"session_id?": "str", "limit?": "int"},
    output_schema={"count": "int", "traces": "list"},
    side_effects=[],
    safety_level="safe",
    latency_class="fast",
    fn=_memory_trace,
))


# ── graph.query ───────────────────────────────────────────────────────────────

def _graph_query(inputs: dict) -> dict:
    """Retrieve direct relations for a concept from the CMS graph."""
    concept    = inputs["concept"]
    predicate  = inputs.get("predicate", "%")
    direction  = inputs.get("direction", "both")   # outgoing | incoming | both
    min_conf   = float(inputs.get("min_confidence", 0.60))
    limit      = int(inputs.get("limit", 15))

    conn = sqlite3.connect(CMS_DB, timeout=10)
    conn.row_factory = sqlite3.Row
    results = []

    if direction in ("outgoing", "both"):
        rows = conn.execute("""
            SELECT a1.canonical as subject, r.predicate, a2.canonical as object,
                   r.confidence, r.seen_count
            FROM relations_aggregated r
            JOIN anchors a1 ON r.subject_id = a1.id
            JOIN anchors a2 ON r.object_id  = a2.id
            WHERE a1.canonical LIKE ? AND r.predicate LIKE ? AND r.confidence >= ?
            ORDER BY r.confidence DESC, r.seen_count DESC LIMIT ?
        """, (f"%{concept}%", predicate, min_conf, limit)).fetchall()
        results += [{"subject": r["subject"], "predicate": r["predicate"],
                     "object": r["object"], "confidence": r["confidence"],
                     "seen": r["seen_count"]} for r in rows]

    if direction in ("incoming", "both"):
        rows = conn.execute("""
            SELECT a1.canonical as subject, r.predicate, a2.canonical as object,
                   r.confidence, r.seen_count
            FROM relations_aggregated r
            JOIN anchors a1 ON r.subject_id = a1.id
            JOIN anchors a2 ON r.object_id  = a2.id
            WHERE a2.canonical LIKE ? AND r.predicate LIKE ? AND r.confidence >= ?
            ORDER BY r.confidence DESC, r.seen_count DESC LIMIT ?
        """, (f"%{concept}%", predicate, min_conf, limit)).fetchall()
        results += [{"subject": r["subject"], "predicate": r["predicate"],
                     "object": r["object"], "confidence": r["confidence"],
                     "seen": r["seen_count"]} for r in rows]

    conn.close()
    # Deduplicate
    seen = set()
    deduped = []
    for r in results:
        key = (r["subject"], r["predicate"], r["object"])
        if key not in seen:
            seen.add(key)
            deduped.append(r)

    return {"concept": concept, "count": len(deduped), "relations": deduped}

registry.register(ToolContract(
    tool_id="graph.query",
    name="Graph Query",
    description="Retrieve direct relations for a concept from the CMS knowledge graph",
    category="graph",
    input_schema={"concept": "str", "predicate?": "str",
                  "direction?": "str", "min_confidence?": "float", "limit?": "int"},
    output_schema={"concept": "str", "count": "int", "relations": "list"},
    side_effects=[],
    safety_level="safe",
    latency_class="fast",
    fn=_graph_query,
))


# ── graph.expand ──────────────────────────────────────────────────────────────

def _graph_expand(inputs: dict) -> dict:
    """Recursive CMS graph traversal — follow relations N hops from a concept."""
    concept = inputs["concept"]
    depth   = min(int(inputs.get("depth", 2)), 4)  # cap at 4 to prevent explosion
    min_conf = float(inputs.get("min_confidence", 0.65))

    visited  = set()
    frontier = {concept.lower().strip()}
    all_rels = []

    conn = sqlite3.connect(CMS_DB, timeout=10)
    conn.row_factory = sqlite3.Row

    for hop in range(depth):
        if not frontier:
            break
        next_frontier = set()
        for node in frontier:
            if node in visited:
                continue
            visited.add(node)
            rows = conn.execute("""
                SELECT a1.canonical as s, r.predicate as p, a2.canonical as o, r.confidence
                FROM relations_aggregated r
                JOIN anchors a1 ON r.subject_id = a1.id
                JOIN anchors a2 ON r.object_id  = a2.id
                WHERE (a1.canonical = ? OR a2.canonical = ?)
                  AND r.confidence >= ?
                ORDER BY r.confidence DESC LIMIT 10
            """, (node, node, min_conf)).fetchall()
            for r in rows:
                all_rels.append({"hop": hop+1, "subject": r["s"],
                                 "predicate": r["p"], "object": r["o"],
                                 "confidence": r["confidence"]})
                next_frontier.add(r["s"])
                next_frontier.add(r["o"])
        frontier = next_frontier - visited

    conn.close()
    # Deduplicate by (s, p, o)
    seen = set()
    deduped = []
    for r in all_rels:
        k = (r["subject"], r["predicate"], r["object"])
        if k not in seen:
            seen.add(k)
            deduped.append(r)

    return {"concept": concept, "depth": depth, "nodes_visited": len(visited),
            "count": len(deduped), "graph": deduped}

registry.register(ToolContract(
    tool_id="graph.expand",
    name="Graph Expand",
    description="Recursive CMS graph traversal — N hops from concept (max 4)",
    category="graph",
    input_schema={"concept": "str", "depth?": "int", "min_confidence?": "float"},
    output_schema={"concept": "str", "depth": "int", "nodes_visited": "int",
                   "count": "int", "graph": "list"},
    side_effects=[],
    safety_level="safe",
    latency_class="medium",
    fn=_graph_expand,
))


# ── graph.contradictions ──────────────────────────────────────────────────────

def _graph_contradictions(inputs: dict) -> dict:
    """Surface contradiction topology — where does the parliament disagree most?"""
    domain  = inputs.get("domain", "%")
    limit   = int(inputs.get("limit", 20))
    resolved = inputs.get("resolved", False)

    conn = sqlite3.connect(SUPERMODEL_DB, timeout=10)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("""
        SELECT model_a, model_b, topic, domain, conf_a, conf_b,
               resolved, created_at
        FROM contradiction_ledger
        WHERE domain LIKE ? AND resolved = ?
        ORDER BY (conf_a + conf_b) DESC LIMIT ?
    """, (f"%{domain}%", 1 if resolved else 0, limit)).fetchall()
    conn.close()

    contradictions = [{"model_a": r["model_a"], "model_b": r["model_b"],
                       "topic": r["topic"], "domain": r["domain"],
                       "conf_a": r["conf_a"], "conf_b": r["conf_b"]} for r in rows]
    return {"domain": domain, "count": len(contradictions),
            "contradictions": contradictions}

registry.register(ToolContract(
    tool_id="graph.contradictions",
    name="Graph Contradictions",
    description="Surface active contradiction topology — where parliament disagrees most",
    category="graph",
    input_schema={"domain?": "str", "limit?": "int", "resolved?": "bool"},
    output_schema={"domain": "str", "count": "int", "contradictions": "list"},
    side_effects=[],
    safety_level="safe",
    latency_class="fast",
    fn=_graph_contradictions,
))


# ── retrieve.semantic ─────────────────────────────────────────────────────────

def _retrieve_semantic(inputs: dict) -> dict:
    """Semantic concept search — finds anchors by meaning cluster, not exact match."""
    query   = inputs["query"]
    domain  = inputs.get("domain", "%")
    limit   = int(inputs.get("limit", 10))
    tokens  = [t.strip().lower() for t in re.split(r'\W+', query) if len(t) > 2]

    conn = sqlite3.connect(CMS_DB, timeout=10)
    conn.row_factory = sqlite3.Row
    results = []

    for token in tokens[:6]:
        rows = conn.execute("""
            SELECT canonical, display_name, anchor_type, maturity, domain_tags
            FROM anchors
            WHERE (canonical LIKE ? OR display_name LIKE ? OR domain_tags LIKE ?)
              AND domain_tags LIKE ? AND state='active'
            ORDER BY maturity DESC LIMIT ?
        """, (f"%{token}%", f"%{token}%", f"%{token}%", f"%{domain}%", limit)).fetchall()
        for r in rows:
            results.append({"canonical": r["canonical"], "display_name": r["display_name"],
                            "type": r["anchor_type"], "maturity": r["maturity"],
                            "matched_token": token})

    conn.close()
    # Deduplicate, rank by maturity
    seen = set()
    deduped = []
    for r in sorted(results, key=lambda x: -x["maturity"]):
        if r["canonical"] not in seen:
            seen.add(r["canonical"])
            deduped.append(r)

    return {"query": query, "count": len(deduped), "results": deduped[:limit]}

registry.register(ToolContract(
    tool_id="retrieve.semantic",
    name="Semantic Retrieval",
    description="Multi-token concept search across CMS by meaning, not exact match",
    category="retrieve",
    input_schema={"query": "str", "domain?": "str", "limit?": "int"},
    output_schema={"query": "str", "count": "int", "results": "list"},
    side_effects=[],
    safety_level="safe",
    latency_class="fast",
    fn=_retrieve_semantic,
))


# ── parliament.status ─────────────────────────────────────────────────────────

def _parliament_status(inputs: dict) -> dict:
    """Query parliament state — psychometrics, routing rules, terrain summary."""
    domain = inputs.get("domain", "chess")

    conn = sqlite3.connect(SUPERMODEL_DB, timeout=10)
    conn.row_factory = sqlite3.Row

    # Psychometrics
    psych = conn.execute("""
        SELECT model, total_positions, engine_agreements, avg_confidence
        FROM model_psychometrics WHERE domain=?
        ORDER BY engine_agreements DESC
    """, (domain,)).fetchall()

    # Active routing rules
    routes = conn.execute("""
        SELECT condition_type, condition_value, preferred_model, accuracy_rate, evidence_count
        FROM terrain_routing_rules ORDER BY accuracy_rate DESC
    """).fetchall()

    # Calibration defects
    defects = conn.execute("""
        SELECT COUNT(*) as n, MAX(overconfidence) as worst
        FROM terrain_calibration_defects
    """).fetchone()

    # Uncertainty topology
    uncertain = conn.execute("""
        SELECT COUNT(*) as n FROM terrain_uncertainty_topology
    """).fetchone()

    conn.close()

    return {
        "domain": domain,
        "psychometrics": [{"model": r["model"],
                           "positions": r["total_positions"],
                           "engine_rate": round((r["engine_agreements"] or 0) /
                                                max(r["total_positions"], 1), 3),
                           "avg_conf": round(r["avg_confidence"] or 0, 3)}
                          for r in psych],
        "routing_rules": [{"condition": f"{r['condition_type']}={r['condition_value']}",
                           "preferred": r["preferred_model"],
                           "accuracy": round(r["accuracy_rate"], 3),
                           "n": r["evidence_count"]} for r in routes],
        "calibration_defects": defects["n"] if defects else 0,
        "uncertain_positions": uncertain["n"] if uncertain else 0,
    }

registry.register(ToolContract(
    tool_id="parliament.status",
    name="Parliament Status",
    description="Query parliament psychometrics, routing rules, and terrain summary",
    category="parliament",
    input_schema={"domain?": "str"},
    output_schema={"domain": "str", "psychometrics": "list", "routing_rules": "list"},
    side_effects=[],
    safety_level="safe",
    latency_class="fast",
    fn=_parliament_status,
))


# ── parliament.contradictions ─────────────────────────────────────────────────

def _parliament_contradictions(inputs: dict) -> dict:
    """Surface the most epistemically charged unresolved contradictions."""
    domain = inputs.get("domain", "chess")
    limit  = int(inputs.get("limit", 10))
    return _graph_contradictions({"domain": domain, "limit": limit, "resolved": False})

registry.register(ToolContract(
    tool_id="parliament.contradictions",
    name="Parliament Contradictions",
    description="Surface highest-confidence unresolved inter-model contradictions",
    category="parliament",
    input_schema={"domain?": "str", "limit?": "int"},
    output_schema={"count": "int", "contradictions": "list"},
    side_effects=[],
    safety_level="safe",
    latency_class="fast",
    fn=_parliament_contradictions,
))


# ── curriculum.pending ────────────────────────────────────────────────────────

def _curriculum_pending(inputs: dict) -> dict:
    """List pending curriculum tasks — positions where parliament needs remediation."""
    domain = inputs.get("domain", "chess")
    limit  = int(inputs.get("limit", 20))

    conn = sqlite3.connect(SUPERMODEL_DB, timeout=10)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("""
        SELECT id, domain, difficulty, weakness as target_failure_mode, task_prompt as prompt, status
        FROM curriculum_tasks
        WHERE domain LIKE ? AND status='pending'
        ORDER BY difficulty DESC LIMIT ?
    """, (f"%{domain}%", limit)).fetchall()
    conn.close()

    tasks = [{"id": r["id"], "domain": r["domain"],
              "difficulty": r["difficulty"],
              "failure_mode": r["target_failure_mode"],
              "prompt": str(r["prompt"] or "")[:200]} for r in rows]
    return {"domain": domain, "count": len(tasks), "tasks": tasks}

registry.register(ToolContract(
    tool_id="curriculum.pending",
    name="Curriculum Pending",
    description="List pending curriculum tasks — parliament's known weakness targets",
    category="curriculum",
    input_schema={"domain?": "str", "limit?": "int"},
    output_schema={"domain": "str", "count": "int", "tasks": "list"},
    side_effects=[],
    safety_level="safe",
    latency_class="fast",
    fn=_curriculum_pending,
))


# ── introspect.failures ───────────────────────────────────────────────────────

def _introspect_failures(inputs: dict) -> dict:
    """Surface known failures and invariant violations from claudecode.db."""
    tags  = inputs.get("tags", "")
    limit = int(inputs.get("limit", 20))

    conn = sqlite3.connect(CLAUDECODE_DB, timeout=10)
    conn.row_factory = sqlite3.Row

    failures = conn.execute("""
        SELECT body, tags, created_at FROM failures
        WHERE tags LIKE ? ORDER BY created_at DESC LIMIT ?
    """, (f"%{tags}%" if tags else "%", limit)).fetchall()

    invariants = conn.execute("""
        SELECT body, domain, created_at FROM invariants
        WHERE domain LIKE ? ORDER BY created_at DESC LIMIT ?
    """, (f"%{tags}%" if tags else "%", limit)).fetchall()

    conn.close()
    return {
        "failures":   [{"body": r["body"], "tags": r["tags"]} for r in failures],
        "invariants": [{"body": r["body"], "domain": r["domain"]} for r in invariants],
    }

registry.register(ToolContract(
    tool_id="introspect.failures",
    name="Introspect Failures",
    description="Surface known failures and invariants — self-inspection of weak cognition zones",
    category="introspect",
    input_schema={"tags?": "str", "limit?": "int"},
    output_schema={"failures": "list", "invariants": "list"},
    side_effects=[],
    safety_level="safe",
    latency_class="fast",
    fn=_introspect_failures,
))


# ── introspect.discoveries ────────────────────────────────────────────────────

def _introspect_discoveries(inputs: dict) -> dict:
    """Retrieve session discoveries — what has been learned across sessions."""
    tags      = inputs.get("tags", "")
    limit     = int(inputs.get("limit", 20))
    min_imp   = int(inputs.get("min_importance", 1))

    conn = sqlite3.connect(CLAUDECODE_DB, timeout=10)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("""
        SELECT body, tags, importance, session_id, created_at
        FROM discoveries
        WHERE tags LIKE ? AND importance >= ?
        ORDER BY importance DESC, created_at DESC LIMIT ?
    """, (f"%{tags}%" if tags else "%", min_imp, limit)).fetchall()
    conn.close()

    return {"count": len(rows),
            "discoveries": [{"body": r["body"], "tags": r["tags"],
                              "importance": r["importance"],
                              "session": r["session_id"]} for r in rows]}

registry.register(ToolContract(
    tool_id="introspect.discoveries",
    name="Introspect Discoveries",
    description="Retrieve cross-session discoveries — accumulated cognitive learning",
    category="introspect",
    input_schema={"tags?": "str", "limit?": "int", "min_importance?": "int"},
    output_schema={"count": "int", "discoveries": "list"},
    side_effects=[],
    safety_level="safe",
    latency_class="fast",
    fn=_introspect_discoveries,
))


# ═══════════════════════════════════════════════════════════════════════════════
# CLI — run standalone for inspection
# ═══════════════════════════════════════════════════════════════════════════════

def _cmd_status():
    print(f"\n  {BOLD}SCOS Tool Registry{R}")
    registry.print_registry()
    print(f"\n  {BOLD}Total:{R} {len(registry._tools)} tools across "
          f"{len(set(t.category for t in registry._tools.values()))} categories\n")

def _cmd_test():
    print(f"\n  {BOLD}Running Phase 1 smoke tests...{R}\n")
    trace = new_trace(session_id="test.scos", intent="smoke test all Phase 1 tools")

    tests = [
        ("memory.search",         {"query": "chess", "limit": 3}),
        ("retrieve.semantic",     {"query": "kingside attack rook", "domain": "chess", "limit": 3}),
        ("graph.query",           {"concept": "fork", "limit": 5}),
        ("graph.expand",          {"concept": "pawn", "depth": 2, "min_confidence": 0.65}),
        ("graph.contradictions",  {"domain": "chess", "limit": 5}),
        ("parliament.status",     {"domain": "chess"}),
        ("parliament.contradictions", {"domain": "chess", "limit": 3}),
        ("curriculum.pending",    {"domain": "chess", "limit": 3}),
        ("introspect.failures",   {"limit": 3}),
        ("introspect.discoveries",{"min_importance": 2, "limit": 3}),
        ("memory.trace",          {"session_id": "session.2026", "limit": 3}),
    ]

    for tool_id, inputs in tests:
        r = registry.execute(tool_id, inputs, trace)
        status = f"{OK}ok{R}" if r["ok"] else f"{ERR}FAIL{R}"
        detail = ""
        if r["ok"]:
            res = r.get("result", {})
            if "count" in res: detail = f"count={res['count']}"
            elif "ok" in res:  detail = f"action={res.get('action','?')}"
        else:
            detail = r.get("error", "")[:60]
        print(f"  {status}  {tool_id:40} {DIM}{detail}{R}")

    trace.finish(output="smoke test complete")
    print(f"\n  Trace saved: {trace.trace_id}  ({trace.runtime_ms()}ms)\n")

if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser(description="SCOS Tool Registry")
    p.add_argument("--status", action="store_true", help="List all registered tools")
    p.add_argument("--test",   action="store_true", help="Smoke test all Phase 1 tools")
    p.add_argument("--tool",   default=None, help="Execute a tool by ID")
    p.add_argument("--input",  default="{}", help="JSON inputs for --tool")
    args = p.parse_args()

    if args.status or not any([args.status, args.test, args.tool]):
        _cmd_status()
    if args.test:
        _cmd_test()
    if args.tool:
        inputs = json.loads(args.input)
        trace  = new_trace(session_id="cli", intent=f"manual {args.tool}")
        result = registry.execute(args.tool, inputs, trace)
        print(json.dumps(result, indent=2))
        trace.finish()
