#!/usr/bin/env python3
"""ork_batch.py -- batch generation / modification of OpenRocket .ork files.

    python ork_batch.py inspect  reference.ork
    python ork_batch.py generate -b reference.ork -v "fins.rootchord=10:16:1" -v "fins.height=5:9:1"
    python ork_batch.py modify   reference.ork -s "nosecone.length=28in" -o edited.ork

See README.md for everything else.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from orkbatch.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
