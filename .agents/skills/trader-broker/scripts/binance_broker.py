#!/usr/bin/env python3
"""CLI Entrypoint for trader-broker."""
import os
import sys

_parent = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _parent not in sys.path:
    sys.path.insert(0, _parent)
_root = os.path.dirname(_parent)
if _root not in sys.path:
    sys.path.insert(0, _root)

import binance_broker

if __name__ == "__main__":
    sys.exit(binance_broker.main())

