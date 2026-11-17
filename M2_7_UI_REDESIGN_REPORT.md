# CROSSWIRE M2.7 — STRUCTURAL UI REDESIGN REPORT
## Project: Forensic Operations Console

**Milestone:** M2.7  
**Category:** Travel-Specific Business-Logic & Transaction-Integrity DAST  
**Design Identity:** *CROSSWIRE — FORENSIC OPERATIONS CONSOLE*  
**Visual Philosophy:** Financial terminal precision, enterprise investigation software, engineering workstation layout, Swiss editorial typography, and high information density.  
**Date:** November 2026  
**Status:** COMPLETE & FULLY VERIFIED (266/266 Tests Passing)

---

## 1. Executive Summary: The Structural Shift

Milestone 2.7 replaces the card-heavy SaaS presentation of previous versions with a **pure forensic operations console**.

### Critical Architectural Transformations
1. **Elimination of Card Grids**:
   - Removed all 3-column KPI card containers, floating feature tiles, and repeated rounded white cards.
   - Replaced with flush horizontal metrics strips, dense data tables, and structured row catalogues.
2. **True 3-Pane Forensic Workspace**:
   - **Left Control Station (22%)**: Target scenario picker, execution ID, industrial segmented mode switcher (`VULNERABLE` vs `FIXED`), and one-click execution trigger.
   - **Center Chronological Timeline (48%)**: Continuous unbroken vertical spine with actor chips (`AGGREGATOR`, `SUPPLIER`, `PAYMENT`, `ORACLE`), aligned timestamps, transaction IDs, and interactive event selection. Zero floating event cards.
   - **Right Docked Inspection Panel (30%)**: Real-time event deep-dive, database observations, expected vs actual observed state, and raw assertion metadata.
   - **Bottom Invariant Status Bar**: Real-time evaluation summary.
3. **Findings Security Registry**:
   - Replaced card lists with an enterprise data table (`Finding ID`, `Severity`, `Title`, `Scenario`, `Violated Invariant`, `CWE`, `Retest Status`).
   - Selecting a row opens a docked side inspection drawer with root cause analysis, expected vs observed state comparison table, and one-click retest trigger.
4. **Evidence Forensic Inspector**:
   - 3-column split layout: Left trace picker, Center recessed dark graphite (`#202721`) raw JSON terminal with copy utility, Right invariant predicate breakdown.

---

## 2. Visual Identity & Approved Color Tokens

All approved M2.6 colors are preserved without decorative gradients, neon effects, or glassmorphism:

| Design Token | Hex Code | Purpose |
| :--- | :--- | :--- |
| **Warm Ivory** | `#F0EFE9` | Background canvas |
| **White** | `#FFFFFF` | Primary workstation surface |
| **Graphite** | `#202721` | High-contrast typography & dark inspection wells |
| **Terracotta** | `#C64C32` | Primary brand accent & active navigation |
| **Critical Red** | `#B9362B` | Invariant violations & vulnerable execution mode |
| **Warning Amber** | `#C28B35` | Ambiguous timeout & workflow warnings |
| **Success Green** | `#35694F` | Invariant compliance & fixed mode verification |
| **Slate Blue** | `#49677A` | Technical identifiers & aggregator actor chip |
| **Structural Border** | `#D9DAD2` | Sharp hairline table borders & split pane dividers |

---

## 3. Screen-by-Screen Architecture

### 1. Operations Overview (`#overview`)
- **Editorial Header:** `TRANSACTION INTEGRITY` with environment specification.
- **Horizontal Metrics Strip:** Replaces 3-column KPI boxes with 4 contiguous metric cells separated by vertical hairline dividers (`Executions`, `Confirmed Vulnerabilities`, `Critical/High`, `Verified Retests`).
- **Main Execution Registry:** Occupies primary screen real estate with a dense, interactive data table. Clicking any row navigates directly to the forensic execution inspector.
- **Invariant Matrix:** Tabular status overview of active invariant predicates (`INV-5.2`, `INV-5.3`, `INV-5.4`, `INV-5.5`).

### 2. Scenario Catalogue (`#scenarios`)
- Replaces repeated cards with horizontal catalogue rows.
- Each row contains scenario ID, name, description, invariant binding, and direct action triggers (`Run Vulnerable` / `Run Fixed`).

### 3. Execution Workspace (`#workspace`)
- 3-pane workstation layout (22% Controls | 48% Chronological Timeline | 30% Docked Inspector).
- Unbroken timeline track with actor markers (`AGGREGATOR`, `SUPPLIER`, `PAYMENT`, `ORACLE`).
- Interactive row selection dynamically updates the docked inspector with database observations.

### 4. Findings Registry (`#findings`)
- High-density security table with severity indicators.
- Right-side docked dossier with root cause analysis, clean expected vs observed tabular comparison, and direct retest execution.

### 5. Evidence Inspector (`#evidence`)
- Split-screen editor layout with trace dropdown, dark graphite terminal (`#202721`) for unadulterated JSON payloads, and predicate breakdown.

### 6. Reports & Diagnostics (`#reports`, `#diagnostics`)
- Tabular download registry for HTML Audit Reports and JSON bundles.
- System diagnostics table displaying SQLite ledger status and synthetic testbed configuration.

---

## 4. Verification & Regression Testing

### 1. Full Automated Test Suite (266 / 266 Passed)
```
control_center/tests/test_control_center.py .........                    [ 3%]
m2_3c/tests/test_m2_3c.py ...................................            [16%]
m2_3b/tests/test_m2_3b.py .............................................. [34%]
tests/ ................................................................. [100%]

============================= 266 passed in 62.56s =============================
```

### 2. High-Resolution Screenshots Captured
Screenshots captured across both **1440x900** (desktop primary) and **1366x768** (laptop target):

| Screen | 1440×900 Screenshot | 1366×768 Screenshot |
| :--- | :--- | :--- |
| **Overview** | `screenshot_01_overview.png` (`m2_7_1440_01_overview.png`) | `m2_7_1366_01_overview.png` |
| **Scenario Library** | `screenshot_02_scenarios.png` (`m2_7_1440_02_scenarios.png`) | `m2_7_1366_02_scenarios.png` |
| **Execution Workspace** | `screenshot_03_execution_workspace.png` | `m2_7_1366_03_execution_workspace.png` |
| **Findings Registry** | `screenshot_04_findings.png` (`m2_7_1440_04_findings.png`) | `m2_7_1366_04_findings.png` |
| **Evidence Inspector** | `screenshot_05_evidence.png` (`m2_7_1440_05_evidence.png`) | `m2_7_1366_05_evidence.png` |

### 3. Self-Contained Offline Compliance
- Zero external CDNs or web fonts.
- All styles and logic self-contained in `control_center/templates/index.html`.
