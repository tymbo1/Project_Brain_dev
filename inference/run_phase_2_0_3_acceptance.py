"""
Phase 2.0.3 acceptance gate.

Pass criteria (user-named at 2026-06-14 20:32 checkpoint):
1. unify() reads from a working-memory slice only — no substrate touch
2. top-k binding bounded by hard ceiling (k <= HARD_CEILING_K = 64)
3. no substrate writes (fingerprint check + module-level proof)
4. dispatcher path: unify_pattern is still enabled=0 — dispatch rejects it
5. minimal hand-built benchmark — 6 cases — passes binding correctness

Run:
    python3 inference/run_phase_2_0_3_acceptance.py
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import time

from inference import working_memory as wm
from inference import unify as U
from inference.operator_dispatcher import (
    OperatorDisabled,
    dispatch,
)

RESONANCE_DB = "/home/timbushnell/resonance_v11.db"


def _check(label, ok, detail=""):
    return {"check": label, "pass": bool(ok), "detail": detail}


def _substrate_fingerprint():
    st = os.stat(RESONANCE_DB)
    return {"size": st.st_size, "mtime_ns": st.st_mtime_ns}


def _insert_edge(conn, ws_id, subj, pred, obj, truth, conf,
                 is_bridge=False, domain_purity=None):
    prov = json.dumps({"is_bridge": bool(is_bridge),
                       "domain_purity": domain_purity})
    conn.execute(
        "INSERT INTO working_set_edges "
        "(working_set_id, subject_id, predicate, object_id, "
        " local_truth, local_confidence, provenance) "
        "VALUES (?,?,?,?,?,?,?)",
        (ws_id, subj, pred, obj, truth, conf, prov),
    )


def _new_ws_with_edges(purpose, edges):
    ws_id = wm.create(purpose, f"q={purpose}", "phase_2_0_3", ttl_seconds=600)
    with sqlite3.connect(wm.WORKMEM_DB_PATH) as conn:
        conn.execute("PRAGMA foreign_keys = ON")
        for e in edges:
            _insert_edge(conn, ws_id, *e[:5],
                         is_bridge=e[5] if len(e) > 5 else False,
                         domain_purity=e[6] if len(e) > 6 else None)
        conn.commit()
    return ws_id


def main() -> int:
    results = []
    pre = _substrate_fingerprint()

    # --- benchmark 1: single literal edge match
    try:
        ws1 = _new_ws_with_edges("b1", [
            (1, "causes", 2, "asserted", 0.9),
            (3, "causes", 4, "asserted", 0.8),
        ])
        out = U.unify([(1, "causes", 2)], ws1)
        ok = len(out) == 1 and out[0].bindings == {} and out[0].score == 0.9
        results.append(_check("b1_literal_edge", ok,
                              f"n={len(out)} top={out[0].score if out else None}"))
    except Exception as exc:
        results.append(_check("b1_literal_edge", False, repr(exc)))

    # --- benchmark 2: single variable on object
    try:
        ws2 = _new_ws_with_edges("b2", [
            (1, "causes", 2, "asserted", 0.9),
            (1, "causes", 3, "asserted", 0.7),
            (4, "leads_to", 5, "asserted", 0.6),
        ])
        out = U.unify([(1, "causes", "?Y")], ws2)
        ys = sorted(b.bindings["?Y"] for b in out)
        ok = ys == [2, 3]
        results.append(_check("b2_variable_object", ok,
                              f"ys={ys}"))
    except Exception as exc:
        results.append(_check("b2_variable_object", False, repr(exc)))

    # --- benchmark 3: two-edge chain with shared variable
    try:
        ws3 = _new_ws_with_edges("b3", [
            (1, "causes", 2, "asserted", 0.9),
            (2, "leads_to", 3, "asserted", 0.8),
            (1, "causes", 4, "asserted", 0.7),
            (4, "leads_to", 5, "asserted", 0.6),
            (9, "leads_to", 10, "asserted", 0.5),
        ])
        out = U.unify(
            [("?X", "causes", "?Y"), ("?Y", "leads_to", "?Z")],
            ws3,
        )
        chains = sorted(
            (b.bindings["?X"], b.bindings["?Y"], b.bindings["?Z"])
            for b in out
        )
        ok = chains == [(1, 2, 3), (1, 4, 5)]
        results.append(_check("b3_chain_shared_var", ok,
                              f"chains={chains}"))
    except Exception as exc:
        results.append(_check("b3_chain_shared_var", False, repr(exc)))

    # --- benchmark 4: top-k cap by score
    try:
        edges = [(1, "rel", i, "asserted", 0.1 * i) for i in range(1, 11)]
        ws4 = _new_ws_with_edges("b4", edges)
        out = U.unify([(1, "rel", "?Y")], ws4, k=3)
        scores = [b.score for b in out]
        ok = (
            len(out) == 3
            and scores == sorted(scores, reverse=True)
            and scores[0] >= scores[2]
            and all(b.bindings["?Y"] in {10, 9, 8} for b in out)
        )
        results.append(_check("b4_topk_cap", ok,
                              f"len={len(out)} scores={scores}"))
    except Exception as exc:
        results.append(_check("b4_topk_cap", False, repr(exc)))

    # --- benchmark 5: bridge-sacred overrides truth_floor
    try:
        ws5 = _new_ws_with_edges("b5", [
            (1, "weak_cross_p", 2, "asserted", 0.9, False, "weak_cross_domain"),
            (1, "bridge_p",     3, "tentative", 0.6, True,  "compatible_bridge"),
            (1, "weak_cross_p", 4, "tentative", 0.5, False, "weak_cross_domain"),
        ])
        out = U.unify(
            [(1, "?P", "?Y")], ws5,
            constraints={"truth_floor": ["asserted"], "bridges_sacred": True},
        )
        ys = sorted(b.bindings["?Y"] for b in out)
        bridge_hit = any(b.hit_bridge for b in out)
        ok = ys == [2, 3] and bridge_hit
        results.append(_check("b5_bridge_sacred", ok,
                              f"ys={ys} bridge_hit={bridge_hit}"))
    except Exception as exc:
        results.append(_check("b5_bridge_sacred", False, repr(exc)))

    # --- benchmark 6: hard ceiling absolute (request k>64 → max 64 returned)
    try:
        edges = [(1, "rel", i, "asserted", 0.5) for i in range(1, 200)]
        ws6 = _new_ws_with_edges("b6", edges)
        out = U.unify(
            [(1, "rel", "?Y")], ws6,
            constraints={"hard_ceiling": 999}, k=999,
        )
        ok = len(out) <= U.HARD_CEILING_K == 64 and len(out) == 64
        results.append(_check("b6_hard_ceiling_absolute", ok,
                              f"len={len(out)} ceiling={U.HARD_CEILING_K}"))
    except Exception as exc:
        results.append(_check("b6_hard_ceiling_absolute", False, repr(exc)))

    # --- doctrine 1: dispatcher still rejects unify_pattern (enabled=0)
    try:
        dispatch("unify_pattern", {})
        results.append(_check("dispatcher_rejects_unify_pattern", False,
                              "dispatch accepted unify_pattern"))
    except OperatorDisabled as exc:
        results.append(_check("dispatcher_rejects_unify_pattern", True,
                              str(exc)))
    except Exception as exc:
        results.append(_check("dispatcher_rejects_unify_pattern", False,
                              f"wrong exc: {exc!r}"))

    # --- doctrine 2: module does not name substrate
    src = open(U.__file__, "rb").read()
    results.append(_check("module_does_not_name_substrate",
                          b"resonance_v11" not in src,
                          U.__file__))

    # --- doctrine 3: substrate untouched
    post = _substrate_fingerprint()
    results.append(_check("substrate_untouched", pre == post,
                          f"pre={pre} post={post}"))

    # --- doctrine 4: expired ws fail-closes unify
    try:
        ws_e = _new_ws_with_edges("b_exp", [(1, "rel", 2, "asserted", 0.5)])
        # force expiry via direct UPDATE for deterministic test
        with sqlite3.connect(wm.WORKMEM_DB_PATH) as conn:
            conn.execute("UPDATE working_sets SET expires_at = 1 WHERE id = ?",
                         (ws_e,))
            conn.commit()
        try:
            U.unify([(1, "rel", "?Y")], ws_e)
            results.append(_check("expired_ws_blocks_unify", False,
                                  "unify accepted expired ws"))
        except wm.WorkingSetExpired:
            results.append(_check("expired_ws_blocks_unify", True, ws_e))
    except Exception as exc:
        results.append(_check("expired_ws_blocks_unify", False, repr(exc)))

    all_pass = all(r["pass"] for r in results)
    summary = {
        "ts": int(time.time()),
        "checks": results,
        "PHASE_2_0_3_PASS": all_pass,
    }
    print(json.dumps(summary, indent=2))
    return 0 if all_pass else 1


if __name__ == "__main__":
    sys.exit(main())
