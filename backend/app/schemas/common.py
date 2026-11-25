"""Types shared by several schemas of the API contract.

They mirror `app/core/authz.py`, which alone gives roles their meaning; the test
`tests/test_authz.py::test_schema_literals_match_authz` keeps both identical. `Permission` lists the
permissions of an active organization (what `ActiveOrganization.permissions` can contain), not the
ones every signed-in user has.
"""

from typing import Literal

Role = Literal["owner", "admin", "member", "guest"]

Permission = Literal[
    "organization:read",
    "organization:leave",
    "organization:rename",
    "agents:use",
    "members:read",
    "members:manage",
    "admins:manage",
    "owners:manage",
    "audit:read",
]
