#!/usr/bin/env python3
"""Check that the unit's OPS243, camera and IWR6843 are present and answering.

The tester page's Check the unit runs this (step 2); the tester server reads the
LIS3DH itself. See openflight.unit_check.
"""

from openflight.unit_check import main

if __name__ == "__main__":
    raise SystemExit(main())
