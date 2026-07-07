"""
Pytest configuration file to fix module imports.

This file ensures that the project root directory is in Python's sys.path,
allowing test files to import from project modules like 'utils', 'rag_utils', etc.
"""

import sys
from pathlib import Path

# Add the project root directory to sys.path
project_root = Path(__file__).parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))
