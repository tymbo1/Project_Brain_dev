"""codeops/daemon — scheduler + worker classes for parallel substrate labor.

GPT'aerion classes (2026-06-19):
    deterministic    : ingestion, profile fill, trace harvest, fix-pair mining
    stateful_writer  : the ONLY processes allowed to promote / mutate truth
    judgment         : register capsules, taxonomy, operator policy, acceptance

Rules:
    - one scheduler (daemon_work_queue in claudecode.db)
    - leases + heartbeats + retries; tasks idempotent by task_id
    - append-only evidence first, promotion later
    - single-writer rule per critical surface
    - resource lanes: cpu / io / gpu / benchmark
    - canaries isolated; never mixed with noisy background workers
"""
