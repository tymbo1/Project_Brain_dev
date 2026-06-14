"""
Phase 2.0.2 acceptance gate.

Pass criteria (locked):
1. workmem DB file exists; schema_version == '009'.
2. create() returns a usable ws_id; read() succeeds for live sets.
3. read() on expired set fail-closes (raises WorkingSetExpired).
4. parent_set tree integrity: child rejects unknown parent; child rejects
   expired/deleted parent; child self-reference rejected by CHECK.
5. delete(cascade=True) removes subtree; non-cascade blocked when children exist.
6. sweep_expired marks past-TTL rows; purge cleans them.
7. No write-path into resonance_v11.db — module imports and connections never
   touch the substrate DB; substrate file mtime + size unchanged across the run.

Run:
    python3 inference/run_phase_2_0_2_acceptance.py
Exit 0 on PASS, 1 on FAIL.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import sys
import time

from inference import working_memory as wm

RESONANCE_DB = "/home/timbushnell/resonance_v11.db"


def _check(label: str, ok: bool, detail: str = "") -> dict:
    return {"check": label, "pass": bool(ok), "detail": detail}


def _substrate_fingerprint() -> dict:
    st = os.stat(RESONANCE_DB)
    return {"size": st.st_size, "mtime_ns": st.st_mtime_ns}


def main() -> int:
    results: list[dict] = []
    pre = _substrate_fingerprint()

    # 1. DB + schema
    try:
        sv = wm.schema_version()
        results.append(_check("schema_version_009",
                              sv == "009", f"schema_version={sv}"))
    except Exception as exc:
        results.append(_check("schema_version_009", False, repr(exc)))

    # 2. create + read live
    try:
        live_id = wm.create("test_live", "q=alpha", "phase_2_0_2_test",
                            ttl_seconds=3600)
        live = wm.read(live_id)
        ok = (live.id == live_id and live.status == "open"
              and live.expires_at > int(time.time()))
        results.append(_check("create_and_read_live", ok,
                              f"ws={live.id} status={live.status}"))
    except Exception as exc:
        live_id = None
        results.append(_check("create_and_read_live", False, repr(exc)))

    # 3. expired read fails closed
    try:
        exp_id = wm.create("test_expired", "q=beta",
                           "phase_2_0_2_test", ttl_seconds=1)
        time.sleep(1.2)
        try:
            wm.read(exp_id)
            results.append(_check("expired_fails_closed", False,
                                  "expected WorkingSetExpired"))
        except wm.WorkingSetExpired:
            results.append(_check("expired_fails_closed", True, exp_id))
    except Exception as exc:
        exp_id = None
        results.append(_check("expired_fails_closed", False, repr(exc)))

    # 4a. unknown parent rejected
    try:
        try:
            wm.create("orphan", "q", "test",
                      parent_set="ws.does_not_exist", ttl_seconds=60)
            results.append(_check("unknown_parent_rejected", False,
                                  "create accepted bad parent"))
        except wm.InvalidParent as exc:
            results.append(_check("unknown_parent_rejected", True, str(exc)))
    except Exception as exc:
        results.append(_check("unknown_parent_rejected", False, repr(exc)))

    # 4b. expired parent rejected
    try:
        if exp_id is None:
            results.append(_check("expired_parent_rejected", False,
                                  "no exp_id available"))
        else:
            # exp_id is past TTL; sweep marks it expired, then attempting parent fails
            wm.sweep_expired(purge=False)
            try:
                wm.create("child_of_dead", "q", "test",
                          parent_set=exp_id, ttl_seconds=60)
                results.append(_check("expired_parent_rejected", False,
                                      "create accepted dead parent"))
            except wm.InvalidParent as exc:
                results.append(_check("expired_parent_rejected", True, str(exc)))
    except Exception as exc:
        results.append(_check("expired_parent_rejected", False, repr(exc)))

    # 4c. self-reference rejected at SQL CHECK
    try:
        with sqlite3.connect(wm.WORKMEM_DB_PATH) as conn:
            conn.execute("PRAGMA foreign_keys = ON")
            try:
                conn.execute(
                    "INSERT INTO working_sets "
                    "(id,purpose,query,created_by,status,expires_at,parent_set,created_at) "
                    "VALUES ('ws.self_loop','p','q','t','open',?,'ws.self_loop',?)",
                    (int(time.time()) + 60, int(time.time())),
                )
                conn.commit()
                results.append(_check("self_loop_rejected", False,
                                      "insert accepted self parent"))
            except sqlite3.IntegrityError as exc:
                results.append(_check("self_loop_rejected", True, str(exc)))
    except Exception as exc:
        results.append(_check("self_loop_rejected", False, repr(exc)))

    # 5. cascade delete
    try:
        if live_id:
            child_id = wm.create("child", "q", "test",
                                 parent_set=live_id, ttl_seconds=3600)
            grand_id = wm.create("grand", "q", "test",
                                 parent_set=child_id, ttl_seconds=3600)
            # non-cascade delete on live_id must fail (children present)
            try:
                wm.delete(live_id, cascade=False)
                non_cascade_blocked = False
            except wm.WorkingMemoryError:
                non_cascade_blocked = True
            # cascade delete must succeed
            deleted = wm.delete(live_id, cascade=True)
            ok = non_cascade_blocked and deleted == 3
            results.append(_check("delete_cascade_tree", ok,
                                  f"non_cascade_blocked={non_cascade_blocked} "
                                  f"deleted={deleted}"))
        else:
            results.append(_check("delete_cascade_tree", False,
                                  "no live_id available"))
    except Exception as exc:
        results.append(_check("delete_cascade_tree", False, repr(exc)))

    # 6. sweep + purge
    try:
        purge_id = wm.create("purgeable", "q", "test", ttl_seconds=1)
        time.sleep(1.2)
        s1 = wm.sweep_expired(purge=False)
        s2 = wm.sweep_expired(purge=True)
        # purge_id should be gone after purge
        with sqlite3.connect(wm.WORKMEM_DB_PATH) as conn:
            still = conn.execute(
                "SELECT 1 FROM working_sets WHERE id = ?", (purge_id,)
            ).fetchone()
        results.append(_check("sweep_marks_and_purges",
                              still is None and s1["marked_expired"] >= 1,
                              f"s1={s1} s2={s2} still={still}"))
    except Exception as exc:
        results.append(_check("sweep_marks_and_purges", False, repr(exc)))

    # 7. substrate untouched
    post = _substrate_fingerprint()
    untouched = pre == post
    results.append(_check("substrate_untouched", untouched,
                          f"pre={pre} post={post}"))

    # 7b. module-level proof — working_memory.py never names resonance_v11
    src_path = wm.__file__
    with open(src_path, "rb") as f:
        src = f.read()
    no_substrate_ref = b"resonance_v11" not in src
    results.append(_check("module_does_not_name_substrate",
                          no_substrate_ref,
                          f"path={src_path}"))

    all_pass = all(r["pass"] for r in results)
    summary = {
        "ts": int(time.time()),
        "workmem_db": wm.WORKMEM_DB_PATH,
        "substrate_db": RESONANCE_DB,
        "checks": results,
        "PHASE_2_0_2_PASS": all_pass,
    }
    print(json.dumps(summary, indent=2))
    return 0 if all_pass else 1


if __name__ == "__main__":
    sys.exit(main())
