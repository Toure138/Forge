from fastapi import APIRouter

router = APIRouter(tags=["health"])


# The operation name `health` is listed in `authz.PUBLIC_ROUTES`: the route is exempted from
# authentication by that name.
@router.get("/health", name="health", response_model=dict[str, str])
async def health() -> dict[str, str]:
    return {"status": "ok"}
