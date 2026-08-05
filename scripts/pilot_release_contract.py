"""Shared immutable pilot release capability contract.

Keep this module dependency-free: backend runtime validation imports it as a
package, while the release CLI imports the same constant in script mode.
"""

CAPABILITY_VERSION = 13
