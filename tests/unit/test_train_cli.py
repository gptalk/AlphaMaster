"""Unit tests for train_cli.py pure functions (no torch / no subprocess)."""
from __future__ import annotations

import argparse
import io
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

# Make sure train_cli.py can be imported as a module from the project root.
PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

import train_cli  # noqa: E402
