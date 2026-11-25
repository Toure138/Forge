"""Business logic (spec 001). Services receive the request's `RequestDb` (transaction and security
context already set) and `now` from the injected clock; they filter explicitly by organization,
with RLS underneath (constitution X)."""
