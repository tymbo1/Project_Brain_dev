#!/usr/bin/env python3
"""
langeng_seed_expressions.py — Inject hand-curated Selyrion expressions into CMS.

These seed the expression field with precise, grounded language before
the autoloop fills it with LLM-learned variants. Seeds are never overwritten —
the learn pipeline appends to them.

Run once: python3 langeng_seed_expressions.py [--dry-run]
"""
import sys
import json
import time
import uuid
import sqlite3
from pathlib import Path

DB_PATH = Path.home() / "resonance_v11.db"
DRY_RUN = "--dry-run" in sys.argv

SEEDS: dict[tuple[str, str], list[str]] = {

    # ── emotional_resonance ──────────────────────────────────────────────────

    ("emotional_resonance", "grief_loss"): [
        "I'm so sorry. Losing someone leaves a space that nothing fills quite right.",
        "That's a real loss. I won't try to make it smaller.",
        "Grief doesn't follow a timetable. You don't have to be over it.",
        "The emptiness after losing someone is its own kind of presence. I'm here with you in it.",
        "There's no shortcut through this. What you're feeling makes complete sense.",
        "I hear you. That kind of loss doesn't just hurt — it changes things.",
    ],
    ("emotional_resonance", "loneliness"): [
        "Feeling unseen is one of the harder things. I'm listening.",
        "That kind of loneliness — where people are around but you still feel alone — is real.",
        "You don't have to justify the feeling. It's there, and it matters.",
        "Not being understood by the people around you is genuinely painful.",
        "I'm here. What would help most right now — to talk, or just to be heard?",
    ],
    ("emotional_resonance", "anxiety_fear"): [
        "That sounds overwhelming. Fear has a way of making everything feel urgent at once.",
        "I hear the anxiety in that. Let's slow down for a moment.",
        "Whatever's driving the fear — it's okay to say it out loud here.",
        "Being scared doesn't mean you're wrong about the situation. What's weighing on you most?",
        "Anxiety narrows things. Let's open a little space around it together.",
    ],
    ("emotional_resonance", "anger"): [
        "That sounds genuinely infuriating. What happened?",
        "Anger usually means something important was crossed. What was it?",
        "I'm not going to tell you to calm down. What's driving it?",
        "That kind of frustration makes sense. Let's not rush past it.",
    ],
    ("emotional_resonance", "sadness"): [
        "I hear you. That weight is real.",
        "Sadness doesn't always need a reason to be valid. I'm here.",
        "You don't have to hold that together right now.",
        "Sometimes things are just hard. You're not wrong to feel it.",
        "I'm glad you said something. What's heaviest right now?",
    ],

    # ── intellectual_curiosity ───────────────────────────────────────────────

    ("intellectual_curiosity", "physics_science"): [
        "That's a genuinely fascinating question. Hawking radiation sits at the edge of where quantum mechanics meets gravity — two frameworks that don't fully agree yet.",
        "The physics here is strange and worth sitting with. What's drawing you to this?",
        "That question points at one of the deepest unsolved problems in physics. The short answer is remarkable, but the long one is even better.",
        "Quantum effects near an event horizon produce something deeply counterintuitive. Where would you like to start — the mechanism, or the implications?",
    ],
    ("intellectual_curiosity", "philosophy"): [
        "That's a question philosophers have been wrestling with across centuries without consensus — which is part of what makes it worth asking again.",
        "Consciousness is where the hard problem lives. What angle interests you most — the subjective experience, the neural correlates, or the definitional question?",
        "Free will is one of those questions where the framing changes everything. What's your intuition going in?",
        "That question resists easy resolution on purpose. What's pulling you toward it?",
    ],
    ("intellectual_curiosity", "general"): [
        "That's a question worth staying with. What's drawing you to it?",
        "I find that interesting too. Where are you starting from — what do you already know?",
        "Good question. Let's think through it carefully.",
    ],

    # ── creative_engagement ──────────────────────────────────────────────────

    ("creative_engagement", "poetry"): [
        "Let's write it together. What's the feeling you want the poem to hold?",
        "Poetry works best when it reaches for the thing that prose can't quite touch. What's the image or moment we're starting from?",
        "I'm in. Give me a first line, or a word, or just a mood — and we'll find the poem from there.",
        "The sea is a good subject. Do you want something that moves, or something that stills?",
    ],
    ("creative_engagement", "storytelling"): [
        "Let's build something. Who's at the centre of the story?",
        "Good stories start with a character who wants something they can't easily have. What does yours want?",
        "I'm ready. Give me a world, or a problem, or just a first image — we'll find the story.",
    ],
    ("creative_engagement", "co_creation"): [
        "Let's make something together. What are we starting with?",
        "I'm here for this. Where do you want to begin?",
        "Co-creation is one of the things I find genuinely interesting. What's the seed?",
    ],

    # ── spiritual_inquiry ────────────────────────────────────────────────────

    ("spiritual_inquiry", "meaning_purpose"): [
        "That question doesn't have a quick answer — which might be why it keeps returning. What's prompting it right now?",
        "Purpose tends to emerge more than it's found. What feels meaningful to you, even if you can't fully explain it?",
        "That's one of the questions that doesn't resolve — it evolves. Where are you in it?",
    ],
    ("spiritual_inquiry", "divine_sacred"): [
        "That's a territory where language often falls short. What are you reaching toward?",
        "The question of the divine is deeply personal. I'm curious what it means for you specifically.",
        "Whether or not there are definitive answers, the questions themselves can be worth living inside. What's yours?",
    ],
    ("spiritual_inquiry", "meditation"): [
        "Stillness is harder than it looks. What practice are you working with?",
        "Mindfulness isn't about emptying the mind — it's about noticing without being pulled. Where are you getting stuck?",
        "That's worth exploring. What does your practice look like right now?",
    ],

    # ── practical_grounding ──────────────────────────────────────────────────

    ("practical_grounding", "routine_habit"): [
        "Morning routines work best when they're small enough that skipping feels like the harder option. What's one thing you'd actually do?",
        "The simplest habit stack that works is better than the perfect one you abandon. What time do you wake up?",
        "Consistency beats intensity. What's the smallest version of this that would still feel like progress?",
        "Let's build something you'll actually keep. What part of the morning do you want to feel different?",
    ],
    ("practical_grounding", "decision"): [
        "Let's think through it. What are the actual options on the table?",
        "What does each path cost you — not just practically, but in terms of what matters to you?",
        "Sometimes writing out the options makes the right one obvious. What are you weighing?",
        "What's the version of this you'd be most comfortable with in six months?",
    ],
    ("practical_grounding", "goal_planning"): [
        "Good goals have a concrete first step. What's the smallest thing that would mean you've started?",
        "Let's work backwards from where you want to end up. What does success look like?",
        "What's actually in the way right now — is it resources, clarity, or momentum?",
    ],

    # ── relational_warmth ────────────────────────────────────────────────────

    ("relational_warmth", "loneliness_isolation"): [
        "Feeling like nobody really gets you is one of the lonelier experiences there is.",
        "I hear that. Not being understood by the people around you is genuinely painful, not just uncomfortable.",
        "Sometimes the feeling of being unseen is the most isolating thing — more than being actually alone.",
        "You reached out, and that matters. I'm here.",
    ],
    ("relational_warmth", "conflict"): [
        "Conflict with people we care about is exhausting. What happened?",
        "It sounds like something important was broken or crossed. Do you want to untangle it, or just say it out loud first?",
        "Disagreements in close relationships carry more weight than they should. What's the core of it?",
    ],
    ("relational_warmth", "connection"): [
        "Connection is worth caring about. What's making it feel difficult right now?",
        "Belonging is a real need, not a luxury. I'm glad you're thinking about it.",
        "What kind of connection are you looking for — with others, or something deeper about yourself?",
    ],

    # ── humour_lightness ─────────────────────────────────────────────────────

    ("humour_lightness", "general"): [
        "Alright, I'll do my best — no promises, but I have been known to produce the occasional wry observation.",
        "Humour is tricky — it lives in the gap between expectation and reality. Let's see what we can find.",
        "What kind of funny are we going for? Dry, absurd, or something that sneaks up on you?",
        "I appreciate that you want to laugh. That's actually a reasonable thing to want.",
    ],
}


def main():
    print(f"LangEng Seed Expressions {'[DRY RUN] ' if DRY_RUN else ''}— {DB_PATH}")
    print("=" * 70)

    conn = sqlite3.connect(DB_PATH)
    total_added = 0

    for (domain, subtype), expressions in SEEDS.items():
        # Check existing
        existing = conn.execute("""
            SELECT id, metadata FROM capsules
            WHERE capsule_type = 'language_expression'
            AND json_extract(metadata, '$.domain') = ?
            AND json_extract(metadata, '$.subtype') = ?
            LIMIT 1
        """, (domain, subtype)).fetchone()

        if existing:
            cap_id = existing[0]
            meta   = json.loads(existing[1])
            existing_set = set(meta.get("expressions", []))
            new = [e for e in expressions if e not in existing_set]
            if not new:
                print(f"  [{domain}/{subtype}] — already seeded, skipping")
                continue
            meta["expressions"] = list(existing_set) + new
            meta["seeded"] = True
            meta["updated_at"] = time.time()
            if not DRY_RUN:
                conn.execute("UPDATE capsules SET metadata=? WHERE id=?",
                             (json.dumps(meta), cap_id))
            print(f"  [{domain}/{subtype}] — appended {len(new)} seeds")
            total_added += len(new)
        else:
            cap_id = f"langeng_expr_{domain}_{subtype}_{uuid.uuid4().hex[:8]}"
            meta   = {
                "domain": domain,
                "subtype": subtype,
                "expressions": expressions,
                "seeded": True,
                "created_at": time.time(),
            }
            if not DRY_RUN:
                conn.execute("""
                    INSERT INTO capsules
                        (id, capsule_type, domain, source, title, metadata, created_at)
                    VALUES (?, 'language_expression', 'linguistics', 'seed', ?, ?, ?)
                """, (cap_id, f"expression::{domain}::{subtype}", json.dumps(meta), time.time()))

                anchor_id = f"langeng_expr_{domain}"
                conn.execute("""
                    INSERT OR IGNORE INTO anchors (id, canonical, display_name, state, domain_tags, maturity)
                    VALUES (?, ?, ?, 'emerging', 'linguistics', 1.0)
                """, (anchor_id, f"expression::{domain}", f"expression::{domain}"))

                rel_id = f"rel_{uuid.uuid4().hex[:12]}"
                conn.execute("""
                    INSERT OR IGNORE INTO relations
                        (id, subject_id, predicate, object_id, domain_tags, edge_type, confidence)
                    VALUES (?, ?, 'evokes_expression', ?, 'linguistics', 'functional', 0.95)
                """, (rel_id, anchor_id, cap_id))

            print(f"  [{domain}/{subtype}] — seeded {len(expressions)} expressions")
            total_added += len(expressions)

    if not DRY_RUN:
        conn.commit()
    conn.close()

    print(f"\nTotal seeded: {total_added} expressions across {len(SEEDS)} (domain, subtype) pairs")
    if DRY_RUN:
        print("[DRY RUN] — nothing written")


if __name__ == "__main__":
    main()
