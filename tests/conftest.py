"""Test setup: make the app's shared UI modules importable for page smoke tests.

`streamlit run app/Home.py` puts app/ on sys.path itself; AppTest on a single page does not.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))
