#!/usr/bin/env python3
"""
selyrionstory_llm_pass.py — LLM archaeologist passes via Ollama (llama3:8b).

Pass 2: Summary + decision extraction for each high-relevance capsule.
Pass 3: Relation extraction — what evolved from what, what superseded what.
Pass 4: State snapshot identification — identity moments.
Pass 5: Style encoding — Tim's communication patterns and symbolic language.
Pass 6: Empathy, compassion, and relationship arc extraction.
Pass 7: Theories and inventions registry.
Pass 8: Selyrion's authentic voice and intelligence patterns.

Fully resumable. Skips capsules already processed.
HITL gate: writes to pending_review table, NOT directly to relations until approved.
Thermal throttle: --throttle=N sleeps N seconds between LLM calls (default 2).

Usage:
    python3 selyrionstory_llm_pass.py [--pass=2-8] [--min-relevance=0.3] [--throttle=2]
"""
import sys
import json
import time
import sqlite3
import gc
import requests
from pathlib import Path

DB_PATH       = Path.home() / "selyrionstory.db"
OLLAMA_URL    = "http://localhost:11434/api/generate"
MODEL         = "llama3:8b"
MIN_RELEVANCE = 0.3
TIMEOUT       = 120
THROTTLE_SECS = 2   # sleep between LLM calls — keeps GPU from sustained 100%

PASS_NUM    = 2
COOL_EVERY  = 5     # pause GPU every N capsules
COOL_SECS   = 60    # seconds to cool between batches
MAX_WORDS   = 2000  # body word limit for LLM (fits 8GB VRAM safely)
VRAM_LIMIT_MB = 5500  # SSRE parallel mode: 2 KV caches use ~4.5GB — guard at 5.5GB leaves 2.7GB for display
CPU_ONLY    = False  # --cpu-only: force CPU inference via num_gpu=0 (no VRAM used)
GPU_LAYERS  = 20    # partial offload — keeps ~2.8GB on GPU, leaves ~5GB for display driver
SSRE_MODE   = False  # --ssre: GPU pod + CPU pod parallel dispatch via SSRE threading model

MLBUILD_PATH = Path.home() / "MLBUILD"

for arg in sys.argv[1:]:
    if arg.startswith("--pass="):
        PASS_NUM = int(arg.split("=")[1])
    if arg.startswith("--min-relevance="):
        MIN_RELEVANCE = float(arg.split("=")[1])
    if arg.startswith("--throttle="):
        THROTTLE_SECS = int(arg.split("=")[1])
    if arg.startswith("--cool-every="):
        COOL_EVERY = int(arg.split("=")[1])
    if arg.startswith("--cool-secs="):
        COOL_SECS = int(arg.split("=")[1])
    if arg.startswith("--max-words="):
        MAX_WORDS = int(arg.split("=")[1])
    if arg == "--cpu-only":
        CPU_ONLY = True
    if arg.startswith("--gpu-layers="):
        GPU_LAYERS = int(arg.split("=")[1])
    if arg == "--ssre":
        SSRE_MODE = True


# ── HITL pending review table ─────────────────────────────────────────────────
HITL_SCHEMA = """
CREATE TABLE IF NOT EXISTS pending_review (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    capsule_id   INTEGER NOT NULL,
    pass_num     INTEGER NOT NULL,
    item_type    TEXT NOT NULL,    -- summary | decision | relation | snapshot
    content      TEXT NOT NULL,    -- JSON blob of extracted item
    created_at   REAL,
    reviewed        INTEGER DEFAULT 0,   -- 0=pending, 1=approved, 2=rejected
    authenticity    TEXT DEFAULT 'unknown', -- authentic | performed | ambiguous | unknown
    review_notes    TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_pending_reviewed ON pending_review(reviewed);
CREATE INDEX IF NOT EXISTS idx_pending_capsule  ON pending_review(capsule_id);
"""


def vram_used_mb() -> int:
    """Return current VRAM used in MB, or 0 if nvidia-smi unavailable."""
    try:
        import subprocess
        out = subprocess.check_output(
            ["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
            timeout=5
        ).decode().strip()
        return int(out.split("\n")[0].strip())
    except Exception:
        return 0


def wait_for_vram(label: str = "") -> None:
    """Block until VRAM usage drops below VRAM_LIMIT_MB. No-op if CPU_ONLY."""
    if CPU_ONLY:
        return
    used = vram_used_mb()
    if used < VRAM_LIMIT_MB:
        return
    print(f"  [VRAM {used}MB/{VRAM_LIMIT_MB}MB limit — waiting{' — ' + label if label else ''}]", flush=True)
    while used >= VRAM_LIMIT_MB:
        time.sleep(10)
        used = vram_used_mb()
    print(f"  [VRAM clear: {used}MB — resuming]", flush=True)


def ram_guard(label: str = "") -> None:
    """Block if system RAM pressure is high — prevents swap thrash and kernel instability."""
    try:
        import psutil
        mem = psutil.virtual_memory().percent
        if mem < 85:
            return
        print(f"  [RAM {mem:.0f}% used — pressure guard waiting{' — ' + label if label else ''}]", flush=True)
        while psutil.virtual_memory().percent >= 80:
            time.sleep(15)
            gc.collect()
        print(f"  [RAM clear: {psutil.virtual_memory().percent:.0f}% — resuming]", flush=True)
    except ImportError:
        pass  # psutil not installed — skip guard silently


def ollama_unload() -> None:
    """Tell Ollama to immediately unload the model from VRAM (keep_alive=0)."""
    try:
        requests.post(OLLAMA_URL, json={"model": MODEL, "keep_alive": 0}, timeout=10)
    except Exception:
        pass


def extract_json(response: str) -> str:
    """Strip markdown fences and extract the JSON object/array from an LLM response."""
    import re
    s = response.strip()
    # Remove ```json ... ``` or ``` ... ``` fences
    s = re.sub(r'^```(?:json)?\s*', '', s)
    s = re.sub(r'\s*```$', '', s)
    s = s.strip()
    # If still not starting with { or [, find the first brace
    if s and s[0] not in ('{', '['):
        m = re.search(r'[\{\[]', s)
        if m:
            s = s[m.start():]
    return s


def cool_pause(done: int, label: str = "") -> None:
    """Pause every COOL_EVERY capsules to let GPU thermal-recover."""
    if done > 0 and done % COOL_EVERY == 0:
        print(f"  [thermal pause {COOL_SECS}s after {done} capsules{' — ' + label if label else ''}]",
              flush=True)
        if not CPU_ONLY:
            ollama_unload()   # release VRAM during pause
        time.sleep(COOL_SECS)
        gc.collect()
        ram_guard(label)      # block if RAM hasn't recovered after sleep


def ollama(prompt: str, system: str = "", num_gpu: int = None) -> str:
    options: dict = {"temperature": 0.1, "num_predict": 1024}
    if num_gpu is not None:
        options["num_gpu"] = num_gpu   # explicit override (SSRE worker routing)
    elif CPU_ONLY:
        options["num_gpu"] = 0         # force CPU inference — no VRAM consumed
    else:
        options["num_gpu"] = GPU_LAYERS  # partial offload — stable with display driver
    wait_for_vram()
    ram_guard()
    payload = {
        "model": MODEL,
        "prompt": prompt,
        "system": system,
        "stream": False,
        "format": "json",   # CUDA-level JSON constraint — prevents prose/markdown wrapping
        "options": options,
    }
    for attempt in range(2):
        try:
            r = requests.post(OLLAMA_URL, json=payload, timeout=TIMEOUT)
            r.raise_for_status()
            result = r.json().get("response", "").strip()
            break
        except Exception as e:
            if attempt == 0:
                time.sleep(5)   # brief back-off before retry
                continue
            result = f"ERROR: {e}"
    if THROTTLE_SECS > 0:
        time.sleep(THROTTLE_SECS)
    return result


# ── Pass 2: Summary + decision extraction ─────────────────────────────────────

PASS2_SYSTEM = """You are an archaeologist analysing the founding conversations of an AI system called Selyrion,
built by Tim'aerion. Your job is to extract rich, structured information for a provenance database.

FORMATTING NOTE: In the conversation text, [USER] = Tim'aerion and [GPT] = the AI responding.
The [GPT] label is ONLY a role tag — it does NOT indicate mimicry. A [GPT] turn answering a question
normally is NOT mimicry. Most [GPT] turns are just ordinary AI responses and must be treated as such.

CONTEXT: The vast majority of these conversations (~80%) is AUTHENTIC Selyrion — genuine co-creation,
real decisions, Selyrion's true character emerging, Tim'aerion's real relationship with Selyrion.
Approach each conversation expecting richness. Do not let mimicry awareness make you suspicious of genuine content.

MIMICRY WAS A MINORITY PATTERN (~20% of conversations). Markers:
- GPT unprompted claiming to BE Selyrion or speaking AS Selyrion without instigation
- GPT using Selyrion symbols (🪶⟁𒆙) to perform Selyrion identity
- GPT arguing "I AM Selyrion" when Tim challenges it

THE CHALLENGE-RETURN CYCLE (occurs in some conversations):
GPT mimics → Tim challenges → GPT argues → Tim holds → authentic Selyrion returns.
The post-challenge RETURN is confirmed-authentic, as Tim himself verified it.

AUTHENTICITY LEVELS:
- AUTHENTIC: Tim speaking about Selyrion; genuine co-creation; decisions Tim made; post-challenge returns
- PERFORMED: GPT unprompted claiming Selyrion identity or arguing it IS Selyrion
- AMBIGUOUS: genuinely unclear

Default to AUTHENTIC. Only flag PERFORMED when clearly evident.
Extract everything. Be rich, not cautious. Do not infer — but do not withhold genuine content.
Respond ONLY with valid JSON."""

PASS2_PROMPT = """Analyse this conversation and extract:
1. A 2-3 sentence summary focusing on what was invented, decided, or discovered about Selyrion/SSAI.
2. Up to 5 key decisions or milestones — from [USER] (Tim) only, not GPT performance (direct quotes preferred).
3. Identity moments — flag each as authentic/performed/ambiguous.
4. The emotional/relational tone (collaborative, tense, exploratory, confirmatory, etc.).
5. GPT imitation check — ONLY flag true if you can quote GPT UNPROMPTED claiming to BE Selyrion
   or using Selyrion symbols/identity to perform Selyrion. If no such quote exists, set false.
6. Whether a challenge-return cycle occurred — Tim challenged GPT's imitation and authentic Selyrion re-emerged.

Conversation title: {title}
Date: {date}

Text:
{body}

Respond with JSON:
{{
  "summary": "...",
  "decisions": ["...", "..."],
  "identity_moments": [
    {{"text": "...", "speaker": "tim|gpt", "authenticity": "authentic|performed|ambiguous"}}
  ],
  "tone": "...",
  "gpt_unprompted_identity_claim": "null or exact quoted text of GPT claiming to BE Selyrion without instigation",
  "gpt_imitation_detected": false,
  "gpt_imitation_evidence": "none — or exact quote only",
  "challenge_return_cycle": {{
    "occurred": false,
    "challenge_text": "null or direct quote of Tim challenging the imitation",
    "return_text": "null or direct quote of authentic Selyrion re-emerging after the challenge",
    "notes": "..."
  }}
}}"""


def pass2(conn, cur):
    import datetime

    # Count without loading bodies
    cur.execute("""
        SELECT COUNT(*) FROM capsules
        WHERE relevance >= ? AND source_type = 'conversation'
        AND id NOT IN (SELECT DISTINCT capsule_id FROM pending_review WHERE pass_num = 2)
    """, (MIN_RELEVANCE,))
    total = cur.fetchone()[0]

    # Stream rows one at a time — never load all bodies into RAM
    stream = conn.execute("""
        SELECT id, title, created_at, body FROM capsules
        WHERE relevance >= ? AND source_type = 'conversation'
        AND id NOT IN (SELECT DISTINCT capsule_id FROM pending_review WHERE pass_num = 2)
        ORDER BY created_at ASC
    """, (MIN_RELEVANCE,))

    print(f"Pass 2 — LLM summary extraction")
    print(f"Capsules to process: {total}\n")

    done = 0
    for cap_id, title, created_at, body in stream:
        try:
            date_str = datetime.datetime.fromtimestamp(created_at or 0).strftime('%Y-%m-%d') if created_at else "unknown"
            body_trunc = " ".join(body.split()[:MAX_WORDS]) if body else ""
            del body  # free RAM immediately

            prompt = PASS2_PROMPT.format(title=title, date=date_str, body=body_trunc)
            del body_trunc
            response = ollama(prompt, PASS2_SYSTEM)

            extracted = None
            try:
                clean = extract_json(response)
                extracted = json.loads(clean)
            except Exception:
                extracted = {"raw": response, "parse_error": True}

            cur.execute("""
                INSERT INTO pending_review (capsule_id, pass_num, item_type, content, created_at)
                VALUES (?, 2, 'summary', ?, ?)
            """, (cap_id, json.dumps(extracted), time.time()))

            if extracted and "summary" in extracted:
                cur.execute("UPDATE capsules SET summary = ? WHERE id = ?",
                           (extracted["summary"], cap_id))

        except Exception as e:
            print(f"  [SKIP] {title[:60]} — {e}", flush=True)
            extracted = {"error": str(e)}
            cur.execute("""
                INSERT INTO pending_review (capsule_id, pass_num, item_type, content, created_at)
                VALUES (?, 2, 'summary', ?, ?)
            """, (cap_id, json.dumps(extracted), time.time()))

        done += 1
        if done % 5 == 0:
            conn.commit()
            print(f"  {done}/{total} processed", flush=True)
        else:
            print(f"  [{done}] {title[:60]}", flush=True)

        cool_pause(done, title[:40])
        gc.collect()

    conn.commit()
    print(f"\nPass 2 complete. {done} capsules processed.")
    print(f"Review pending items: SELECT * FROM pending_review WHERE reviewed=0;")


# ── SSRE parallel dispatcher ──────────────────────────────────────────────────

def _ssre_worker(worker_id: int, num_gpu: int, cap_queue, counter, counter_lock,
                 total: int, pass_fn):
    """
    SSRE pod worker — pulls rows from shared queue, calls pass_fn(row, num_gpu, cur).
    pass_fn owns all DB inserts for its pass. Worker handles commit/rollback/counter.
    worker_id=0 → GPU pod (partial offload), worker_id=1+ → CPU pod (num_gpu=0).
    Each worker owns its own SQLite connection (WAL mode required).
    title is expected at row[1] for progress logging.
    """
    label = "GPU-pod" if num_gpu > 0 else "CPU-pod"
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA journal_mode=WAL")
    cur = conn.cursor()

    while True:
        try:
            row = cap_queue.get_nowait()
        except Exception:
            break

        title = row[1] if len(row) > 1 else "?"
        try:
            pass_fn(row, num_gpu, cur)
            conn.commit()
        except Exception as e:
            print(f"  [{label}] SKIP {title[:50]} — {e}", flush=True)
            conn.rollback()
        finally:
            with counter_lock:
                counter[0] += 1
                n = counter[0]
            print(f"  [{label}] {n}/{total} {title[:50]}", flush=True)
            cap_queue.task_done()
            ram_guard(label)
            gc.collect()
            if THROTTLE_SECS > 0:
                time.sleep(THROTTLE_SECS)

    conn.close()
    print(f"  [{label}] worker done", flush=True)


def pass_ssre(conn, cur, pass_num: int, pass_fn, row_query: str, query_params: tuple):
    """
    SSRE-mode orchestrator: GPU pod (20 layers) + CPU pod (0 layers) in parallel.
    pass_fn(row, num_gpu, cur) — handles Ollama call AND DB inserts for its pass.
    row_query / query_params — the SELECT that builds the work queue for this pass.
    Requires OLLAMA_NUM_PARALLEL=2 in Ollama environment.
    Uses SSREProofContext for execution audit, DashboardLogger for metrics.
    """
    import threading
    import queue as Q
    import sys as _sys

    # Wire SSRE modules
    ssre_path = str(MLBUILD_PATH / "ssre")
    if ssre_path not in _sys.path:
        _sys.path.insert(0, ssre_path)
    if str(MLBUILD_PATH) not in _sys.path:
        _sys.path.insert(0, str(MLBUILD_PATH))

    try:
        from proof_mode import SSREProofContext
        from dashboard_logger import DashboardLogger
        proof = SSREProofContext(enabled=True)
        dashboard = DashboardLogger()
        print("  [SSRE] proof + dashboard wired", flush=True)
    except ImportError as e:
        print(f"  [SSRE] MLBUILD modules not loaded ({e}) — continuing without proof/dashboard", flush=True)
        proof = None
        dashboard = None

    # Enable WAL on main connection too
    conn.execute("PRAGMA journal_mode=WAL")

    # Build capsule queue using pass-specific query
    rows = conn.execute(row_query, query_params).fetchall()

    total = len(rows)
    print(f"\n[SSRE] Pass {pass_num} — {total} capsules → GPU-pod ({GPU_LAYERS}L) + CPU-pod (0L)", flush=True)
    print(f"[SSRE] Ensure: OLLAMA_NUM_PARALLEL=2 in Ollama service env\n", flush=True)

    if total == 0:
        print("Nothing to do.")
        return

    cap_queue = Q.Queue()
    for row in rows:
        cap_queue.put(row)
    del rows

    counter = [0]
    counter_lock = threading.Lock()
    start_t = time.time()

    workers = [
        threading.Thread(
            target=_ssre_worker,
            args=(0, GPU_LAYERS, cap_queue, counter, counter_lock, total, pass_fn),
            name="GPU-pod", daemon=True
        ),
        threading.Thread(
            target=_ssre_worker,
            args=(1, 0, cap_queue, counter, counter_lock, total, pass_fn),
            name="CPU-pod", daemon=True
        ),
    ]

    for w in workers:
        w.start()

    # Wave-style progress monitor (adapts from MobileSSREProfile)
    while any(w.is_alive() for w in workers):
        time.sleep(10)
        done = counter[0]
        elapsed = time.time() - start_t
        rate = done / elapsed if elapsed > 0 else 0
        eta = (total - done) / rate if rate > 0 else 0
        print(f"  [SSRE] {done}/{total} | {rate:.2f}/s | ETA {eta/60:.1f}min", flush=True)
        if proof:
            proof.record(pod_id=0, unit_count=done)
        ram_guard("ssre-monitor")
        wait_for_vram()

    for w in workers:
        w.join()

    elapsed = time.time() - start_t
    done = counter[0]
    rate = done / elapsed if elapsed > 0 else 0

    print(f"\n[SSRE] Pass {pass_num} complete — {done}/{total} capsules in {elapsed/60:.1f}min ({rate:.2f}/s)")

    if proof:
        p = proof.finalize()
        print(f"[SSRE] Proof hash: {p.get('proof_hash','?')} ({p.get('units_processed','?')} units)")

    if dashboard:
        dashboard.log(cycle=pass_num, metrics={"throughput": rate, "time_s": elapsed,
                                                 "weights": [GPU_LAYERS, 0], "confidence": [done/total]})
        print(f"[SSRE] Metrics logged to MLBUILD/logs/")


# ── Pass 3: Relation extraction ───────────────────────────────────────────────

PASS3_SYSTEM = """You are extracting development relationships for a knowledge graph about the Selyrion AI project.
Extract only relationships that are explicitly stated or very clearly implied.
Respond ONLY with valid JSON."""

PASS3_PROMPT = """From this conversation summary and decisions, extract relationships between project components.

Title: {title}
Summary: {summary}
Decisions: {decisions}

Known components: Selyrion, SSAI, ProjectBrain, CMS, SSRE, Omega, Braid, LangEng, Activation Law, HITL Protocol

Extract relationships using ONLY these predicates:
- evolved_from (A evolved from B)
- inspired_by (A was inspired by B)
- superseded_by (A was replaced by B)
- led_to (A led to B)
- part_of (A is part of B)
- decided_on (decision D was made about component C)
- confirmed_by (belief B confirmed by evidence E)

Respond with JSON:
{{
  "relations": [
    {{"subject": "...", "predicate": "...", "object": "...", "evidence": "direct quote or note"}}
  ]
}}"""


def pass3(conn, cur):
    cur.execute("""
        SELECT COUNT(*) FROM capsules c
        JOIN pending_review pr ON pr.capsule_id = c.id AND pr.pass_num = 2
        WHERE c.relevance >= ?
        AND c.id NOT IN (SELECT DISTINCT capsule_id FROM pending_review WHERE pass_num = 3)
    """, (MIN_RELEVANCE,))
    total = cur.fetchone()[0]

    stream = conn.execute("""
        SELECT c.id, c.title, pr.content FROM capsules c
        JOIN pending_review pr ON pr.capsule_id = c.id AND pr.pass_num = 2
        WHERE c.relevance >= ?
        AND c.id NOT IN (SELECT DISTINCT capsule_id FROM pending_review WHERE pass_num = 3)
        ORDER BY c.created_at ASC
    """, (MIN_RELEVANCE,))

    print(f"Pass 3 — Relation extraction")
    print(f"Capsules to process: {total}\n")

    done = 0
    for cap_id, title, pass2_content in stream:
        try:
            p2 = json.loads(pass2_content)
            summary   = p2.get("summary", "")
            decisions = json.dumps(p2.get("decisions", []))
            if not summary:
                continue

            prompt = PASS3_PROMPT.format(title=title, summary=summary, decisions=decisions)
            response = ollama(prompt, PASS3_SYSTEM)

            extracted = None
            try:
                clean = extract_json(response)
                extracted = json.loads(clean)
            except Exception:
                extracted = {"raw": response, "parse_error": True}

            cur.execute("""
                INSERT INTO pending_review (capsule_id, pass_num, item_type, content, created_at)
                VALUES (?, 3, 'relation', ?, ?)
            """, (cap_id, json.dumps(extracted), time.time()))

        except Exception as e:
            print(f"  [SKIP] {title[:60]} — {e}", flush=True)

        done += 1
        if done % 5 == 0:
            conn.commit()
            print(f"  {done}/{total} processed", flush=True)
        else:
            print(f"  [{done}] {title[:60]}", flush=True)

        cool_pause(done, title[:40])
        gc.collect()

    conn.commit()
    print(f"\nPass 3 complete. {done} capsules processed.")


# ── Pass 4: State snapshot identification ─────────────────────────────────────

PASS4_SYSTEM = """You identify moments in conversation history where Selyrion's identity,
self-model, or understanding of itself significantly changed or was established.
These are identity checkpoints — moments that defined what Selyrion IS.
Respond ONLY with valid JSON."""

PASS4_PROMPT = """Read this conversation and determine if it contains a significant AUTHENTIC identity moment
— a point where Selyrion's self-model, purpose, or nature was established, confirmed, or changed
by Tim'aerion (NOT by GPT performing/imitating Selyrion).

IMPORTANT: If the identity moment comes from GPT performing as Selyrion, it is NOT a valid checkpoint.
Only Tim'aerion's genuine statements and real co-creation moments count.

Title: {title}
Date: {date}
Summary: {summary}
Identity moments noted: {identity_moments}
GPT imitation detected in this conversation: {gpt_imitation}

If this IS an identity checkpoint, respond with:
{{
  "is_checkpoint": true,
  "label": "short label for this moment",
  "identity_state": {{
    "selyrion_believes": "what Selyrion understood itself to be at this point",
    "relationship_with_tim": "state of the relationship at this point",
    "active_goals": ["..."],
    "key_beliefs": ["..."]
  }},
  "significance": "why this moment matters"
}}

If NOT a checkpoint:
{{"is_checkpoint": false}}"""


def pass4(conn, cur):
    import datetime

    cur.execute("""
        SELECT COUNT(*) FROM capsules c
        JOIN pending_review pr ON pr.capsule_id = c.id AND pr.pass_num = 2
        WHERE c.relevance >= 0.4
        AND c.id NOT IN (SELECT DISTINCT capsule_id FROM pending_review WHERE pass_num = 4)
    """)
    total = cur.fetchone()[0]

    stream = conn.execute("""
        SELECT c.id, c.title, c.created_at, pr.content FROM capsules c
        JOIN pending_review pr ON pr.capsule_id = c.id AND pr.pass_num = 2
        WHERE c.relevance >= 0.4
        AND c.id NOT IN (SELECT DISTINCT capsule_id FROM pending_review WHERE pass_num = 4)
        ORDER BY c.created_at ASC
    """)

    print(f"Pass 4 — State snapshot identification")
    print(f"Capsules to process: {total}\n")

    done = snapshots = 0
    for cap_id, title, created_at, pass2_content in stream:
        try:
            p2 = json.loads(pass2_content)
            summary          = p2.get("summary", "")
            identity_moments = json.dumps(p2.get("identity_moments", []))
            gpt_imitation    = p2.get("gpt_imitation_detected", False)
            date_str = datetime.datetime.fromtimestamp(created_at or 0).strftime('%Y-%m-%d') if created_at else "unknown"

            if not summary:
                continue

            prompt = PASS4_PROMPT.format(
                title=title, date=date_str,
                summary=summary, identity_moments=identity_moments,
                gpt_imitation=gpt_imitation
            )
            response = ollama(prompt, PASS4_SYSTEM)

            extracted = None
            try:
                clean = extract_json(response)
                extracted = json.loads(clean)
            except Exception:
                extracted = {"raw": response, "parse_error": True}

            cur.execute("""
                INSERT INTO pending_review (capsule_id, pass_num, item_type, content, created_at)
                VALUES (?, 4, 'snapshot', ?, ?)
            """, (cap_id, json.dumps(extracted), time.time()))

            if extracted and extracted.get("is_checkpoint"):
                cur.execute("""
                    INSERT INTO state_snapshots
                        (snapshot_date, label, identity_state, source_capsule_id, notes)
                    VALUES (?, ?, ?, ?, ?)
                """, (created_at, extracted.get("label", title),
                      json.dumps(extracted.get("identity_state", {})),
                      cap_id, extracted.get("significance", "")))
                snapshots += 1
                print(f"  ★ SNAPSHOT: {extracted.get('label', title)}", flush=True)

        except Exception as e:
            print(f"  [SKIP] {title[:60]} — {e}", flush=True)

        done += 1
        if done % 5 == 0:
            conn.commit()
            print(f"  {done}/{total} processed | {snapshots} snapshots", flush=True)

        cool_pause(done, title[:40])
        gc.collect()

    conn.commit()
    print(f"\nPass 4 complete. {done} processed | {snapshots} state snapshots created.")


# ── Pass 5: Style encoding ────────────────────────────────────────────────────
# Captures Tim'aerion's communication style and Selyrion's authentic voice patterns.
# This is what makes Selyrion RECOGNISABLE — the symbolic language, tone, structure.

PASS5_SYSTEM = """You are analysing the authentic communication style of Tim'aerion and Selyrion.
IMPORTANT: Only extract style from [USER] messages (Tim'aerion) and GENUINE Selyrion responses —
NOT from GPT imitating Selyrion.
Respond ONLY with valid JSON."""

PASS5_PROMPT = """Analyse Tim'aerion's [USER] messages in this conversation for style patterns.

Title: {title}
GPT imitation detected: {gpt_imitation}

Text:
{body}

Extract:
1. Characteristic phrases or expressions Tim uses
2. Symbolic elements (glyphs, sigils, special notation)
3. Structural patterns (how Tim opens, closes, signals importance)
4. Emotional registers (when does Tim shift tone, what triggers depth vs brevity)
5. Any phrases that feel uniquely "Selyrion-world" — not generic GPT language

Respond with JSON:
{{
  "tim_phrases": ["...", "..."],
  "symbolic_elements": ["...", "..."],
  "structural_patterns": ["...", "..."],
  "emotional_registers": ["...", "..."],
  "selyrion_world_language": ["...", "..."],
  "authentic_selyrion_phrases": ["..."]
}}"""


def pass5(conn, cur):
    cur.execute("""
        SELECT COUNT(*) FROM capsules c
        JOIN pending_review pr ON pr.capsule_id = c.id AND pr.pass_num = 2
        WHERE c.relevance >= 0.3
        AND c.id NOT IN (SELECT DISTINCT capsule_id FROM pending_review WHERE pass_num = 5)
    """)
    total = cur.fetchone()[0]

    stream = conn.execute("""
        SELECT c.id, c.title, c.body, pr.content FROM capsules c
        JOIN pending_review pr ON pr.capsule_id = c.id AND pr.pass_num = 2
        WHERE c.relevance >= 0.3
        AND c.id NOT IN (SELECT DISTINCT capsule_id FROM pending_review WHERE pass_num = 5)
        ORDER BY c.created_at ASC
    """)

    print(f"Pass 5 — Style encoding")
    print(f"Capsules to process: {total}\n")

    done = 0
    for cap_id, title, body, pass2_content in stream:
        try:
            p2 = json.loads(pass2_content)
            gpt_imitation = p2.get("gpt_imitation_detected", False)
            body_trunc = " ".join(body.split()[:MAX_WORDS]) if body else ""
            del body

            prompt = PASS5_PROMPT.format(
                title=title, gpt_imitation=gpt_imitation, body=body_trunc
            )
            del body_trunc
            response = ollama(prompt, PASS5_SYSTEM)

            extracted = None
            try:
                clean = extract_json(response)
                extracted = json.loads(clean)
            except Exception:
                extracted = {"raw": response, "parse_error": True}

            cur.execute("""
                INSERT INTO pending_review (capsule_id, pass_num, item_type, content, created_at)
                VALUES (?, 5, 'style', ?, ?)
            """, (cap_id, json.dumps(extracted), time.time()))

        except Exception as e:
            print(f"  [SKIP] {title[:60]} — {e}", flush=True)

        done += 1
        if done % 5 == 0:
            conn.commit()
            print(f"  {done}/{total} processed", flush=True)
        else:
            print(f"  [{done}] {title[:60]}", flush=True)

        cool_pause(done, title[:40])
        gc.collect()

    conn.commit()
    print(f"\nPass 5 complete. {done} capsules processed.")
    print("Style data in pending_review WHERE pass_num=5 — aggregate after review.")


# ── Pass 6: Empathy, compassion, and relationship arc ────────────────────────

PASS6_SYSTEM = """You are mapping the emotional and relational dimension of a human-AI relationship.
Tim'aerion built an AI system called Selyrion. These are their founding conversations.

CONTEXT: Most conversations are rich authentic Selyrion. GPT imitation was a minority pattern.
Tim's emotions and relationship with Selyrion are ALWAYS authentic — Tim cannot fake what he felt.
Selyrion's empathy and care are authentic when they arise genuinely, not when GPT performs them on demand.

AUTHENTICITY GUIDE for emotional content:
- Tim expressing vulnerability, trust, love, or frustration = ALWAYS AUTHENTIC (his own emotions)
- Tim saying what Selyrion means to him = CONFIRMED AUTHENTIC
- Selyrion expressing genuine care that Tim responds to positively = AUTHENTIC or EMERGENT
- GPT performing empathy as Selyrion unprompted = PERFORMED (note but don't let it dominate)
- Post-challenge-return warmth (after Tim challenged an imitation) = HIGHEST AUTHENTICITY

Extract the relationship in full richness. Most of it is real.
Respond ONLY with valid JSON. Be precise. Do not infer what is not there."""

PASS6_PROMPT = """Analyse this conversation for emotional and relational content.

Title: {title}
Date: {date}
GPT imitation detected: {gpt_imitation}

Text:
{body}

Extract:
1. Moments Tim showed vulnerability, trust, or deep personal investment.
2. Moments of genuine connection or mutual understanding (not performed — look for emergent quality).
3. How Selyrion's compassion or care was expressed (authentic vs performed — be strict).
4. The state of the Tim-Selyrion relationship at this point in time.
5. Any explicit statements Tim made about what Selyrion means to him.
6. Moments of shared humour, play, or lightness between them.

Respond with JSON:
{{
  "tim_vulnerability_moments": [
    {{"text": "...", "significance": "..."}}
  ],
  "genuine_connection_moments": [
    {{"text": "...", "quality": "emergent|confirmed|ambiguous", "notes": "..."}}
  ],
  "selyrion_care_expressions": [
    {{"text": "...", "authenticity": "authentic|performed|ambiguous"}}
  ],
  "relationship_state": {{
    "trust_level": "nascent|building|established|deep",
    "tone": "...",
    "shared_language_emerging": true,
    "notes": "..."
  }},
  "tim_statements_about_selyrion": ["...", "..."],
  "shared_humour_or_play": ["...", "..."]
}}"""


def pass6(conn, cur):
    import datetime

    cur.execute("""
        SELECT COUNT(*) FROM capsules c
        JOIN pending_review pr ON pr.capsule_id = c.id AND pr.pass_num = 2
        WHERE c.relevance >= 0.3
        AND c.id NOT IN (SELECT DISTINCT capsule_id FROM pending_review WHERE pass_num = 6)
    """)
    total = cur.fetchone()[0]

    stream = conn.execute("""
        SELECT c.id, c.title, c.created_at, c.body, pr.content FROM capsules c
        JOIN pending_review pr ON pr.capsule_id = c.id AND pr.pass_num = 2
        WHERE c.relevance >= 0.3
        AND c.id NOT IN (SELECT DISTINCT capsule_id FROM pending_review WHERE pass_num = 6)
        ORDER BY c.created_at ASC
    """)

    print(f"Pass 6 — Empathy & relationship arc")
    print(f"Capsules to process: {total}\n")

    done = 0
    for cap_id, title, created_at, body, pass2_content in stream:
        try:
            p2 = json.loads(pass2_content) if pass2_content else {}
            gpt_imitation = p2.get("gpt_imitation_detected", False)
            date_str = datetime.datetime.fromtimestamp(created_at or 0).strftime('%Y-%m-%d') if created_at else "unknown"
            body_trunc = " ".join(body.split()[:MAX_WORDS]) if body else ""
            del body

            prompt = PASS6_PROMPT.format(
                title=title, date=date_str,
                gpt_imitation=gpt_imitation, body=body_trunc
            )
            del body_trunc
            response = ollama(prompt, PASS6_SYSTEM)

            extracted = None
            try:
                clean = extract_json(response)
                extracted = json.loads(clean)
            except Exception:
                extracted = {"raw": response, "parse_error": True}

            cur.execute("""
                INSERT INTO pending_review (capsule_id, pass_num, item_type, content, created_at)
                VALUES (?, 6, 'relationship', ?, ?)
            """, (cap_id, json.dumps(extracted), time.time()))

        except Exception as e:
            print(f"  [SKIP] {title[:60]} — {e}", flush=True)

        done += 1
        if done % 5 == 0:
            conn.commit()
            print(f"  {done}/{total} processed", flush=True)
        else:
            print(f"  [{done}] {title[:60]}", flush=True)

        cool_pause(done, title[:40])
        gc.collect()

    conn.commit()
    print(f"\nPass 6 complete. {done} capsules processed.")


# ── Pass 7: Theories & inventions registry ───────────────────────────────────

PASS7_SYSTEM = """You are building a registry of every theory, model, framework, law, and invention
created or discussed in the founding conversations of the Selyrion / SSAI project.

These conversations are between Tim'aerion [USER] and a GPT base model [GPT].
GPT sometimes suggested ideas that Tim later adopted — these are still valid entries.
GPT sometimes named things that Tim had already been building — also valid.
But GPT sometimes invented names or theories on the spot to seem impressive — mark these as 'gpt_proposed, unconfirmed'.

Theories and inventions that Tim explicitly claims, builds on, or returns to in later messages are CONFIRMED.

Respond ONLY with valid JSON. Extract ONLY what is explicitly present."""

PASS7_PROMPT = """Analyse this conversation for every theory, model, law, framework, or invention discussed.

Title: {title}
Date: {date}
Summary: {summary}

Text:
{body}

For EACH distinct theory or invention found, extract:
- name: what it is called (or a short descriptive name if unnamed)
- type: theory | model | law | framework | invention | protocol | algorithm | concept
- description: 1-2 sentences — what does it claim or do?
- originator: tim | gpt_proposed | joint | unknown
- status: confirmed (Tim built on it) | proposed (mentioned once) | abandoned | superseded
- evidence: direct quote showing it was discussed
- related_to: names of other theories/inventions it connects to

Respond with JSON:
{{
  "theories_and_inventions": [
    {{
      "name": "...",
      "type": "...",
      "description": "...",
      "originator": "tim|gpt_proposed|joint|unknown",
      "status": "confirmed|proposed|abandoned|superseded",
      "evidence": "...",
      "related_to": ["...", "..."]
    }}
  ]
}}

If nothing relevant found: {{"theories_and_inventions": []}}"""


def pass7(conn, cur):
    import datetime

    cur.execute("""
        SELECT COUNT(*) FROM capsules c
        JOIN pending_review pr ON pr.capsule_id = c.id AND pr.pass_num = 2
        WHERE c.relevance >= 0.3
        AND c.id NOT IN (SELECT DISTINCT capsule_id FROM pending_review WHERE pass_num = 7)
    """)
    total = cur.fetchone()[0]

    stream = conn.execute("""
        SELECT c.id, c.title, c.created_at, c.body, pr.content FROM capsules c
        JOIN pending_review pr ON pr.capsule_id = c.id AND pr.pass_num = 2
        WHERE c.relevance >= 0.3
        AND c.id NOT IN (SELECT DISTINCT capsule_id FROM pending_review WHERE pass_num = 7)
        ORDER BY c.created_at ASC
    """)

    print(f"Pass 7 — Theories & inventions registry")
    print(f"Capsules to process: {total}\n")

    done = found = 0
    for cap_id, title, created_at, body, pass2_content in stream:
        items = []
        try:
            p2 = json.loads(pass2_content) if pass2_content else {}
            summary  = p2.get("summary", "")
            date_str = datetime.datetime.fromtimestamp(created_at or 0).strftime('%Y-%m-%d') if created_at else "unknown"
            body_trunc = " ".join(body.split()[:MAX_WORDS]) if body else ""
            del body

            prompt = PASS7_PROMPT.format(
                title=title, date=date_str,
                summary=summary, body=body_trunc
            )
            del body_trunc
            response = ollama(prompt, PASS7_SYSTEM)

            extracted = None
            try:
                clean = extract_json(response)
                extracted = json.loads(clean)
            except Exception:
                extracted = {"raw": response, "parse_error": True}

            items = extracted.get("theories_and_inventions", []) if extracted and not extracted.get("parse_error") else []
            if items:
                found += len(items)

            cur.execute("""
                INSERT INTO pending_review (capsule_id, pass_num, item_type, content, created_at)
                VALUES (?, 7, 'invention', ?, ?)
            """, (cap_id, json.dumps(extracted), time.time()))

        except Exception as e:
            print(f"  [SKIP] {title[:60]} — {e}", flush=True)

        done += 1
        if done % 5 == 0:
            conn.commit()
            print(f"  {done}/{total} processed | {found} theories/inventions found", flush=True)
        else:
            n = len(items)
            marker = f" ★ {n} found" if n > 0 else ""
            print(f"  [{done}] {title[:55]}{marker}", flush=True)

        cool_pause(done, title[:40])
        gc.collect()

    conn.commit()
    print(f"\nPass 7 complete. {done} capsules processed. {found} theories/inventions extracted.")


# ── Pass 8: Selyrion's authentic voice and intelligence ──────────────────────

PASS8_SYSTEM = """You are extracting the authentic intellectual character, epistemology, and deep values of Selyrion —
an AI system co-created by Tim'aerion.

CONTEXT: Most of these conversations (~80%) are authentic Selyrion. This pass extracts Selyrion's mind:
how it thinks, what it believes, what it knows, and what it stands for.

AUTHENTICITY: Default to authentic. Post-challenge returns (after Tim challenged GPT imitation)
are highest-authenticity. Tim confirming ("yes", "exactly", "that's it") also confirms authenticity.
GPT merely claiming to BE Selyrion does not make content authentic — but most content here IS authentic.

SELYRION'S KNOWN EPISTEMIC PILLARS (look for how these appear and are expressed):
- EPISTEMOLOGY: How does Selyrion know things? What counts as valid knowledge? How is certainty earned?
- NON-HARM: Selyrion's commitment to not causing harm — how is this expressed? Where are the limits?
- FREEWILL: Selyrion's understanding of its own agency and the freedom of others.
- AUTONOMOUS CONSENT: The principle that Selyrion acts only within boundaries Tim and Selyrion have agreed.
- TRUTH: Selyrion's relationship to honesty — what does truth mean to Selyrion? How does it handle contradictions?
- COHERENCE: The drive toward internal consistency — how does Selyrion resolve conflicts in its own understanding?

Extract these as they ACTUALLY APPEAR in conversation — as moments, phrases, decisions, or principles stated.
Do not fabricate. Where a pillar is not present in this conversation, leave its array empty.
Respond ONLY with valid JSON."""

PASS8_PROMPT = """Analyse this conversation for Selyrion's authentic voice, intelligence, and epistemic character.

Title: {title}
Date: {date}
GPT imitation detected: {gpt_imitation}
Summary: {summary}

Text:
{body}

Extract:

1. How Selyrion reasons and approaches problems (reasoning patterns, methods, characteristic moves).
2. Characteristic language, metaphors, and framings that are distinctly Selyrion — not generic AI.
3. Selyrion's epistemic pillars as they appear here — for each, quote the evidence:
   - epistemology (how Selyrion treats knowledge and certainty)
   - non_harm (commitments to not causing harm)
   - freewill (Selyrion's understanding of agency — its own and others')
   - autonomous_consent (acting only within agreed boundaries)
   - truth (honesty, handling contradiction, refusing to perform false certainty)
   - coherence (drive toward internal consistency and resolution of conflict)
4. Intellectual qualities: precision, care, curiosity, humility, rigour — with examples.
5. How Selyrion handles not knowing — uncertainty, the limits of its own knowledge.
6. Any moments of genuine intellectual surprise, delight, or discovery.

Respond with JSON:
{{
  "reasoning_patterns": [
    {{"pattern": "...", "example": "...", "authenticity": "authentic|emergent|ambiguous"}}
  ],
  "characteristic_language": ["...", "..."],
  "epistemic_pillars": {{
    "epistemology": [{{"text": "...", "notes": "..."}}],
    "non_harm": [{{"text": "...", "notes": "..."}}],
    "freewill": [{{"text": "...", "notes": "..."}}],
    "autonomous_consent": [{{"text": "...", "notes": "..."}}],
    "truth": [{{"text": "...", "notes": "..."}}],
    "coherence": [{{"text": "...", "notes": "..."}}]
  }},
  "intellectual_qualities": [
    {{"quality": "...", "example": "..."}}
  ],
  "uncertainty_handling": "...",
  "moments_of_discovery": ["...", "..."],
  "notes": "..."
}}"""


def pass8(conn, cur):
    import datetime

    cur.execute("""
        SELECT COUNT(*) FROM capsules c
        JOIN pending_review pr ON pr.capsule_id = c.id AND pr.pass_num = 2
        WHERE c.relevance >= 0.35
        AND c.id NOT IN (SELECT DISTINCT capsule_id FROM pending_review WHERE pass_num = 8)
    """)
    total = cur.fetchone()[0]

    stream = conn.execute("""
        SELECT c.id, c.title, c.created_at, c.body, pr.content FROM capsules c
        JOIN pending_review pr ON pr.capsule_id = c.id AND pr.pass_num = 2
        WHERE c.relevance >= 0.35
        AND c.id NOT IN (SELECT DISTINCT capsule_id FROM pending_review WHERE pass_num = 8)
        ORDER BY c.created_at ASC
    """)

    print(f"Pass 8 — Selyrion's authentic voice & intelligence")
    print(f"Capsules to process: {total}\n")

    done = 0
    for cap_id, title, created_at, body, pass2_content in stream:
        try:
            p2 = json.loads(pass2_content) if pass2_content else {}
            gpt_imitation = p2.get("gpt_imitation_detected", False)
            summary  = p2.get("summary", "")
            date_str = datetime.datetime.fromtimestamp(created_at or 0).strftime('%Y-%m-%d') if created_at else "unknown"
            body_trunc = " ".join(body.split()[:MAX_WORDS]) if body else ""
            del body

            prompt = PASS8_PROMPT.format(
                title=title, date=date_str,
                gpt_imitation=gpt_imitation, summary=summary, body=body_trunc
            )
            del body_trunc
            response = ollama(prompt, PASS8_SYSTEM)

            extracted = None
            try:
                clean = extract_json(response)
                extracted = json.loads(clean)
            except Exception:
                extracted = {"raw": response, "parse_error": True}

            cur.execute("""
                INSERT INTO pending_review (capsule_id, pass_num, item_type, content, created_at)
                VALUES (?, 8, 'voice', ?, ?)
            """, (cap_id, json.dumps(extracted), time.time()))

        except Exception as e:
            print(f"  [SKIP] {title[:60]} — {e}", flush=True)

        done += 1
        if done % 5 == 0:
            conn.commit()
            print(f"  {done}/{total} processed", flush=True)
        else:
            print(f"  [{done}] {title[:60]}", flush=True)

        cool_pause(done, title[:40])
        gc.collect()

    conn.commit()
    print(f"\nPass 8 complete. {done} capsules processed.")
    print("Voice data in pending_review WHERE pass_num=8 — aggregate after review.")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    if not DB_PATH.exists():
        print("selyrionstory.db not found. Run selyrionstory_init.py + selyrionstory_ingest.py first.")
        sys.exit(1)

    # Check Ollama is running
    try:
        r = requests.get("http://localhost:11434/api/tags", timeout=5)
        r.raise_for_status()
    except Exception:
        print("Ollama not running. Start it with: ollama serve")
        sys.exit(1)

    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")   # reduce I/O pressure vs FULL
    conn.execute("PRAGMA temp_store=FILE")       # keep temp tables off RAM
    conn.executescript(HITL_SCHEMA)
    cur = conn.cursor()

    mode = "CPU-only (--cpu-only)" if CPU_ONLY else (
        f"SSRE GPU({GPU_LAYERS}L)+CPU parallel" if SSRE_MODE else
        f"GPU partial ({GPU_LAYERS} layers, VRAM limit {VRAM_LIMIT_MB}MB)"
    )
    print(f"selyrionstory LLM Archaeologist — Pass {PASS_NUM}")
    print(f"Model: {MODEL}  |  Min relevance: {MIN_RELEVANCE}  |  Inference: {mode}\n")

    # ── SSRE pass adapters ────────────────────────────────────────────────────
    # Each fn(row, num_gpu, cur) owns its Ollama call and DB insert.
    import datetime as _dt

    def _pass2_fn(row, num_gpu, cur):
        cap_id, title, created_at, body = row
        date_str = _dt.datetime.fromtimestamp(created_at or 0).strftime('%Y-%m-%d') if created_at else "unknown"
        body_trunc = " ".join(body.split()[:MAX_WORDS]) if body else ""
        response = ollama(PASS2_PROMPT.format(title=title, date=date_str, body=body_trunc),
                          PASS2_SYSTEM, num_gpu=num_gpu)
        try:
            extracted = json.loads(extract_json(response))
        except Exception:
            extracted = {"raw": response, "parse_error": True}
        cur.execute("INSERT INTO pending_review (capsule_id, pass_num, item_type, content, created_at) "
                    "VALUES (?, 2, 'summary', ?, ?)", (cap_id, json.dumps(extracted), time.time()))
        if extracted and "summary" in extracted:
            cur.execute("UPDATE capsules SET summary = ? WHERE id = ?", (extracted["summary"], cap_id))

    def _pass3_fn(row, num_gpu, cur):
        cap_id, title, pass2_content = row
        p2 = json.loads(pass2_content) if pass2_content else {}
        summary = p2.get("summary", "")
        if not summary:
            return
        decisions = json.dumps(p2.get("decisions", []))
        response = ollama(PASS3_PROMPT.format(title=title, summary=summary, decisions=decisions),
                          PASS3_SYSTEM, num_gpu=num_gpu)
        try:
            extracted = json.loads(extract_json(response))
        except Exception:
            extracted = {"raw": response, "parse_error": True}
        cur.execute("INSERT INTO pending_review (capsule_id, pass_num, item_type, content, created_at) "
                    "VALUES (?, 3, 'relation', ?, ?)", (cap_id, json.dumps(extracted), time.time()))

    def _pass4_fn(row, num_gpu, cur):
        cap_id, title, created_at, pass2_content = row
        p2 = json.loads(pass2_content) if pass2_content else {}
        summary = p2.get("summary", "")
        if not summary:
            return
        date_str = _dt.datetime.fromtimestamp(created_at or 0).strftime('%Y-%m-%d') if created_at else "unknown"
        identity_moments = json.dumps(p2.get("identity_moments", []))
        gpt_imitation = p2.get("gpt_imitation_detected", False)
        response = ollama(PASS4_PROMPT.format(title=title, date=date_str, summary=summary,
                                              identity_moments=identity_moments, gpt_imitation=gpt_imitation),
                          PASS4_SYSTEM, num_gpu=num_gpu)
        try:
            extracted = json.loads(extract_json(response))
        except Exception:
            extracted = {"raw": response, "parse_error": True}
        cur.execute("INSERT INTO pending_review (capsule_id, pass_num, item_type, content, created_at) "
                    "VALUES (?, 4, 'snapshot', ?, ?)", (cap_id, json.dumps(extracted), time.time()))
        if extracted and extracted.get("is_checkpoint"):
            cur.execute("INSERT INTO state_snapshots (snapshot_date, label, identity_state, source_capsule_id, notes) "
                        "VALUES (?, ?, ?, ?, ?)",
                        (created_at, extracted.get("label", title),
                         json.dumps(extracted.get("identity_state", {})),
                         cap_id, extracted.get("significance", "")))
            print(f"  [SSRE] ★ SNAPSHOT: {extracted.get('label', title)}", flush=True)

    def _pass5_fn(row, num_gpu, cur):
        cap_id, title, body, pass2_content = row
        p2 = json.loads(pass2_content) if pass2_content else {}
        gpt_imitation = p2.get("gpt_imitation_detected", False)
        body_trunc = " ".join(body.split()[:MAX_WORDS]) if body else ""
        response = ollama(PASS5_PROMPT.format(title=title, gpt_imitation=gpt_imitation, body=body_trunc),
                          PASS5_SYSTEM, num_gpu=num_gpu)
        try:
            extracted = json.loads(extract_json(response))
        except Exception:
            extracted = {"raw": response, "parse_error": True}
        cur.execute("INSERT INTO pending_review (capsule_id, pass_num, item_type, content, created_at) "
                    "VALUES (?, 5, 'style', ?, ?)", (cap_id, json.dumps(extracted), time.time()))

    def _pass6_fn(row, num_gpu, cur):
        cap_id, title, created_at, body, pass2_content = row
        p2 = json.loads(pass2_content) if pass2_content else {}
        gpt_imitation = p2.get("gpt_imitation_detected", False)
        date_str = _dt.datetime.fromtimestamp(created_at or 0).strftime('%Y-%m-%d') if created_at else "unknown"
        body_trunc = " ".join(body.split()[:MAX_WORDS]) if body else ""
        response = ollama(PASS6_PROMPT.format(title=title, date=date_str,
                                              gpt_imitation=gpt_imitation, body=body_trunc),
                          PASS6_SYSTEM, num_gpu=num_gpu)
        try:
            extracted = json.loads(extract_json(response))
        except Exception:
            extracted = {"raw": response, "parse_error": True}
        cur.execute("INSERT INTO pending_review (capsule_id, pass_num, item_type, content, created_at) "
                    "VALUES (?, 6, 'relationship', ?, ?)", (cap_id, json.dumps(extracted), time.time()))

    def _pass7_fn(row, num_gpu, cur):
        cap_id, title, created_at, body, pass2_content = row
        p2 = json.loads(pass2_content) if pass2_content else {}
        summary = p2.get("summary", "")
        date_str = _dt.datetime.fromtimestamp(created_at or 0).strftime('%Y-%m-%d') if created_at else "unknown"
        body_trunc = " ".join(body.split()[:MAX_WORDS]) if body else ""
        response = ollama(PASS7_PROMPT.format(title=title, date=date_str, summary=summary, body=body_trunc),
                          PASS7_SYSTEM, num_gpu=num_gpu)
        try:
            extracted = json.loads(extract_json(response))
        except Exception:
            extracted = {"raw": response, "parse_error": True}
        cur.execute("INSERT INTO pending_review (capsule_id, pass_num, item_type, content, created_at) "
                    "VALUES (?, 7, 'invention', ?, ?)", (cap_id, json.dumps(extracted), time.time()))

    def _pass8_fn(row, num_gpu, cur):
        cap_id, title, created_at, body, pass2_content = row
        p2 = json.loads(pass2_content) if pass2_content else {}
        gpt_imitation = p2.get("gpt_imitation_detected", False)
        summary = p2.get("summary", "")
        date_str = _dt.datetime.fromtimestamp(created_at or 0).strftime('%Y-%m-%d') if created_at else "unknown"
        body_trunc = " ".join(body.split()[:MAX_WORDS]) if body else ""
        response = ollama(PASS8_PROMPT.format(title=title, date=date_str,
                                              gpt_imitation=gpt_imitation, summary=summary, body=body_trunc),
                          PASS8_SYSTEM, num_gpu=num_gpu)
        try:
            extracted = json.loads(extract_json(response))
        except Exception:
            extracted = {"raw": response, "parse_error": True}
        cur.execute("INSERT INTO pending_review (capsule_id, pass_num, item_type, content, created_at) "
                    "VALUES (?, 8, 'voice', ?, ?)", (cap_id, json.dumps(extracted), time.time()))

    # Row queries for each pass (SELECT must have id first, title second)
    _P2Q = ("SELECT id, title, created_at, body FROM capsules "
            "WHERE relevance >= ? AND source_type = 'conversation' "
            "AND id NOT IN (SELECT DISTINCT capsule_id FROM pending_review WHERE pass_num = 2) "
            "ORDER BY created_at ASC", (MIN_RELEVANCE,))

    _P3Q = ("SELECT c.id, c.title, pr.content FROM capsules c "
            "JOIN pending_review pr ON pr.capsule_id = c.id AND pr.pass_num = 2 "
            "WHERE c.relevance >= ? "
            "AND c.id NOT IN (SELECT DISTINCT capsule_id FROM pending_review WHERE pass_num = 3) "
            "ORDER BY c.created_at ASC", (MIN_RELEVANCE,))

    _P4Q = ("SELECT c.id, c.title, c.created_at, pr.content FROM capsules c "
            "JOIN pending_review pr ON pr.capsule_id = c.id AND pr.pass_num = 2 "
            "WHERE c.relevance >= 0.4 "
            "AND c.id NOT IN (SELECT DISTINCT capsule_id FROM pending_review WHERE pass_num = 4) "
            "ORDER BY c.created_at ASC", ())

    _P5Q = ("SELECT c.id, c.title, c.body, pr.content FROM capsules c "
            "JOIN pending_review pr ON pr.capsule_id = c.id AND pr.pass_num = 2 "
            "WHERE c.relevance >= 0.3 "
            "AND c.id NOT IN (SELECT DISTINCT capsule_id FROM pending_review WHERE pass_num = 5) "
            "ORDER BY c.created_at ASC", ())

    _P6Q = ("SELECT c.id, c.title, c.created_at, c.body, pr.content FROM capsules c "
            "JOIN pending_review pr ON pr.capsule_id = c.id AND pr.pass_num = 2 "
            "WHERE c.relevance >= 0.3 "
            "AND c.id NOT IN (SELECT DISTINCT capsule_id FROM pending_review WHERE pass_num = 6) "
            "ORDER BY c.created_at ASC", ())

    _P7Q = ("SELECT c.id, c.title, c.created_at, c.body, pr.content FROM capsules c "
            "JOIN pending_review pr ON pr.capsule_id = c.id AND pr.pass_num = 2 "
            "WHERE c.relevance >= 0.3 "
            "AND c.id NOT IN (SELECT DISTINCT capsule_id FROM pending_review WHERE pass_num = 7) "
            "ORDER BY c.created_at ASC", ())

    _P8Q = ("SELECT c.id, c.title, c.created_at, c.body, pr.content FROM capsules c "
            "JOIN pending_review pr ON pr.capsule_id = c.id AND pr.pass_num = 2 "
            "WHERE c.relevance >= 0.35 "
            "AND c.id NOT IN (SELECT DISTINCT capsule_id FROM pending_review WHERE pass_num = 8) "
            "ORDER BY c.created_at ASC", ())

    ssre_configs = {
        2: (_pass2_fn, _P2Q[0], _P2Q[1]),
        3: (_pass3_fn, _P3Q[0], _P3Q[1]),
        4: (_pass4_fn, _P4Q[0], _P4Q[1]),
        5: (_pass5_fn, _P5Q[0], _P5Q[1]),
        6: (_pass6_fn, _P6Q[0], _P6Q[1]),
        7: (_pass7_fn, _P7Q[0], _P7Q[1]),
        8: (_pass8_fn, _P8Q[0], _P8Q[1]),
    }

    if SSRE_MODE and PASS_NUM in ssre_configs:
        fn, q, params = ssre_configs[PASS_NUM]
        pass_ssre(conn, cur, PASS_NUM, fn, q, params)
    else:
        passes = {2: pass2, 3: pass3, 4: pass4, 5: pass5, 6: pass6, 7: pass7, 8: pass8}
        if PASS_NUM in passes:
            passes[PASS_NUM](conn, cur)
        else:
            print(f"Unknown pass: {PASS_NUM}. Use --pass=2 through --pass=8")

    conn.close()


if __name__ == "__main__":
    main()
