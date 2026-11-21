"""P19 (plan §6, C §1.6): authorisation.

    identity.py    Principal, IdentityProvider, TypedNameProvider, the agent's in-process provider
    scope.py       object → (team, project): resolvers per kind
    policy.py      the action matrix, decide(), decide_check() (four eyes)
    context.py     the per-request context (principal, request id, decisions)
    middleware.py  the request context and the response headers
    deps.py        require(action, obj…) / public(reason) / visible_teams(); mode()
    lists.py       the team on every list row, filtered in enforce mode
    coverage.py    the coverage gate
    api.py         /authz/coverage, /authz/check, /teams/{t}/audit, /teams/{t}/audit/verify; install(app)
"""

from .deps import (  # noqa: F401
    body,
    mode,
    path,
    public,
    query,
    require,
    variant,
    visible_teams,
)
from .lists import with_team  # noqa: F401
