"""Local (email/password) registration, login, and the current-user endpoint."""

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.dependencies import get_current_user
from app.core.rate_limit import check_rate_limit
from app.core.security import create_access_token
from app.database.session import get_db_session
from app.models.user import User
from app.schemas.auth import TokenResponse, UserLoginRequest, UserRegisterRequest, UserResponse
from app.services.auth_service import authenticate_user, register_user

router = APIRouter(prefix="/auth", tags=["auth"])

# There's no authenticated user yet at login — the rate-limit key is the
# submitted email itself, the only thing distinguishing one login attempt
# from another before credentials are verified. 10 attempts per 5 minutes
# comfortably covers a real user who mistypes their password a few times
# while meaningfully slowing a brute-force attempt against one account.
# Matches the scale of workflows.py's _STAGE_START_RATE_LIMIT.
_LOGIN_RATE_LIMIT = 10
_LOGIN_RATE_WINDOW_SECONDS = 300.0


@router.post(
    "/register", response_model=UserResponse, status_code=201, summary="Create a local account"
)
async def register(
    request: UserRegisterRequest,
    db: AsyncSession = Depends(get_db_session),
) -> User:
    return await register_user(db, request)


@router.post("/login", response_model=TokenResponse, summary="Log in with email and password")
async def login(
    request: UserLoginRequest,
    db: AsyncSession = Depends(get_db_session),
) -> TokenResponse:
    # Keyed by the submitted email (lowercased — email is case-insensitive
    # for matching purposes) rather than IP: the API sits behind whatever
    # reverse proxy/load balancer terminates TLS, and this app doesn't
    # currently thread the real client IP through to route handlers. An
    # email-keyed limit still stops the practically-relevant case (many
    # guesses against *one* account) even though it doesn't stop one
    # attacker spraying many different emails at low volume each.
    check_rate_limit(
        f"login:{request.email.lower()}",
        max_requests=_LOGIN_RATE_LIMIT,
        window_seconds=_LOGIN_RATE_WINDOW_SECONDS,
    )
    user = await authenticate_user(db, request.email, request.password)
    access_token = create_access_token(subject=str(user.id))
    return TokenResponse(access_token=access_token)


@router.get("/me", response_model=UserResponse, summary="Get the current authenticated user")
async def read_current_user(current_user: User = Depends(get_current_user)) -> User:
    return current_user
