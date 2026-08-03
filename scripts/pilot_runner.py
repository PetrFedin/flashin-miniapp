#!/usr/bin/env python3
"""Backward-compatible entry point for the executable pilot control plane."""

from pilot_control import main


if __name__ == "__main__":
    raise SystemExit(main())
