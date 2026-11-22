"""Request identity and tenant boundary, also propagated into background jobs."""

from contextvars import ContextVar

workspace = ContextVar("workspace", default=None)
principal = ContextVar("principal", default=None)
