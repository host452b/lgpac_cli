#!/usr/bin/env python3
"""entry point wrapper - run with: python lgpac_cli.py <command>"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from lgpac.cli import app

if __name__ == "__main__":
    app()
