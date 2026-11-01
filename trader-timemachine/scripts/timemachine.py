#!/usr/bin/env python3
"""CLI Entrypoint for trader-timemachine."""
import os
import sys

_parent = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _parent not in sys.path:
    sys.path.insert(0, _parent)
_root = os.path.dirname(_parent)
if _root not in sys.path:
    sys.path.insert(0, _root)

import timemachine

if __name__ == "__main__":
    sys.exit(timemachine.main())

