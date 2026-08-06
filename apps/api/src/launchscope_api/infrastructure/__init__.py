"""Application infrastructure adapters.

The infrastructure package is deliberately outside ``packages.domain``.  It
owns database, messaging and external-system details while the domain package
continues to expose only ports and value objects.
"""
