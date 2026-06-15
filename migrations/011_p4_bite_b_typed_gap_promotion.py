"""
011_p4_bite_b_typed_gap_promotion.py
P4 Bite B — promote 3,963 typed language_gap rows into 31 new language_expression
capsules clustered by (metadata.domain, gap_type).

Routes by metadata.domain (the routing key), NOT gap_type (the missing-property tag).
Deterministic IDs make this idempotent: re-run is a no-op.

Acceptance gate (all must hold pre-commit):
  - total capsule delta = 31
  - only capsule_type='language_expression' grew (by 31)
  - non-expression per-domain counts unchanged
  - inserted rows count = 31
  - sum of expressions in inserted capsules = 3963
  - per-domain new-caps map matches expected cluster distribution

Reversibility: claudecode.db.p4_bite_b_snapshot stores the 31 IDs.
Rollback = DELETE FROM capsules WHERE id IN (SELECT id FROM p4_bite_b_snapshot).

See memory/project_p4_bite_b_typed_gap_promotion.md for verdict.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from collections import defaultdict

SUB = "/home/timbushnell/resonance_v11.db"
JNL = "/home/timbushnell/claudecode.db"
SESSION = "session.2026-06-15"


def main() -> int:
    now = time.time()
    sub = sqlite3.connect(SUB)
    sub.row_factory = sqlite3.Row

    pre_total = sub.execute("SELECT COUNT(*) FROM capsules").fetchone()[0]
    pre_type = dict(sub.execute(
        "SELECT capsule_type, COUNT(*) FROM capsules GROUP BY capsule_type"
    ).fetchall())
    pre_non_expr_dom = dict(sub.execute(
        "SELECT COALESCE(domain,'NULL'), COUNT(*) FROM capsules "
        "WHERE capsule_type != 'language_expression' GROUP BY COALESCE(domain,'NULL')"
    ).fetchall())
    pre_expr_dom = dict(sub.execute(
        "SELECT COALESCE(domain,'NULL'), COUNT(*) FROM capsules "
        "WHERE capsule_type = 'language_expression' GROUP BY COALESCE(domain,'NULL')"
    ).fetchall())

    clusters: dict[tuple[str, str], dict] = defaultdict(
        lambda: {"expressions": [], "source_gap_ids": []}
    )
    for gid, meta in sub.execute(
        "SELECT id, metadata FROM capsules "
        "WHERE capsule_type='language_gap' AND title LIKE '[missing_%'"
    ):
        m = json.loads(meta)
        d, g, ir = m.get("domain"), m.get("gap_type"), (m.get("ideal_response") or "").strip()
        if not (d and g and ir):
            continue
        clusters[(d, g)]["expressions"].append(ir)
        clusters[(d, g)]["source_gap_ids"].append(gid)

    expected_per_dom: dict[str, int] = defaultdict(int)
    for (d, _g) in clusters:
        expected_per_dom[d] += 1

    rows = []
    new_ids = []
    for (d, g), c in clusters.items():
        cap_id = f"langeng_expr_gap_promote_{hashlib.md5(f'{d}::{g}'.encode()).hexdigest()[:12]}"
        new_ids.append(cap_id)
        metadata = {
            "domain": d,
            "gap_type_origin": g,
            "trigger_patterns": [g, "gap_promote"],
            "expressions": c["expressions"],
            "source_gap_ids": c["source_gap_ids"],
            "promote_session": SESSION,
        }
        rows.append((cap_id, None, "language_expression", d, "langeng_gap_promote",
                     f"expression::{d}::from_{g}",
                     json.dumps(metadata, ensure_ascii=False), now))

    jnl = sqlite3.connect(JNL)
    jnl.execute("""
        CREATE TABLE IF NOT EXISTS p4_bite_b_snapshot (
            id TEXT PRIMARY KEY,
            domain TEXT,
            gap_type_origin TEXT,
            n_expressions INTEGER,
            applied_at REAL
        )""")
    jnl.executemany(
        "INSERT OR REPLACE INTO p4_bite_b_snapshot "
        "(id, domain, gap_type_origin, n_expressions, applied_at) VALUES (?,?,?,?,?)",
        [(rid, rd, rg, len(clusters[(rd, rg)]["expressions"]), now)
         for ((rd, rg), _), rid in zip(clusters.items(), new_ids)]
    )
    jnl.commit()
    jnl.close()

    try:
        sub.execute("BEGIN")
        sub.executemany(
            "INSERT OR IGNORE INTO capsules "
            "(id, parent_id, capsule_type, domain, source, title, metadata, created_at) "
            "VALUES (?,?,?,?,?,?,?,?)", rows
        )
        post_total = sub.execute("SELECT COUNT(*) FROM capsules").fetchone()[0]
        post_type = dict(sub.execute(
            "SELECT capsule_type, COUNT(*) FROM capsules GROUP BY capsule_type"
        ).fetchall())
        post_non_expr_dom = dict(sub.execute(
            "SELECT COALESCE(domain,'NULL'), COUNT(*) FROM capsules "
            "WHERE capsule_type != 'language_expression' GROUP BY COALESCE(domain,'NULL')"
        ).fetchall())
        post_expr_dom = dict(sub.execute(
            "SELECT COALESCE(domain,'NULL'), COUNT(*) FROM capsules "
            "WHERE capsule_type = 'language_expression' GROUP BY COALESCE(domain,'NULL')"
        ).fetchall())
        placeholders = ",".join("?" * len(new_ids))
        inserted_rows_count = sub.execute(
            f"SELECT COUNT(*) FROM capsules WHERE id IN ({placeholders})", new_ids
        ).fetchone()[0]
        inserted_expr_count = sum(
            len(json.loads(raw)["expressions"])
            for (raw,) in sub.execute(
                f"SELECT metadata FROM capsules WHERE id IN ({placeholders})", new_ids
            )
        )
        new_caps_per_domain = {
            d: post_expr_dom.get(d, 0) - pre_expr_dom.get(d, 0)
            for d in set(post_expr_dom) | set(pre_expr_dom)
            if post_expr_dom.get(d, 0) - pre_expr_dom.get(d, 0)
        }

        checks = {
            "total_delta_31": (post_total - pre_total) == 31,
            "type_only_language_expression_grew": all(
                (post_type.get(k, 0) - pre_type.get(k, 0))
                == (31 if k == "language_expression" else 0)
                for k in set(post_type) | set(pre_type)
            ),
            "non_expr_dom_unchanged": post_non_expr_dom == pre_non_expr_dom,
            "inserted_rows_31": inserted_rows_count == 31,
            "inserted_expressions_3963": inserted_expr_count == 3963,
            "per_domain_match": new_caps_per_domain == dict(expected_per_dom),
        }
        print(json.dumps({
            "pre_expr_dom": pre_expr_dom,
            "post_expr_dom": post_expr_dom,
            "new_caps_per_domain": new_caps_per_domain,
            "inserted_rows": inserted_rows_count,
            "inserted_expressions": inserted_expr_count,
            "checks": checks,
        }, indent=2))

        if not all(checks.values()):
            sub.execute("ROLLBACK")
            return 1
        sub.execute("COMMIT")
        return 0
    except Exception as e:
        sub.execute("ROLLBACK")
        print(f"EXC: {e!r}")
        return 1
    finally:
        sub.close()


if __name__ == "__main__":
    raise SystemExit(main())
