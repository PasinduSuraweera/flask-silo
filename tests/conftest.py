"""Shared test configuration for flask-silo."""

import sys
import os

# Ensure the library source is importable
sys.path.insert(
    0, os.path.join(os.path.dirname(__file__), "..", "src")
)
