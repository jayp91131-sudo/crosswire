#!/usr/bin/env python3
"""
Crosswire Milestone 2.4: Local Control Center Launcher.

Usage:
    python run_control_center.py [--port 8000] [--host 127.0.0.1]

Starts the web-based local security operations control center,
providing interactive scenario execution, timeline audits, findings exploration,
retest verification, and security assessment report export.
"""

import sys
import argparse
from pathlib import Path
import uvicorn

# Add project root to sys.path
PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def main():
    parser = argparse.ArgumentParser(description="Crosswire Control Center Web Application")
    parser.add_argument("--host", default="127.0.0.1", help="Host address (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8000, help="Port number (default: 8000)")
    parser.add_argument("--reload", action="store_true", help="Enable live auto-reload")
    args = parser.parse_args()

    url = f"http://{args.host}:{args.port}"
    print("\n" + "=" * 78)
    print("  CROSSWIRE CONTROL CENTER M2.4")
    print("  Web-Based Cybersecurity Testing Platform & Scenario Runner")
    print("=" * 78)
    print(f"  Access URL          : {url}")
    print(f"  API Docs            : {url}/docs")
    print(f"  Security Report     : {url}/api/reports/html")
    print(f"  Target Scope        : Single-Hotel / Single-Currency Local Synthetic Platform")
    print("=" * 78 + "\n")

    uvicorn.run(
        "control_center.app:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        log_level="info"
    )


if __name__ == "__main__":
    main()
