"""Every filesystem location the backend uses, derived once.

Before this module existed, BASE_DIR was re-derived independently in five
modules — and diagnostics.py's private PHOTOS_DIR copy escaped the test
fixture that redirects photo storage, so its tests read the real photos dir.

Import notes:
- This module imports nothing from backend, so anything (including logger)
  may import it without cycles.
- storage.py and photo_processor.py re-export PHOTOS_DIR/OVERLAYS_DIR as
  their own module attributes deliberately: the test fixture monkeypatches
  them per-module (conftest.temp_workspace). Code that should honor that
  redirection must read `storage.PHOTOS_DIR` at call time, not import the
  name at module load.
"""

import os

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

PHOTOS_DIR = os.path.join(BASE_DIR, "backend", "photos")
OVERLAYS_DIR = os.path.join(BASE_DIR, "backend", "overlays")
FONT_PATH = os.path.join(BASE_DIR, "backend", "PlayfairDisplay-Regular.ttf")
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
