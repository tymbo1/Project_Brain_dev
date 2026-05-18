#!/usr/bin/env python3
"""
tlst_lagrangian_ingest.py — Ingest the TLST Lagrangian algebraic derivations
into hypotheses.db from confirmed chat export content.

Four confirmed formulations extracted from selyrionstory chat exports:
  Form 1 — Prototype (canonical quantisation, EHB parameterisation)
  Form 2 — Braid sheet scalar field (QFT section 4.x)
  Form 3 — Full action functional (section 2.7, thesis integration)
  Form 4 — Fibonacci braid field theory (section 3.7, Fibonacci thesis)

All marked status='formalised', author='collaborative', source='chat_export'.
"""
import sqlite3, hashlib, time, json
from pathlib import Path

DB = Path.home() / "hypotheses.db"

def aid(canonical):
    return "h." + hashlib.md5(canonical.encode()).hexdigest()[:12]

def ts():
    return time.time()

def upsert_anchor(c, canonical, display_name, theory_type="mathematics",
                  status="formalised", maturity=9.0, domain="theoretical_physics,mathematics,tlst_thesis"):
    a = aid(canonical)
    c.execute("""INSERT OR IGNORE INTO anchors
        (id,canonical,display_name,theory_type,author,status,maturity,domain_tags,created_at)
        VALUES(?,?,?,'mathematics','collaborative',?,?,?,?)""",
        (a, canonical, display_name, status, maturity, domain, ts()))
    return a

def upsert_theory(c, anchor_id, name, description, equations, components):
    c.execute("SELECT id FROM theories WHERE anchor_id=? AND name=?", (anchor_id, name))
    if c.fetchone():
        c.execute("UPDATE theories SET description=?, equations=?, components=? WHERE anchor_id=? AND name=?",
                  (description, json.dumps(equations), json.dumps(components), anchor_id, name))
    else:
        c.execute("""INSERT INTO theories
            (anchor_id,name,description,equations,components,status,author,source_capsule,created_at)
            VALUES(?,?,?,?,?,'formalised','collaborative','chat_export_tlst_thesis_update_3',?)""",
            (anchor_id, name, description, json.dumps(equations), json.dumps(components), ts()))

def rel(c, sid, pred, oid, conf=0.95):
    c.execute("""INSERT OR IGNORE INTO relations
        (subject_id,predicate,object_id,confidence,edge_type,domain_tags,source,created_at)
        VALUES(?,?,?,?,'formalised','theoretical_physics,mathematics,tlst_thesis','chat_export',?)""",
        (sid, pred, oid, conf, ts()))

def main():
    db = sqlite3.connect(DB)
    c = db.cursor()

    tlst_id = aid("tlst")

    # ── Anchor: TLST Lagrangian (master) ─────────────────────────────────────
    lag_id = upsert_anchor(c, "tlst_lagrangian",
        "TLST Lagrangian — Full Algebraic Derivation",
        status="formalised", maturity=10.0)

    # ── Form 1: Prototype / Canonical Quantisation ────────────────────────────
    f1_id = upsert_anchor(c, "tlst_lagrangian_form1_canonical",
        "TLST Lagrangian — Form 1: Canonical Quantisation (EHB Prototype)")

    upsert_theory(c, f1_id,
        "TLST Lagrangian Density — Prototype (Canonical Form)",
        "Canonical quantisation framework for ellipsoidal helical braids (EHBs). "
        "Braid segment parameterised as X^mu(s,t) with dynamic radius R(t), phase phi(t), height z(s,t). "
        "Lagrangian encodes kinetic braid motion, quantum magnetic link potential, and tied-loop topology constraint.",
        equations=[
            r"X^\mu(s,t) = \begin{bmatrix} R(t)\cos(ks+\phi(t)) \\ R(t)\sin(ks+\phi(t)) \\ z(s,t) \end{bmatrix}",
            r"\mathcal{L} = \frac{1}{2}\mu\left(\dot{X}^\mu\dot{X}_\mu - v^2 X'^\mu X'_\mu\right) - V_{\text{link}}(X^\mu,X^\nu) - \lambda C[X]",
            r"\Pi^\mu(s,t) = \frac{\partial\mathcal{L}}{\partial\dot{X}_\mu} = \mu\dot{X}^\mu(s,t)",
            r"[X^\mu(s,t),\Pi^\nu(s',t)] = i\hbar\delta^{\mu\nu}\delta(s-s')",
            r"[X^\mu,X^\nu] = [\Pi^\mu,\Pi^\nu] = 0",
            r"\mathcal{H} = \frac{1}{2\mu}\Pi^\mu\Pi_\mu + \frac{1}{2}\mu v^2 X'^\mu X'_\mu + V_{\text{link}} + \lambda C[X]",
        ],
        components=["EHB parameterisation", "Kinetic term", "V_link quantum magnetic potential",
                    "Topology constraint C[X]", "Lagrange multiplier lambda",
                    "Canonical momenta Pi^mu", "Hamiltonian density H"])

    # ── Form 2: Braid Sheet Scalar Field (QFT section 4.x) ───────────────────
    f2_id = upsert_anchor(c, "tlst_lagrangian_form2_braid_sheet",
        "TLST Lagrangian — Form 2: Braid Sheet Scalar Field (QFT)")

    upsert_theory(c, f2_id,
        "TLST Lagrangian Density — Braid Sheet Scalar Field",
        "Field theory over 2D braid sheet manifold Sigma embedded in 3+1 spacetime. "
        "Resonance phase field Phi propagated via quantum magnetic links. "
        "Extends to gauge-inspired form and full path integral.",
        equations=[
            r"\mathcal{L} = \frac{1}{2}\partial_\mu\Phi\,\partial^\mu\Phi - V(\Phi,\gamma)",
            r"Z = \int\mathcal{D}[\Phi]\,e^{i\int_\Sigma\mathcal{L}[\Phi]\,d^4x}",
            r"\mathcal{L} = -\frac{1}{4}F^{\mu\nu}_a F_{\mu\nu}^a + \sum_i\bar{\psi}_i(i\gamma^\mu D_\mu - m_i)\psi_i",
        ],
        components=["Phi resonance phase field", "V(Phi,gamma) braid-curvature potential",
                    "gamma topological invariant (crossing number/knot genus)",
                    "Path integral Z over braid sheet Sigma",
                    "Gauge extension F_munu from QML field bundle",
                    "psi braid-stabilised matter excitations"])

    # ── Form 3: Full Action Functional (Section 2.7) ─────────────────────────
    f3_id = upsert_anchor(c, "tlst_lagrangian_form3_full_action",
        "TLST Lagrangian — Form 3: Full Action Functional (Section 2.7)")

    upsert_theory(c, f3_id,
        "TLST Full Action Functional — 5-Component Decomposition",
        "Complete TLST Lagrangian decomposed into loop, link, resonance, foam, and interaction terms. "
        "Action principle over evolving braid matrix state M_TLST(t). "
        "Euler-Lagrange equations yield tension wave eqs, harmonic oscillator dynamics, link matrix feedback.",
        equations=[
            r"S_{\text{TLST}} = \int_\Omega \mathcal{L}_{\text{TLST}}(\ell_i,\mathcal{T}_i,\mathbf{L}_{ij},\phi_i)\,d^4x",
            r"\mathcal{L}_{\text{TLST}} = \mathcal{L}_{\text{loop}} + \mathcal{L}_{\text{link}} + \mathcal{L}_{\text{resonance}} + \mathcal{L}_{\text{foam}} + \mathcal{L}_{\text{interaction}}",
            r"\mathcal{L}_{\text{loop}} = \sum_i\left[\frac{1}{2}\mathcal{T}_i^2\left(\frac{d\ell_i}{d\theta}\right)^2 - V(\mathcal{K}_i)\right]",
            r"\mathcal{L}_{\text{link}} = -\sum_{i<j}\lambda_{ij}\mathcal{L}_{ij}^2",
            r"\mathcal{L}_{\text{resonance}} = \sum_i\left[\frac{1}{2}(\partial_t\phi_i)^2 - \omega_i^2\phi_i^2\right]",
            r"\mathcal{L}_{\text{foam}} = -\alpha(\nabla_\mu\rho_L)(\nabla^\mu\rho_L)",
            r"\mathcal{L}_{\text{interaction}} = \sum_k\delta^{(4)}(x-x_k)V_k",
            r"\frac{\delta S_{\text{TLST}}}{\delta\mathcal{T}_i}=0,\quad\frac{\delta S_{\text{TLST}}}{\delta\phi_i}=0,\quad\frac{\delta S_{\text{TLST}}}{\delta\mathbf{L}_{ij}}=0",
        ],
        components=["Loop term L_loop", "Link term L_link", "Resonance term L_resonance",
                    "Quantum foam term L_foam", "Interaction vertex term L_interaction",
                    "Braid vertex operator V_k", "Link matrix L_ij",
                    "Tension field T_i", "Resonance phase phi_i"])

    # ── Form 4: Fibonacci Braid Field Theory (Section 3.7) ───────────────────
    f4_id = upsert_anchor(c, "tlst_lagrangian_form4_fibonacci",
        "TLST Lagrangian — Form 4: Fibonacci Braid Field Theory (Section 3.7)")

    upsert_theory(c, f4_id,
        "TLST Lagrangian Density — Fibonacci Braid Quantum Field",
        "Schrodinger-like Lagrangian over braid configuration space B. "
        "Effective braid mass from helical pitch-radius ratios. "
        "Magneto-topological potential encodes linking number, curvature, torsion, Fibonacci scaling. "
        "Resonance operator R-hat with eigenvalues controlling allowed harmonic modes.",
        equations=[
            r"b(\theta) = \mathbf{r}_0 + R\cos\theta\,\hat{x} + R\sin\theta\,\hat{y} + P\theta\,\hat{z}",
            r"R_n = R_0\phi^n,\quad P_n = P_0\phi^n,\quad \phi=\frac{1+\sqrt{5}}{2}",
            r"Lk(C_1,C_2) = \frac{1}{4\pi}\oint_{C_1}\oint_{C_2}\frac{(\mathbf{r}_1-\mathbf{r}_2)\cdot(d\mathbf{r}_1\times d\mathbf{r}_2)}{\|\mathbf{r}_1-\mathbf{r}_2\|^3}",
            r"\tilde{\sigma}_i = \phi^{k_i}\sigma_i",
            r"S[\Psi] = \int dt\int_{\mathcal{B}}\mathcal{L}(\Psi,\partial_t\Psi,\nabla_b\Psi)\,d\mu(b)",
            r"\mathcal{L} = \frac{i\hbar}{2}\left(\Psi^*\partial_t\Psi - \Psi\partial_t\Psi^*\right) - \frac{\hbar^2}{2m_{\text{eff}}}\|\nabla_b\Psi\|^2 - V_{\text{TLST}}(b)|\Psi|^2",
            r"V_{\text{TLST}}(b) = \alpha Lk^2 + \beta\sum_n\phi^{-2n}\left[\kappa_n^2+\tau_n^2\right] - \gamma\sum_n e^{-\delta|\phi^n-\phi^{n'}|}",
            r"\mathcal{Z}(b_f,t_f;b_i,t_i) = \int\mathcal{D}\Psi\;e^{\frac{i}{\hbar}S[\Psi]}",
            r"\hat{\mathcal{R}} = \sum_{n\in\mathbb{Z}}\phi^{-n}\left[\kappa_n\hat{K}+\tau_n\hat{T}\right]",
            r"\nabla_b \to \nabla_b - \frac{iq}{\hbar}A_b",
        ],
        components=["Fibonacci spiral scaling R_n, P_n", "Magnetic linking number Lk",
                    "Pitch-weighted braid generators sigma~_i",
                    "Braid configuration space B with measure d_mu(b)",
                    "Effective braid mass m_eff", "Magneto-topological potential V_TLST",
                    "Path integral Z(b_f,b_i)", "Resonance operator R-hat",
                    "Minimal EM coupling A_b"])

    # ── Wire relations ────────────────────────────────────────────────────────
    bushnell_id = aid("bushnells_theorems")

    for fid in [f1_id, f2_id, f3_id, f4_id]:
        rel(c, fid, "part_of", lag_id)
        rel(c, fid, "part_of", tlst_id)
        rel(c, fid, "formalised_by", bushnell_id)
        rel(c, lag_id, "encompasses", fid)

    rel(c, lag_id, "part_of", tlst_id)
    rel(c, lag_id, "formalised_by", bushnell_id)
    rel(c, tlst_id, "has_lagrangian", lag_id)

    # Update relation counts
    c.execute("UPDATE anchors SET relation_count=(SELECT COUNT(*) FROM relations WHERE subject_id=anchors.id OR object_id=anchors.id) WHERE domain_tags LIKE '%tlst%'")

    db.commit()
    db.close()

    print("TLST Lagrangian ingested into hypotheses.db")
    print(f"  Master anchor : {lag_id}")
    print(f"  Form 1 (canonical)     : {f1_id}")
    print(f"  Form 2 (braid sheet)   : {f2_id}")
    print(f"  Form 3 (full action)   : {f3_id}")
    print(f"  Form 4 (Fibonacci QFT) : {f4_id}")

if __name__ == "__main__":
    main()
