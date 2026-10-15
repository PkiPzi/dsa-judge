"""Local AI providers. Every AI call goes through this package, and only to loopback hosts.

Heavy AI dependencies live in the ``[ai]`` extra and are imported lazily; importing this
package must work with only the core dependencies installed.
"""
