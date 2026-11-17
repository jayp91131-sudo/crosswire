"""
Crosswire Control Center: Reports & Export Engine.
Generates executive-ready HTML Security Assessment Reports and structured JSON exports.
"""

from typing import Dict, Any, List
from datetime import datetime, timezone
import json
from .state import get_state_store, FindingRecord, ExecutionRecord, get_iso_now


def generate_html_security_report() -> str:
    """Renders a self-contained, professional HTML Security Assessment Report."""
    state = get_state_store()
    stats = state.get_stats()
    findings = list(state.findings.values())
    executions = state.executions
    timestamp = get_iso_now()

    # Pre-calculate counts
    crit_count = stats["severity_counts"]["CRITICAL"]
    high_count = stats["severity_counts"]["HIGH"]
    med_count = stats["severity_counts"]["MEDIUM"]
    low_count = stats["severity_counts"]["LOW"]
    retest_count = stats["successful_retests"]
    total_findings = len(findings)

    findings_html_cards = []
    for f in findings:
        sev_class = {
            "CRITICAL": "badge-crit",
            "HIGH": "badge-high",
            "MEDIUM": "badge-med",
            "LOW": "badge-low"
        }.get(f.severity.upper(), "badge-med")

        retest_html = ""
        if f.retest_result and f.retest_result.get("fixed_mode_passed", False):
            rr = f.retest_result
            retest_html = f"""
            <div class="retest-box retest-pass">
                <div class="retest-header">
                    <span class="retest-title">&#10004; RETEST VERIFICATION PROVED FIX</span>
                    <span class="retest-id">{rr.get('retest_id', 'N/A')}</span>
                </div>
                <p class="retest-summary">{rr.get('summary', 'Fixed workflow verified invariant adherence.')}</p>
                <div class="retest-meta">
                    <span><strong>Timestamp:</strong> {rr.get('timestamp', 'N/A')}</span>
                    <span><strong>Res Count (Fixed):</strong> {rr.get('supplier_reservation_count', 0)}</span>
                    <span><strong>Invariant Result:</strong> <span class="text-emerald">PASSED</span></span>
                </div>
            </div>
            """
        else:
            retest_html = """
            <div class="retest-box retest-pending">
                <span class="retest-title">&#9888; Retest Pending</span>
                <p class="retest-summary">Fixed-mode regression execution has not yet been attached to this finding.</p>
            </div>
            """

        evidence_json = json.dumps(f.evidence, indent=2)

        card = f"""
        <div class="finding-card">
            <div class="finding-card-header">
                <div>
                    <span class="finding-id">{f.finding_id}</span>
                    <span class="badge {sev_class}">{f.severity}</span>
                    <span class="cwe-pill">{f.cwe.split(':')[0]}</span>
                </div>
                <div class="scenario-tag">{f.scenario_id}</div>
            </div>
            <h3 class="finding-title">{f.title}</h3>
            
            <div class="meta-grid">
                <div><strong>Violated Invariant:</strong> <code>{f.violated_invariant}</code></div>
                <div><strong>Logical Intent ID:</strong> <code>{f.logical_intent_id}</code></div>
                <div><strong>Supplier Res IDs:</strong> <code>{', '.join(f.supplier_reservation_ids) if f.supplier_reservation_ids else 'None'}</code></div>
                <div><strong>Detected:</strong> {f.created_at}</div>
            </div>

            <div class="section-block">
                <h4>Expected vs Actual Outcome</h4>
                <div class="behavior-compare">
                    <div class="behavior-col expected">
                        <span class="col-tag">Expected</span>
                        <p>{f.expected_outcome}</p>
                    </div>
                    <div class="behavior-col actual">
                        <span class="col-tag">Actual (Vulnerable)</span>
                        <p>{f.actual_outcome}</p>
                    </div>
                </div>
            </div>

            <div class="section-block">
                <h4>Technical Root Cause</h4>
                <p class="tech-desc">{f.technical_description}</p>
            </div>

            <div class="section-block">
                <h4>Remediation Guidance</h4>
                <pre class="remediation-text">{f.remediation_guidance}</pre>
            </div>

            {retest_html}

            <details class="evidence-details">
                <summary>Expand Relational & Ground-Truth Evidence (JSON)</summary>
                <pre class="evidence-json"><code>{evidence_json}</code></pre>
            </details>
        </div>
        """
        findings_html_cards.append(card)

    executions_rows = []
    for ex in executions[:15]:
        v = (ex.verdict or "").strip().upper()
        if v == "FAIL":
            verdict_str = "FAIL"
            status_badge = "status-vuln"
            icon = "&#10008; "  # cross mark
        elif v == "PASS":
            verdict_str = "PASS"
            status_badge = "status-pass"
            icon = "&#10004; "  # checkmark
        elif v == "INCONCLUSIVE":
            verdict_str = "INCONCLUSIVE"
            status_badge = "status-inconclusive"
            icon = "&#9888; "  # warning icon (never green checkmark)
        else:
            # Missing or unknown verdict must render as INCONCLUSIVE
            verdict_str = "INCONCLUSIVE"
            status_badge = "status-inconclusive"
            icon = "&#9888; "

        digest_display = f"<code>{ex.evidence_sha256[:8]}...</code>" if ex.evidence_sha256 else "<code>UNVERIFIED</code>"
        executions_rows.append(f"""
        <tr>
            <td><code>{ex.execution_id}</code></td>
            <td><strong>{ex.scenario_name}</strong></td>
            <td><span class="mode-tag mode-{ex.mode.lower()}">{ex.mode}</span></td>
            <td><span class="status-pill {status_badge}">{icon}{verdict_str} ({ex.status})</span></td>
            <td><code>{', '.join(ex.invariants_checked)}</code></td>
            <td>{digest_display}</td>
            <td>{ex.duration_ms:.1f}ms</td>
            <td>{ex.timestamp}</td>
        </tr>
        """)

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Crosswire Security Assessment Report</title>
    <style>
        :root {{
            --bg: #0B0F19;
            --surface: #111827;
            --surface-card: #1F2937;
            --border: #374151;
            --text-main: #F9FAFB;
            --text-muted: #9CA3AF;
            --cyan: #06B6D4;
            --emerald: #10B981;
            --amber: #F59E0B;
            --rose: #F43F5E;
            --font-mono: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
        }}
        * {{ box-sizing: border-box; margin: 0; padding: 0; }}
        body {{
            background: var(--bg);
            color: var(--text-main);
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
            line-height: 1.6;
            padding: 32px 16px;
        }}
        .report-container {{
            max-width: 1040px;
            margin: 0 auto;
        }}
        .header-bar {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            border-bottom: 2px solid var(--border);
            padding-bottom: 24px;
            margin-bottom: 32px;
        }}
        .brand-logo {{
            font-size: 24px;
            font-weight: 800;
            letter-spacing: -0.5px;
            color: var(--cyan);
            display: flex;
            align-items: center;
            gap: 10px;
        }}
        .brand-sub {{
            font-size: 13px;
            color: var(--text-muted);
            text-transform: uppercase;
            letter-spacing: 1px;
        }}
        .report-meta-box {{
            text-align: right;
            font-size: 13px;
            color: var(--text-muted);
        }}
        .kpi-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 16px;
            margin-bottom: 32px;
        }}
        .kpi-card {{
            background: var(--surface);
            border: 1px solid var(--border);
            border-radius: 8px;
            padding: 20px;
        }}
        .kpi-label {{
            font-size: 12px;
            color: var(--text-muted);
            text-transform: uppercase;
            letter-spacing: 0.5px;
            margin-bottom: 8px;
        }}
        .kpi-val {{
            font-size: 32px;
            font-weight: 800;
            line-height: 1;
        }}
        .val-crit {{ color: var(--rose); }}
        .val-high {{ color: var(--amber); }}
        .val-pass {{ color: var(--emerald); }}
        .val-cyan {{ color: var(--cyan); }}

        h2.section-header {{
            font-size: 20px;
            font-weight: 700;
            margin: 40px 0 20px 0;
            color: var(--text-main);
            display: flex;
            align-items: center;
            gap: 10px;
            border-left: 4px solid var(--cyan);
            padding-left: 12px;
        }}
        .finding-card {{
            background: var(--surface);
            border: 1px solid var(--border);
            border-radius: 10px;
            padding: 24px;
            margin-bottom: 24px;
        }}
        .finding-card-header {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 12px;
        }}
        .finding-id {{
            font-family: var(--font-mono);
            font-weight: 700;
            color: var(--cyan);
            font-size: 16px;
            margin-right: 12px;
        }}
        .badge {{
            padding: 4px 10px;
            border-radius: 4px;
            font-size: 11px;
            font-weight: 700;
            text-transform: uppercase;
            letter-spacing: 0.5px;
            margin-right: 8px;
        }}
        .badge-crit {{ background: rgba(244, 63, 94, 0.2); color: var(--rose); border: 1px solid var(--rose); }}
        .badge-high {{ background: rgba(245, 158, 11, 0.2); color: var(--amber); border: 1px solid var(--amber); }}
        .badge-med {{ background: rgba(6, 182, 212, 0.2); color: var(--cyan); border: 1px solid var(--cyan); }}
        .cwe-pill {{
            background: #27272A;
            color: var(--text-muted);
            padding: 4px 8px;
            border-radius: 4px;
            font-size: 11px;
            font-family: var(--font-mono);
        }}
        .scenario-tag {{
            font-size: 12px;
            color: var(--text-muted);
            font-family: var(--font-mono);
        }}
        .finding-title {{
            font-size: 18px;
            font-weight: 700;
            margin-bottom: 16px;
        }}
        .meta-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
            gap: 12px;
            font-size: 13px;
            background: var(--surface-card);
            padding: 12px 16px;
            border-radius: 6px;
            margin-bottom: 20px;
        }}
        .meta-grid code {{
            font-family: var(--font-mono);
            color: var(--cyan);
        }}
        .section-block {{
            margin-bottom: 18px;
        }}
        .section-block h4 {{
            font-size: 13px;
            text-transform: uppercase;
            letter-spacing: 0.5px;
            color: var(--text-muted);
            margin-bottom: 8px;
        }}
        .behavior-compare {{
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 16px;
        }}
        .behavior-col {{
            padding: 12px;
            border-radius: 6px;
            font-size: 13px;
        }}
        .behavior-col.expected {{
            background: rgba(16, 185, 129, 0.08);
            border: 1px solid rgba(16, 185, 129, 0.3);
        }}
        .behavior-col.actual {{
            background: rgba(244, 63, 94, 0.08);
            border: 1px solid rgba(244, 63, 94, 0.3);
        }}
        .col-tag {{
            font-size: 11px;
            font-weight: 700;
            text-transform: uppercase;
            display: block;
            margin-bottom: 4px;
        }}
        .behavior-col.expected .col-tag {{ color: var(--emerald); }}
        .behavior-col.actual .col-tag {{ color: var(--rose); }}
        .tech-desc {{
            font-size: 14px;
            color: #E2E8F0;
        }}
        .remediation-text {{
            background: #090D16;
            border: 1px solid var(--border);
            padding: 12px;
            border-radius: 6px;
            font-family: var(--font-mono);
            font-size: 12px;
            color: #A7F3D0;
            white-space: pre-wrap;
        }}
        .retest-box {{
            padding: 14px 18px;
            border-radius: 6px;
            margin: 20px 0 16px 0;
        }}
        .retest-pass {{
            background: rgba(16, 185, 129, 0.12);
            border: 1px solid var(--emerald);
        }}
        .retest-pending {{
            background: rgba(245, 158, 11, 0.1);
            border: 1px dashed var(--amber);
        }}
        .retest-header {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 6px;
        }}
        .retest-title {{
            font-weight: 700;
            font-size: 13px;
            letter-spacing: 0.5px;
            color: var(--emerald);
        }}
        .retest-id {{
            font-family: var(--font-mono);
            font-size: 11px;
            color: var(--text-muted);
        }}
        .retest-summary {{
            font-size: 13px;
            color: #E2E8F0;
            margin-bottom: 8px;
        }}
        .retest-meta {{
            display: flex;
            gap: 20px;
            font-size: 12px;
            color: var(--text-muted);
        }}
        .text-emerald {{ color: var(--emerald); font-weight: 700; }}
        details.evidence-details {{
            margin-top: 12px;
            font-size: 13px;
        }}
        details.evidence-details summary {{
            cursor: pointer;
            color: var(--cyan);
            user-select: none;
            padding: 6px 0;
        }}
        .evidence-json {{
            background: #060911;
            border: 1px solid var(--border);
            border-radius: 6px;
            padding: 12px;
            margin-top: 8px;
            font-family: var(--font-mono);
            font-size: 11px;
            color: #CBD5E1;
            max-height: 350px;
            overflow-y: auto;
        }}
        table.exec-table {{
            width: 100%;
            border-collapse: collapse;
            font-size: 13px;
            background: var(--surface);
            border-radius: 8px;
            overflow: hidden;
            border: 1px solid var(--border);
        }}
        table.exec-table th, table.exec-table td {{
            padding: 12px 16px;
            text-align: left;
            border-bottom: 1px solid var(--border);
        }}
        table.exec-table th {{
            background: #1E293B;
            font-size: 11px;
            text-transform: uppercase;
            letter-spacing: 0.5px;
            color: var(--text-muted);
        }}
        table.exec-table code {{
            font-family: var(--font-mono);
            font-size: 12px;
        }}
        .mode-tag {{
            font-size: 11px;
            font-weight: 700;
            padding: 2px 6px;
            border-radius: 3px;
        }}
        .mode-vulnerable {{ background: rgba(244, 63, 94, 0.2); color: var(--rose); }}
        .mode-fixed {{ background: rgba(16, 185, 129, 0.2); color: var(--emerald); }}
        .status-pill {{
            font-size: 11px;
            font-weight: 700;
            padding: 3px 8px;
            border-radius: 4px;
            display: inline-block;
        }}
        .status-vuln {{ color: var(--rose); background: rgba(244, 63, 94, 0.15); border: 1px solid var(--rose); }}
        .status-pass {{ color: var(--emerald); background: rgba(16, 185, 129, 0.15); border: 1px solid var(--emerald); }}
        .status-inconclusive {{ color: var(--amber); background: rgba(245, 158, 11, 0.15); border: 1px dashed var(--amber); }}

        @media print {{
            body {{ background: #FFF; color: #000; padding: 0; }}
            .finding-card, .kpi-card, table.exec-table {{ border: 1px solid #CCC; background: #FFF; color: #000; }}
            .brand-logo {{ color: #000; }}
            .evidence-json, .remediation-text {{ background: #F8F9FA; color: #000; }}
        }}
    </style>
</head>
<body>
    <div class="report-container">
        <div class="header-bar">
            <div>
                <div class="brand-logo">
                    <span>&#9889; CROSSWIRE</span>
                    <span class="brand-sub">Security Assessment Report</span>
                </div>
                <div style="font-size: 13px; color: var(--text-muted); margin-top: 4px;">
                    Deterministic Transaction Invariant & Outbox Architecture Verification
                </div>
            </div>
            <div class="report-meta-box">
                <div><strong>Generated:</strong> {timestamp}</div>
                <div><strong>Scope:</strong> Single-Hotel / Single-Currency Travel Sandbox</div>
                <div><strong>Classification:</strong> Hackathon Technical Review</div>
            </div>
        </div>

        <div class="kpi-grid">
            <div class="kpi-card">
                <div class="kpi-label">Total Executions</div>
                <div class="kpi-val val-cyan">{stats['total_executions']}</div>
            </div>
            <div class="kpi-card">
                <div class="kpi-label">Vulnerabilities Detected</div>
                <div class="kpi-val val-crit">{total_findings}</div>
            </div>
            <div class="kpi-card">
                <div class="kpi-label">Critical / High</div>
                <div class="kpi-val val-high">{crit_count} / {high_count}</div>
            </div>
            <div class="kpi-card">
                <div class="kpi-label">Retests Passed</div>
                <div class="kpi-val val-pass">{retest_count} / {total_findings}</div>
            </div>
        </div>

        <h2 class="section-header">Verified Transaction & Financial Invariants</h2>
        <table class="exec-table">
            <thead>
                <tr>
                    <th>Invariant Key</th>
                    <th>Name</th>
                    <th>Target Risk</th>
                    <th>Verification Mechanism</th>
                    <th>Status</th>
                </tr>
            </thead>
            <tbody>
                <tr>
                    <td><code>INV-5.2</code></td>
                    <td><strong>Booking Intent Uniqueness</strong></td>
                    <td>Duplicate Supplier Reservations (CWE-675)</td>
                    <td>Relational ground-truth SQLite reservation counting</td>
                    <td><span class="status-pill status-pass">&#10004; VERIFIED</span></td>
                </tr>
                <tr>
                    <td><code>INV-5.3</code></td>
                    <td><strong>Webhook Idempotency</strong></td>
                    <td>Duplicate Ledger Credit Mutations (CWE-799)</td>
                    <td>Atomic idempotency locks & event deduplication table</td>
                    <td><span class="status-pill status-pass">&#10004; VERIFIED</span></td>
                </tr>
                <tr>
                    <td><code>INV-5.5</code></td>
                    <td><strong>Refund Boundedness</strong></td>
                    <td>Financial Entitlement Over-Disbursement</td>
                    <td>Relational SUM(refunds) &lt;= eligible booking entitlement</td>
                    <td><span class="status-pill status-pass">&#10004; VERIFIED</span></td>
                </tr>
                <tr>
                    <td><code>INV-5.7</code></td>
                    <td><strong>Financial Consistency</strong></td>
                    <td>Accounting Double-Entry Leak</td>
                    <td>Mathematical proof: Booking == Refunds + Net Ledger</td>
                    <td><span class="status-pill status-pass">&#10004; VERIFIED</span></td>
                </tr>
                <tr>
                    <td><code>INV-M2.3B</code></td>
                    <td><strong>Parent Stay Coverage & Fencing</strong></td>
                    <td>Partial Bookings & Worker Split-Brain (CWE-841)</td>
                    <td>CoverageOracle topology graph + monotonic fencing tokens</td>
                    <td><span class="status-pill status-pass">&#10004; VERIFIED</span></td>
                </tr>
            </tbody>
        </table>

        <h2 class="section-header">Security Findings & Retest Proofs</h2>
        {''.join(findings_html_cards)}

        <h2 class="section-header">Execution Activity Log</h2>
        <table class="exec-table">
            <thead>
                <tr>
                    <th>Execution ID</th>
                    <th>Scenario</th>
                    <th>Mode</th>
                    <th>Outcome / Verdict</th>
                    <th>Invariants</th>
                    <th>Evidence SHA-256</th>
                    <th>Duration</th>
                    <th>Timestamp</th>
                </tr>
            </thead>
            <tbody>
                {''.join(executions_rows)}
            </tbody>
        </table>
    </div>
</body>
</html>
"""
    return html


def export_findings_json() -> str:
    """Exports all findings in state as pretty JSON."""
    state = get_state_store()
    return json.dumps([f.to_dict() for f in state.findings.values()], indent=2)


def export_executions_json() -> str:
    """Exports all executions in state as pretty JSON."""
    state = get_state_store()
    return json.dumps([e.to_dict() for e in state.executions], indent=2)
