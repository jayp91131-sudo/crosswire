"""
Crosswire Hackathon Presentation Generator (M2.5 Final Content Correction)
Generates a polished, professional 10-slide PowerPoint deck for Crosswire M2.5.
Design: Dark cybersecurity aesthetic (Navy/Charcoal background, Cyan/Sky Blue accents,
crisp typography, structured cards, and visual flowcharts).
"""

import sys
import os
from pptx import Presentation
from pptx.util import Inches, Pt
from pptx.dml.color import RGBColor
from pptx.enum.text import PP_ALIGN
from pptx.enum.shapes import MSO_SHAPE

def create_deck(output_path: str):
    prs = Presentation()
    prs.slide_width = Inches(13.333)
    prs.slide_height = Inches(7.5)
    blank_layout = prs.slide_layouts[6]

    # Color Palette - Professional Cybersecurity Dark Theme
    BG_DARK = RGBColor(11, 15, 25)         # Deep Navy Charcoal #0B0F19
    CARD_BG = RGBColor(20, 27, 45)         # Slate Blue Container #141B2D
    CARD_BORDER = RGBColor(40, 53, 83)     # Subtle outline #283553
    CYAN_ACCENT = RGBColor(6, 182, 212)    # Tech Cyan #06B6D4
    SKY_BLUE = RGBColor(56, 189, 248)      # Highlight Blue #38BDF8
    TEXT_WHITE = RGBColor(255, 255, 255)   # Pure White
    TEXT_MUTED = RGBColor(148, 163, 184)   # Slate Grey #94A3B8
    ROSE_RED = RGBColor(244, 63, 94)       # Vulnerability Alert #F43F5E
    EMERALD_GREEN = RGBColor(16, 185, 129) # Verified Fix #10B981
    AMBER_WARN = RGBColor(245, 158, 11)    # Invariant Alert #F59E0B

    def set_slide_background(slide):
        bg = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, 0, 0, prs.slide_width, prs.slide_height)
        bg.fill.solid()
        bg.fill.fore_color.rgb = BG_DARK
        bg.line.color.rgb = BG_DARK
        return bg

    def add_header(slide, title_text, category_text="CROSSWIRE | TRAVEL DAST"):
        tb_cat = slide.shapes.add_textbox(Inches(0.8), Inches(0.4), Inches(11.7), Inches(0.4))
        p_cat = tb_cat.text_frame.paragraphs[0]
        p_cat.text = category_text.upper()
        p_cat.font.name = "Segoe UI"
        p_cat.font.size = Pt(10)
        p_cat.font.bold = True
        p_cat.font.color.rgb = CYAN_ACCENT

        tb_title = slide.shapes.add_textbox(Inches(0.8), Inches(0.7), Inches(11.7), Inches(0.6))
        p_title = tb_title.text_frame.paragraphs[0]
        p_title.text = title_text
        p_title.font.name = "Segoe UI"
        p_title.font.size = Pt(22)
        p_title.font.bold = True
        p_title.font.color.rgb = TEXT_WHITE

    def add_card(slide, left, top, width, height, title="", border_color=CARD_BORDER, bg_color=CARD_BG):
        card = slide.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, left, top, width, height)
        card.fill.solid()
        card.fill.fore_color.rgb = bg_color
        card.line.color.rgb = border_color
        card.line.width = Pt(1.5)

        if title:
            tb = slide.shapes.add_textbox(left + Inches(0.2), top + Inches(0.15), width - Inches(0.4), Inches(0.4))
            p = tb.text_frame.paragraphs[0]
            p.text = title
            p.font.name = "Segoe UI"
            p.font.size = Pt(13)
            p.font.bold = True
            p.font.color.rgb = SKY_BLUE
        return card

    # ==========================================
    # SLIDE 1: TITLE SLIDE
    # ==========================================
    s1 = prs.slides.add_slide(blank_layout)
    set_slide_background(s1)

    glow = s1.shapes.add_shape(MSO_SHAPE.RECTANGLE, Inches(0.8), Inches(1.2), Inches(0.4), Inches(0.4))
    glow.fill.solid()
    glow.fill.fore_color.rgb = CYAN_ACCENT
    glow.line.fill.background()

    tb_tag = s1.shapes.add_textbox(Inches(1.4), Inches(1.15), Inches(10), Inches(0.5))
    p_tag = tb_tag.text_frame.paragraphs[0]
    p_tag.text = "TRANSACTION-INTEGRITY & BUSINESS-LOGIC DAST"
    p_tag.font.name = "Segoe UI"
    p_tag.font.size = Pt(12)
    p_tag.font.bold = True
    p_tag.font.color.rgb = CYAN_ACCENT

    tb_hero = s1.shapes.add_textbox(Inches(0.8), Inches(1.7), Inches(11.5), Inches(1.8))
    tf_hero = tb_hero.text_frame
    tf_hero.word_wrap = True
    p_hero1 = tf_hero.paragraphs[0]
    p_hero1.text = "CROSSWIRE"
    p_hero1.font.name = "Segoe UI"
    p_hero1.font.size = Pt(56)
    p_hero1.font.bold = True
    p_hero1.font.color.rgb = TEXT_WHITE

    p_hero2 = tf_hero.add_paragraph()
    p_hero2.text = "When travel transactions fail, detect what APIs cannot see."
    p_hero2.font.name = "Segoe UI"
    p_hero2.font.size = Pt(24)
    p_hero2.font.bold = False
    p_hero2.font.color.rgb = SKY_BLUE

    tb_desc = s1.shapes.add_textbox(Inches(0.8), Inches(3.7), Inches(11.5), Inches(1.2))
    tf_desc = tb_desc.text_frame
    tf_desc.word_wrap = True
    p_desc = tf_desc.paragraphs[0]
    p_desc.text = (
        "In B2B travel distribution, HTTP 200 OK does not guarantee business correctness. "
        "Crosswire tests travel transaction scenarios across synthetic aggregators and suppliers—"
        "validating actual database and financial ledger ground truth against formal business invariants."
    )
    p_desc.font.name = "Segoe UI"
    p_desc.font.size = Pt(15)
    p_desc.font.color.rgb = TEXT_MUTED

    add_card(s1, Inches(0.8), Inches(5.2), Inches(3.6), Inches(1.5), "M2.5 READY PROTOTYPE")
    tb_b1 = s1.shapes.add_textbox(Inches(1.0), Inches(5.7), Inches(3.2), Inches(0.8))
    tb_b1.text_frame.word_wrap = True
    tb_b1.text_frame.text = "Interactive local web Control Center demonstrating scenario execution and evidence inspection."
    tb_b1.text_frame.paragraphs[0].font.size = Pt(11)
    tb_b1.text_frame.paragraphs[0].font.color.rgb = TEXT_MUTED

    add_card(s1, Inches(4.8), Inches(5.2), Inches(3.6), Inches(1.5), "266 TEST SUITE PROOF")
    tb_b2 = s1.shapes.add_textbox(Inches(5.0), Inches(5.7), Inches(3.2), Inches(0.8))
    tb_b2.text_frame.word_wrap = True
    tb_b2.text_frame.text = "266 passed tests verified in clean isolated extraction across Control Center, M2.3C, M2.3B, and M2.2."
    tb_b2.text_frame.paragraphs[0].font.size = Pt(11)
    tb_b2.text_frame.paragraphs[0].font.color.rgb = TEXT_MUTED

    add_card(s1, Inches(8.8), Inches(5.2), Inches(3.7), Inches(1.5), "GROUND-TRUTH ORACLE")
    tb_b3 = s1.shapes.add_textbox(Inches(9.0), Inches(5.7), Inches(3.3), Inches(0.8))
    tb_b3.text_frame.word_wrap = True
    tb_b3.text_frame.text = "Direct SQL queries on synthetic supplier records and ledger balances to catch duplicate side-effects."
    tb_b3.text_frame.paragraphs[0].font.size = Pt(11)
    tb_b3.text_frame.paragraphs[0].font.color.rgb = TEXT_MUTED

    # ==========================================
    # SLIDE 2: THE PROBLEM
    # ==========================================
    s2 = prs.slides.add_slide(blank_layout)
    set_slide_background(s2)
    add_header(s2, "The Travel Transaction Gap: HTTP 200 OK vs. Transaction State")

    add_card(s2, Inches(0.8), Inches(1.5), Inches(5.6), Inches(5.4), "THE DISTRIBUTED FAILURE PARADOX")
    tb_p_left = s2.shapes.add_textbox(Inches(1.0), Inches(2.1), Inches(5.2), Inches(4.6))
    tf_pl = tb_p_left.text_frame
    tf_pl.word_wrap = True
    bullets_p = [
        ("Traditional security testing focuses on web protocols and syntax.", "Tools like Burp Suite and ZAP check whether an API endpoint is authenticated, hardened, and injection-free."),
        ("Travel transactions are multi-party sagas.", "A single booking touches an agency, an aggregator, payment gateways, and multiple hotel/airline suppliers."),
        ("Ambiguous timeouts can cause un-reconciled retries.", "When a supplier commits a room but the response drops, the client experiences a timeout. A naive retry creates a second reservation record."),
        ("API responses can mask transaction-state discrepancies.", "Both requests may receive valid HTTP responses, while behind the scenes two reservation records exist for one booking intent.")
    ]
    for i, (head, body) in enumerate(bullets_p):
        p_h = tf_pl.paragraphs[0] if i == 0 else tf_pl.add_paragraph()
        p_h.text = f"• {head}"
        p_h.font.name = "Segoe UI"
        p_h.font.size = Pt(12)
        p_h.font.bold = True
        p_h.font.color.rgb = SKY_BLUE
        p_b = tf_pl.add_paragraph()
        p_b.text = f"  {body}"
        p_b.font.name = "Segoe UI"
        p_b.font.size = Pt(11)
        p_b.font.color.rgb = TEXT_MUTED
        p_b.space_after = Pt(10)

    add_card(s2, Inches(6.8), Inches(1.5), Inches(5.7), Inches(5.4), "ANATOMY OF A TRANSACTION INTEGRITY FAILURE")
    steps = [
        ("1. Agency Requests Booking", "Aggregator sends POST /reservations (15,000 INR)", CYAN_ACCENT),
        ("2. Supplier Commits Room", "Supplier persists reservation 'SUP-RES-01' in DB", EMERALD_GREEN),
        ("3. Response Times Out (> 0.15s)", "Network packet lost; Aggregator raises ReadTimeout", AMBER_WARN),
        ("4. Blind Retry Dispatched", "Aggregator re-issues POST /reservations without key", ROSE_RED),
        ("5. Supplier Commits 2nd Room", "Second reservation 'SUP-RES-02' created for SAME intent!", ROSE_RED),
        ("DEMONSTRATED OUTCOME", "2 supplier reservation records in DB for 1 intent. Potential impact: duplicate billing.", ROSE_RED)
    ]
    y_step = 2.1
    for title_s, desc_s, color_s in steps:
        step_card = s2.shapes.add_shape(MSO_SHAPE.RECTANGLE, Inches(7.1), Inches(y_step), Inches(5.1), Inches(0.68))
        step_card.fill.solid()
        step_card.fill.fore_color.rgb = RGBColor(15, 23, 42)
        step_card.line.color.rgb = color_s
        step_card.line.width = Pt(1.2)

        tb_s = s2.shapes.add_textbox(Inches(7.2), Inches(y_step + 0.05), Inches(4.9), Inches(0.6))
        tf_s = tb_s.text_frame
        tf_s.word_wrap = True
        p1 = tf_s.paragraphs[0]
        p1.text = title_s
        p1.font.bold = True
        p1.font.size = Pt(11)
        p1.font.color.rgb = color_s
        p2 = tf_s.add_paragraph()
        p2.text = desc_s
        p2.font.size = Pt(10)
        p2.font.color.rgb = TEXT_MUTED
        y_step += 0.76

    # ==========================================
    # SLIDE 3: WHY EXISTING TESTING IS NOT ENOUGH
    # ==========================================
    s3 = prs.slides.add_slide(blank_layout)
    set_slide_background(s3)
    add_header(s3, "Why Existing Testing Is Not Enough: Complementary Focus")

    cols = [
        ("GENERAL API DAST\n(Burp Suite / OWASP ZAP)",
         "FOCUSED ON: Protocol & Syntax",
         "• Detects SQLi, XSS, SSRF, IDOR, auth token leaks\n"
         "• Checks HTTP status codes and response headers\n"
         "• Stateless request/response fuzzing\n\n"
         "COMPLEMENTARY ROLE:\n"
         "General-purpose DAST secures protocols and perimeters. Crosswire complements them by focusing on multi-party travel sagas and relational database state.",
         CARD_BORDER, TEXT_MUTED),

        ("FUNCTIONAL / QA TESTING\n(Postman / Playwright)",
         "FOCUSED ON: Happy-Path Logic",
         "• Verifies user journeys work as designed\n"
         "• Uses deterministic, cooperative mocks\n"
         "• Assumes clean network transport\n\n"
         "SPECIFIC LIMITATION:\n"
         "Rarely injects adversarial partial failures (e.g. drop after supplier commit) or webhook replays against backend ground truth.",
         CARD_BORDER, TEXT_MUTED),

        ("CHAOS ENGINEERING\n(Chaos Mesh / Gremlin)",
         "FOCUSED ON: Infrastructure Resiliency",
         "• Injects network latency and kills pods\n"
         "• Verifies system uptime and recovery\n"
         "• Measures SLOs and error rates\n\n"
         "SPECIFIC LIMITATION:\n"
         "Monitors service health and availability rather than domain-specific business invariants like reservation counts.",
         CARD_BORDER, TEXT_MUTED),

        ("CROSSWIRE DAST\n(Transaction Integrity)",
         "FOCUSED ON: Business & Financial State",
         "• Injects travel-specific transaction scenarios\n"
         "• Monitors ground-truth database state in testbed\n"
         "• Mathematical invariant proofs (INV-5.2, 5.3, 5.5)\n"
         "• Automated fixed-build retest verification\n\n"
         "COMPLEMENTARY FOCUS:\n"
         "Evaluates whether business invariants survive simulated distributed failure conditions in a synthetic testbed.",
         CYAN_ACCENT, SKY_BLUE)
    ]

    for i, (title_c, sub_c, body_c, border_c, accent_c) in enumerate(cols):
        left_c = Inches(0.8 + i * 2.95)
        add_card(s3, left_c, Inches(1.5), Inches(2.8), Inches(5.4), border_color=border_c)
        tb_c = s3.shapes.add_textbox(left_c + Inches(0.15), Inches(1.65), Inches(2.5), Inches(5.1))
        tf_c = tb_c.text_frame
        tf_c.word_wrap = True

        p_t = tf_c.paragraphs[0]
        p_t.text = title_c
        p_t.font.bold = True
        p_t.font.size = Pt(11)
        p_t.font.color.rgb = accent_c
        p_t.space_after = Pt(4)

        p_s = tf_c.add_paragraph()
        p_s.text = sub_c
        p_s.font.bold = True
        p_s.font.size = Pt(9)
        p_s.font.color.rgb = CYAN_ACCENT
        p_s.space_after = Pt(12)

        for line in body_c.split("\n"):
            p_b = tf_c.add_paragraph()
            p_b.text = line
            p_b.font.size = Pt(9.5)
            if "COMPLEMENTARY ROLE:" in line or "SPECIFIC LIMITATION:" in line:
                p_b.font.bold = True
                p_b.font.color.rgb = AMBER_WARN
            elif "COMPLEMENTARY FOCUS:" in line:
                p_b.font.bold = True
                p_b.font.color.rgb = EMERALD_GREEN
            else:
                p_b.font.color.rgb = TEXT_MUTED

    # ==========================================
    # SLIDE 4: THE CROSSWIRE SOLUTION
    # ==========================================
    s4 = prs.slides.add_slide(blank_layout)
    set_slide_background(s4)
    add_header(s4, "The Crosswire Solution: Invariant-Driven Security Testing Loop")

    chain = [
        ("1. SYNTHETIC B2B PLATFORM", "Target System", "Runs synthetic travel aggregator, payment gateway, and mock suppliers in an isolated local testbed.", CYAN_ACCENT),
        ("2. ADVERSARIAL INJECTION", "Attack Simulation", "Simulates commit-then-timeout faults, socket drops, and duplicated refund webhooks.", AMBER_WARN),
        ("3. EXECUTION DISPATCH", "DAK / Outbox Engine", "Executes transactions with timeout boundaries and lease coordinator guarantees.", SKY_BLUE),
        ("4. GROUND-TRUTH ORACLE", "Direct SQLite Audit", "Directly queries relational supplier tables, booking engine, and payment ledgers.", CYAN_ACCENT),
        ("5. INVARIANT EVALUATION", "Mathematical Proof", "Asserts INV-5.2 (Uniqueness), INV-5.3 (Idempotency), and INV-5.5 (Refund Boundedness).", EMERALD_GREEN),
        ("6. FINDING & RETEST", "Evidence & Verification", "Generates structured finding (CWE-675) with diffs; verifies fixed build with 1-click retest.", ROSE_RED)
    ]

    for i, (title_ch, sub_ch, body_ch, col_ch) in enumerate(chain):
        x = Inches(0.8 + (i % 3) * 3.95)
        y = Inches(1.5 + (i // 3) * 2.8)
        add_card(s4, x, y, Inches(3.75), Inches(2.5), border_color=col_ch)

        tb_ch = s4.shapes.add_textbox(x + Inches(0.2), y + Inches(0.2), Inches(3.35), Inches(2.1))
        tf_ch = tb_ch.text_frame
        tf_ch.word_wrap = True

        p1 = tf_ch.paragraphs[0]
        p1.text = title_ch
        p1.font.bold = True
        p1.font.size = Pt(12)
        p1.font.color.rgb = col_ch

        p2 = tf_ch.add_paragraph()
        p2.text = sub_ch
        p2.font.bold = True
        p2.font.size = Pt(10)
        p2.font.color.rgb = TEXT_WHITE
        p2.space_after = Pt(8)

        p3 = tf_ch.add_paragraph()
        p3.text = body_ch
        p3.font.size = Pt(10.5)
        p3.font.color.rgb = TEXT_MUTED

    bot_card = s4.shapes.add_shape(MSO_SHAPE.RECTANGLE, Inches(0.8), Inches(6.5), Inches(11.7), Inches(0.6))
    bot_card.fill.solid()
    bot_card.fill.fore_color.rgb = RGBColor(15, 23, 42)
    bot_card.line.color.rgb = CYAN_ACCENT
    bot_card.line.width = Pt(1)
    tb_bot = s4.shapes.add_textbox(Inches(1.0), Inches(6.55), Inches(11.3), Inches(0.5))
    tb_bot.text_frame.text = "Key Focus: Findings are verified directly against database ground truth in the synthetic testbed."
    tb_bot.text_frame.paragraphs[0].font.bold = True
    tb_bot.text_frame.paragraphs[0].font.size = Pt(11)
    tb_bot.text_frame.paragraphs[0].font.color.rgb = CYAN_ACCENT

    # ==========================================
    # SLIDE 5: SYSTEM ARCHITECTURE
    # ==========================================
    s5 = prs.slides.add_slide(blank_layout)
    set_slide_background(s5)
    add_header(s5, "System Architecture: Built Directly on Verified M2.4 Codebase")

    add_card(s5, Inches(0.8), Inches(1.5), Inches(11.7), Inches(1.1), "OPERATOR TIER: WEB CONTROL CENTER (FastAPI + SPA Dashboard)")
    tb_l1 = s5.shapes.add_textbox(Inches(1.0), Inches(1.9), Inches(11.3), Inches(0.6))
    tb_l1.text_frame.word_wrap = True
    tb_l1.text_frame.text = "Interactive Scenario Runner • Chronological Timeline Stream • Ground-Truth Evidence Inspector • 1-Click Retest Runner • Standalone HTML Reports"
    tb_l1.text_frame.paragraphs[0].font.size = Pt(11)
    tb_l1.text_frame.paragraphs[0].font.color.rgb = TEXT_WHITE

    add_card(s5, Inches(0.8), Inches(2.8), Inches(5.7), Inches(2.5), "CROSSWIRE DAST ENGINE CORE")
    tb_l2_l = s5.shapes.add_textbox(Inches(1.0), Inches(3.3), Inches(5.3), Inches(1.9))
    tf_l2_l = tb_l2_l.text_frame
    tf_l2_l.word_wrap = True
    tf_l2_l.text = (
        "• EngineBridge (engine_bridge.py): Orchestrates fault injection and timeline generation\n"
        "• InvariantEngine (engine/invariant_engine.py): Mathematical oracle for invariant evaluation\n"
        "• GroundTruthOracle (ground_truth_oracle.py): Direct SQL queries against target DBs\n"
        "• Outbox Coordinator (m2_3b): Atomic leasing, worker heartbeats, monotonic fencing tokens"
    )
    for p in tf_l2_l.paragraphs:
        p.font.size = Pt(10.5)
        p.font.color.rgb = TEXT_MUTED

    add_card(s5, Inches(6.8), Inches(2.8), Inches(5.7), Inches(2.5), "SYNTHETIC TARGET B2B PLATFORM")
    tb_l2_r = s5.shapes.add_textbox(Inches(7.0), Inches(3.3), Inches(5.3), Inches(1.9))
    tf_l2_r = tb_l2_r.text_frame
    tf_l2_r.word_wrap = True
    tf_l2_r.text = (
        "• Aggregator Service (FastAPI): Booking orchestration, wallet balance holds, idempotency lock\n"
        "• Mock Supplier Adapters: Simulated hotel providers with fault-injection delay hooks\n"
        "• Payment Gateway: Webhook delivery simulation and refund entitlement processing\n"
        "• Relational Storage: aggregator.db, supplier.db, payment.db (SQLite Ground Truth)"
    )
    for p in tf_l2_r.paragraphs:
        p.font.size = Pt(10.5)
        p.font.color.rgb = TEXT_MUTED

    add_card(s5, Inches(0.8), Inches(5.5), Inches(11.7), Inches(1.6), "CANONICAL INVARIANT FRAMEWORK (MASTER_PLAN_V2)")
    invs = [
        ("INV-5.2: Booking Intent Uniqueness", "COUNT(supplier_reservations) <= 1 per intent ID", "CWE-675 (Multiple Operations)"),
        ("INV-5.3: Webhook Idempotency", "Repeated webhook deliveries create exactly 1 ledger credit", "CWE-799 (Improper Interaction Freq)"),
        ("INV-5.4: Voucher Entitlement", "Voucher issuance requires confirmed booking & satisfied obligation", "Voucher Entitlement Gate"),
        ("INV-5.5: Refund Boundedness", "SUM(completed_refunds) <= eligible refundable entitlement", "Financial Over-Disbursement Gate"),
        ("INV-5.7: Financial Ledger Consistency (deferred)", "Double-entry balanced debits & credits in accounting ledger", "Double-Entry Balance Gate"),
        ("INV-M2.3B: Stay Coverage & Fencing", "Monotonic fencing tokens prevent split-brain worker collisions", "CWE-841 (Workflow Enforcement)")
    ]
    for i, (name_i, rule_i, cwe_i) in enumerate(invs):
        x_i = Inches(1.0 + (i % 2) * 5.8)
        y_i = Inches(5.85 + (i // 2) * 0.38)
        tb_i = s5.shapes.add_textbox(x_i, y_i, Inches(5.6), Inches(0.35))
        tf_i = tb_i.text_frame
        p_i = tf_i.paragraphs[0]
        p_i.text = f"{name_i}: {rule_i}"
        p_i.font.size = Pt(9)
        p_i.font.color.rgb = SKY_BLUE

    # ==========================================
    # SLIDE 6: LIVE SCENARIO 1 - COMMIT-THEN-TIMEOUT
    # ==========================================
    s6 = prs.slides.add_slide(blank_layout)
    set_slide_background(s6)
    add_header(s6, "Live Scenario 1: Ambiguous Supplier Commit-Then-Timeout (SCN-TIMEOUT-001)")

    add_card(s6, Inches(0.8), Inches(1.5), Inches(5.7), Inches(5.4), "VULNERABLE MODE: BLIND RETRY", border_color=ROSE_RED)
    tb_v = s6.shapes.add_textbox(Inches(1.0), Inches(2.0), Inches(5.3), Inches(4.7))
    tf_v = tb_v.text_frame
    tf_v.word_wrap = True
    v_steps = [
        ("1. Intent Created", "Agency creates booking intent for Deluxe Suite (15,000 INR)."),
        ("2. Supplier Commits", "Supplier persists reservation 'SUP-RES-01' in SQLite database."),
        ("3. Timeout Injected", "Supplier delays HTTP response by 0.30s (> 0.15s client timeout). Client raises ReadTimeout."),
        ("4. Blind Retry Sent", "Aggregator naively re-issues POST /reservations without idempotency contract."),
        ("5. Duplicate Reservation Created", "Supplier commits second reservation 'SUP-RES-02' for SAME intent."),
        ("6. Invariant Violated: INV-5.2", "Oracle counts 2 distinct supplier records in SQLite for 1 intent."),
        ("DEMONSTRATED OUTCOME", "CW-SEC-M2-001 (CWE-675). Potential consequence: customer double-charge and locked inventory.")
    ]
    for i, (st, desc) in enumerate(v_steps):
        p1 = tf_v.paragraphs[0] if i == 0 else tf_v.add_paragraph()
        p1.text = st
        p1.font.bold = True
        p1.font.size = Pt(11)
        p1.font.color.rgb = ROSE_RED if "OUTCOME" in st or "Duplicate" in st or "Violated" in st else TEXT_WHITE
        p2 = tf_v.add_paragraph()
        p2.text = desc
        p2.font.size = Pt(10)
        p2.font.color.rgb = TEXT_MUTED
        p2.space_after = Pt(4)

    add_card(s6, Inches(6.8), Inches(1.5), Inches(5.7), Inches(5.4), "FIXED MODE: IDEMPOTENT RECONCILIATION", border_color=EMERALD_GREEN)
    tb_f = s6.shapes.add_textbox(Inches(7.0), Inches(2.0), Inches(5.3), Inches(4.7))
    tf_f = tb_f.text_frame
    tf_f.word_wrap = True
    f_steps = [
        ("1. Intent Created", "Agency creates booking intent with deterministic idempotency key."),
        ("2. Supplier Commits", "Supplier persists reservation 'SUP-RES-01' bound to idempotency key."),
        ("3. Timeout Occurs", "Network timeout simulated exactly as before (> 0.15s delay)."),
        ("4. Idempotent Probe Dispatched", "Aggregator dispatches reconciliation lookup using booking intent key."),
        ("5. Existing Reservation Reconciled", "Supplier returns existing confirmation 'SUP-RES-01'—zero duplicates created in DB."),
        ("6. Invariant Verified: INV-5.2", "Oracle confirms strictly 1 supplier reservation in SQLite."),
        ("RETEST RESULT", "RETEST-TIMEOUT-PASSED | Status: VERIFIED FIXED | Zero Duplicate Reservations")
    ]
    for i, (st, desc) in enumerate(f_steps):
        p1 = tf_f.paragraphs[0] if i == 0 else tf_f.add_paragraph()
        p1.text = st
        p1.font.bold = True
        p1.font.size = Pt(11)
        p1.font.color.rgb = EMERALD_GREEN if "RETEST" in st or "Verified" in st or "Reconciled" in st else TEXT_WHITE
        p2 = tf_f.add_paragraph()
        p2.text = desc
        p2.font.size = Pt(10)
        p2.font.color.rgb = TEXT_MUTED
        p2.space_after = Pt(4)

    # ==========================================
    # SLIDE 7: LIVE SCENARIO 2 - DUPLICATE REFUND WEBHOOK
    # ==========================================
    s7 = prs.slides.add_slide(blank_layout)
    set_slide_background(s7)
    add_header(s7, "Live Scenario 2: Duplicate Refund Webhook Replay (SCN-WEBHOOK-001)")

    add_card(s7, Inches(0.8), Inches(1.5), Inches(5.7), Inches(5.4), "VULNERABLE WEBHOOK REPLAY (CWE-799)", border_color=ROSE_RED)
    tb_w_v = s7.shapes.add_textbox(Inches(1.0), Inches(2.0), Inches(5.3), Inches(4.7))
    tf_w_v = tb_w_v.text_frame
    tf_w_v.word_wrap = True
    wv_points = [
        ("Event Ingestion", "Payment gateway delivers a 5,000 INR refund webhook 3 consecutive times."),
        ("Vulnerable Processing", "Aggregator processes each delivery independently without deduplication lock."),
        ("Financial Mutation", "Three separate 5,000 INR refund credits are posted to agency wallet (Total: 15,000 INR)."),
        ("Entitlement Breach", "Booking refundable entitlement was strictly 12,000 INR. System disbursed 15,000 INR in DB!"),
        ("Invariants Violated", "INV-5.3 (Webhook Idempotency) & INV-5.5 (Refund Boundedness) BOTH FAILED."),
        ("DEMONSTRATED OUTCOME", "3,000 INR excess credit created in synthetic ledger. Potential impact: cash over-disbursement."),
        ("FINDING GENERATED", "CW-SEC-M2-002 | Severity: CRITICAL | CWE-799 / CWE-675")
    ]
    for i, (head, body) in enumerate(wv_points):
        p1 = tf_w_v.paragraphs[0] if i == 0 else tf_w_v.add_paragraph()
        p1.text = head
        p1.font.bold = True
        p1.font.size = Pt(11)
        p1.font.color.rgb = ROSE_RED if "FINDING" in head or "Breach" in head or "Violated" in head or "OUTCOME" in head else TEXT_WHITE
        p2 = tf_w_v.add_paragraph()
        p2.text = body
        p2.font.size = Pt(10)
        p2.font.color.rgb = TEXT_MUTED
        p2.space_after = Pt(4)

    add_card(s7, Inches(6.8), Inches(1.5), Inches(5.7), Inches(5.4), "FIXED ATOMIC DEDUPLICATION", border_color=EMERALD_GREEN)
    tb_w_f = s7.shapes.add_textbox(Inches(7.0), Inches(2.0), Inches(5.3), Inches(4.7))
    tf_w_f = tb_w_f.text_frame
    tf_w_f.word_wrap = True
    wf_points = [
        ("Event Ingestion", "Same 3 refund webhook deliveries delivered to fixed aggregator endpoint."),
        ("Deduplication Store", "Aggregator checks atomic event deduplication table within SQLite transaction."),
        ("Delivery 1 Processed", "First delivery posts 5,000 INR refund and records event ID."),
        ("Deliveries 2 & 3 Suppressed", "Duplicate events match recorded ID; acknowledged with HTTP 200 without ledger mutation."),
        ("Invariants Preserved", "INV-5.3 PASSED (1 ledger effect) & INV-5.5 PASSED (5,000 INR <= 12,000 INR entitlement)."),
        ("Zero Excess Mutation", "Synthetic ledger balance matches expected entitlement exactly (0 INR excess)."),
        ("RETEST RESULT", "RETEST-WEBHOOK-PASSED | Status: VERIFIED FIXED | Pass Proof Attached")
    ]
    for i, (head, body) in enumerate(wf_points):
        p1 = tf_w_f.paragraphs[0] if i == 0 else tf_w_f.add_paragraph()
        p1.text = head
        p1.font.bold = True
        p1.font.size = Pt(11)
        p1.font.color.rgb = EMERALD_GREEN if "RETEST" in head or "Preserved" in head or "Zero" in head or "Suppressed" in head else TEXT_WHITE
        p2 = tf_w_f.add_paragraph()
        p2.text = body
        p2.font.size = Pt(10)
        p2.font.color.rgb = TEXT_MUTED
        p2.space_after = Pt(4)

    # ==========================================
    # SLIDE 8: THE CONTROL CENTER
    # ==========================================
    s8 = prs.slides.add_slide(blank_layout)
    set_slide_background(s8)
    add_header(s8, "The Crosswire Control Center: Local Operator Cockpit")

    f_cards = [
        ("EXECUTIVE KPI DASHBOARD",
         "• Live execution counters (Total, Confirmed, Passed)\n"
         "• Severity breakdown (Critical, High, Medium, Low)\n"
         "• Real-time Invariant Status Monitor (INV-5.2, 5.3, 5.5)\n"
         "• Reverse-chronological activity feed with ms latency",
         CYAN_ACCENT),

        ("INTERACTIVE SCENARIO RUNNER",
         "• 1-click execution for all 3 approved travel scenarios\n"
         "• Mode switch: Run Vulnerable vs Run Fixed\n"
         "• Real-time progress console during adversarial simulation\n"
         "• Self-contained execution on synthetic local testbed",
         SKY_BLUE),

        ("CHRONOLOGICAL TIMELINE STREAM",
         "• Granular audit from intent creation to final assertion\n"
         "• Displays exact delay seconds and client ReadTimeout faults\n"
         "• Color-coded statuses: INFO, WARNING, CRITICAL, SUCCESS\n"
         "• Direct traceability for every security decision",
         EMERALD_GREEN),

        ("GROUND-TRUTH FINDINGS & REPORTS",
         "• Structured finding cards with CWE tags and remediation diffs\n"
         "• Expandable JSON evidence with raw SQLite row IDs\n"
         "• 1-Click Retest verification attached directly to findings\n"
         "• Standalone HTML executive report export (/api/reports/html)",
         AMBER_WARN)
    ]

    for i, (title_fc, body_fc, color_fc) in enumerate(f_cards):
        x = Inches(0.8 + (i % 2) * 5.95)
        y = Inches(1.5 + (i // 2) * 2.8)
        add_card(s8, x, y, Inches(5.75), Inches(2.6), title_fc, border_color=color_fc)

        tb_fc = s8.shapes.add_textbox(x + Inches(0.2), y + Inches(0.6), Inches(5.35), Inches(1.9))
        tf_fc = tb_fc.text_frame
        tf_fc.word_wrap = True
        for line in body_fc.split("\n"):
            p = tf_fc.add_paragraph() if tf_fc.paragraphs[0].text else tf_fc.paragraphs[0]
            p.text = line
            p.font.size = Pt(11)
            p.font.color.rgb = TEXT_WHITE if line.startswith("• Live") or line.startswith("• 1-") or line.startswith("• Granular") or line.startswith("• Structured") else TEXT_MUTED

    bot_s8 = s8.shapes.add_shape(MSO_SHAPE.RECTANGLE, Inches(0.8), Inches(6.5), Inches(11.7), Inches(0.6))
    bot_s8.fill.solid()
    bot_s8.fill.fore_color.rgb = RGBColor(15, 23, 42)
    bot_s8.line.color.rgb = EMERALD_GREEN
    bot_s8.line.width = Pt(1)
    tb_bot_s8 = s8.shapes.add_textbox(Inches(1.0), Inches(6.55), Inches(11.3), Inches(0.5))
    tb_bot_s8.text_frame.text = "Local Execution: Launches with 'python run_control_center.py' • Serves on http://127.0.0.1:8000 • Synthetic Local Testbed"
    tb_bot_s8.text_frame.paragraphs[0].font.bold = True
    tb_bot_s8.text_frame.paragraphs[0].font.size = Pt(11)
    tb_bot_s8.text_frame.paragraphs[0].font.color.rgb = EMERALD_GREEN

    # ==========================================
    # SLIDE 9: DIFFERENTIATION & POSITIONING
    # ==========================================
    s9 = prs.slides.add_slide(blank_layout)
    set_slide_background(s9)
    add_header(s9, "Product Differentiation & Strategic Positioning")

    table_shape = s9.shapes.add_table(5, 5, Inches(0.8), Inches(1.5), Inches(11.7), Inches(4.5))
    table = table_shape.table
    table.columns[0].width = Inches(2.7)
    table.columns[1].width = Inches(2.25)
    table.columns[2].width = Inches(2.25)
    table.columns[3].width = Inches(2.25)
    table.columns[4].width = Inches(2.25)

    headers_table = ["CAPABILITY / CRITERIA", "GENERAL API DAST\n(ZAP / Burp)", "FUNCTIONAL QA\n(Postman)", "CHAOS TOOLS\n(Chaos Mesh)", "CROSSWIRE DAST\n(Prototype)"]
    for col_idx, h_text in enumerate(headers_table):
        cell = table.cell(0, col_idx)
        cell.fill.solid()
        cell.fill.fore_color.rgb = RGBColor(15, 23, 42) if col_idx < 4 else RGBColor(6, 40, 60)
        p = cell.text_frame.paragraphs[0]
        p.text = h_text
        p.font.bold = True
        p.font.size = Pt(10)
        p.font.color.rgb = SKY_BLUE if col_idx < 4 else CYAN_ACCENT

    rows_data = [
        ("Target Vulnerability Class", "Syntax, Auth, Injections\n(OWASP Top 10)", "Functional workflow breakage", "System crashing, SLO latency", "Business logic & transaction integrity (CWE-675, CWE-799)"),
        ("Adversarial Test Driver", "Protocol parameter fuzzing", "Deterministic happy-path", "Kernel/network fault injection", "Travel transaction sagas\n(Commit-timeout, webhook replay)"),
        ("Verification Mechanism", "HTTP status & body analysis", "HTTP assertions & status", "Uptime & monitoring metrics", "Database invariant assertions\non synthetic ground truth"),
        ("Remediation Verification", "Manual re-scan", "Re-run test script", "Observe metric recovery", "Automated 1-click retest\nwith invariant pass proof")
    ]

    for row_idx, r_data in enumerate(rows_data, start=1):
        for col_idx, text_val in enumerate(r_data):
            cell = table.cell(row_idx, col_idx)
            cell.fill.solid()
            cell.fill.fore_color.rgb = CARD_BG if col_idx < 4 else RGBColor(16, 32, 50)
            p = cell.text_frame.paragraphs[0]
            p.text = text_val
            p.font.size = Pt(9.5)
            if col_idx == 0:
                p.font.bold = True
                p.font.color.rgb = TEXT_WHITE
            elif col_idx == 4:
                p.font.bold = True
                p.font.color.rgb = CYAN_ACCENT
            else:
                p.font.color.rgb = TEXT_MUTED

    add_card(s9, Inches(0.8), Inches(6.15), Inches(11.7), Inches(0.9), border_color=CYAN_ACCENT)
    tb_diff = s9.shapes.add_textbox(Inches(1.0), Inches(6.25), Inches(11.3), Inches(0.7))
    tf_diff = tb_diff.text_frame
    tf_diff.word_wrap = True
    p_d1 = tf_diff.paragraphs[0]
    p_d1.text = "The Complementary Role: Crosswire does not replace general DAST or chaos tools. It provides a specialized focus on travel transaction scenarios where HTTP endpoints succeed while database transaction state becomes duplicated or inconsistent."
    p_d1.font.bold = True
    p_d1.font.size = Pt(10.5)
    p_d1.font.color.rgb = TEXT_WHITE

    # ==========================================
    # SLIDE 10: ROADMAP, BOUNDARIES & CLOSING
    # ==========================================
    s10 = prs.slides.add_slide(blank_layout)
    set_slide_background(s10)
    add_header(s10, "Prototype Scope, Roadmap & Final Conclusion")

    add_card(s10, Inches(0.8), Inches(1.5), Inches(5.7), Inches(4.3), "CURRENT PROTOTYPE BOUNDARIES")
    tb_lim = s10.shapes.add_textbox(Inches(1.0), Inches(2.0), Inches(5.3), Inches(3.6))
    tf_lim = tb_lim.text_frame
    tf_lim.word_wrap = True
    lims = [
        ("Synthetic Local Testbed", "Built and tested on a single-host synthetic travel platform; does not establish production-scale distributed guarantees or real supplier integrations."),
        ("Relational SQLite Storage", "Evaluates ground truth against local SQLite databases; does not test distributed network partitions."),
        ("In-Memory Session State", "Session execution records are held in-memory in StateStore; target assertions evaluate against SQLite databases."),
        ("Single-Hotel Scope", "Single-hotel, single-currency prototype scope."),
        ("Deferred Invariant", "Invariant 5.7 (Financial Ledger Double-Entry Consistency) remains deferred in current prototype.")
    ]
    for i, (h, b) in enumerate(lims):
        p1 = tf_lim.paragraphs[0] if i == 0 else tf_lim.add_paragraph()
        p1.text = f"• {h}: "
        p1.font.bold = True
        p1.font.size = Pt(10)
        p1.font.color.rgb = AMBER_WARN
        p1.text += b
        p1.font.bold = False
        p1.font.color.rgb = TEXT_MUTED
        p1.space_after = Pt(4)

    add_card(s10, Inches(6.8), Inches(1.5), Inches(5.7), Inches(4.3), "TECHNICAL ROADMAP")
    tb_road = s10.shapes.add_textbox(Inches(7.0), Inches(2.0), Inches(5.3), Inches(3.6))
    tf_road = tb_road.text_frame
    tf_road.word_wrap = True
    roads = [
        ("Phase 1: Real Sandbox Connectors", "Pre-built adapters for Amadeus, Sabre, and Hotelbeds certification testbeds."),
        ("Phase 2: Invariant 5.7 Integration", "Implement Invariant 5.7 (Financial Ledger Double-Entry Consistency) mathematical ledger checks."),
        ("Phase 3: CI/CD Pipeline Action", "Headless CLI runner ('crosswire-cli') for automated GitHub Actions / GitLab merge gates."),
        ("Phase 4: Multi-Supplier Split Engine", "Full execution of distributed split-stay multi-supplier outbox coordination.")
    ]
    for i, (h, b) in enumerate(roads):
        p1 = tf_road.paragraphs[0] if i == 0 else tf_road.add_paragraph()
        p1.text = f"• {h}: "
        p1.font.bold = True
        p1.font.size = Pt(10)
        p1.font.color.rgb = CYAN_ACCENT
        p1.text += b
        p1.font.bold = False
        p1.font.color.rgb = TEXT_MUTED
        p1.space_after = Pt(6)

    close_card = s10.shapes.add_shape(MSO_SHAPE.RECTANGLE, Inches(0.8), Inches(6.0), Inches(11.7), Inches(1.0))
    close_card.fill.solid()
    close_card.fill.fore_color.rgb = RGBColor(15, 23, 42)
    close_card.line.color.rgb = SKY_BLUE
    close_card.line.width = Pt(1.5)

    tb_close = s10.shapes.add_textbox(Inches(1.0), Inches(6.15), Inches(11.3), Inches(0.7))
    tf_close = tb_close.text_frame
    p_c1 = tf_close.paragraphs[0]
    p_c1.text = "CROSSWIRE"
    p_c1.font.bold = True
    p_c1.font.size = Pt(14)
    p_c1.font.color.rgb = CYAN_ACCENT
    p_c1.alignment = PP_ALIGN.CENTER

    p_c2 = tf_close.add_paragraph()
    p_c2.text = "Crosswire doesn't just ask whether the API responded. It asks whether the transaction remained correct."
    p_c2.font.bold = True
    p_c2.font.size = Pt(13)
    p_c2.font.color.rgb = TEXT_WHITE
    p_c2.alignment = PP_ALIGN.CENTER

    prs.save(output_path)
    print(f"Presentation saved successfully to {output_path}")

if __name__ == "__main__":
    out_file = sys.argv[1] if len(sys.argv) > 1 else "Crosswire_Hackathon_Presentation.pptx"
    create_deck(out_file)
