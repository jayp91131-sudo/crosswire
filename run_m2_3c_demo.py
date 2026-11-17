#!/usr/bin/env python3
"""
Crosswire Milestone 2.3C: One-Command End-to-End Demonstration Script.

Usage:
    python run_m2_3c_demo.py

Executes:
1. Primary Scenario: Ambiguous Supplier Commit-Then-Timeout
   - Vulnerable mode: commit-then-timeout -> blind retry -> 2 reservations -> INV-5.2 violated -> Finding CW-SEC-M2-001.
   - Fixed mode: commit-then-timeout -> idempotent reconciliation -> 1 reservation -> INV-5.2 passed.
   - Retest verification attached to finding.
2. Secondary Scenario: Duplicate Refund Webhook Replay
   - Vulnerable mode: 3 deliveries -> 3 duplicate ledger entries -> 15,000 INR vs 12,000 INR -> Finding CW-SEC-M2-002.
   - Fixed mode: 3 deliveries -> atomic deduplication -> 1 refund (5,000 INR) -> INV-5.3 & INV-5.5 passed.
   - Retest verification attached to finding.
3. Outbox Multi-Supplier Coordination:
   - M2.3A Relational Schema + M2.3B Outbox Engine executing multi-room booking across Expedia & Hotelbeds.
   - Observation audit trail in SQLite, Coverage Oracle verification, Parent Saga Coordinator confirmation.
"""

import sys
import asyncio
from pathlib import Path

# Add project root to sys.path
PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from m2_3c.demo import M23CDemoRunner


def main():
    runner = M23CDemoRunner(output_dir=PROJECT_ROOT)
    exit_code = asyncio.run(runner.run_all())
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
