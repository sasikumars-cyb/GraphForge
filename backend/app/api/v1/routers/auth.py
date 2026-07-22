"""Local (email/password) registration, login, and the current-user endpoint."""

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.dependencies import get_current_user
from app.core.security import create_access_token
from app.database.session import get_db_session
from app.models.user import User
from app.schemas.auth import TokenResponse, UserLoginRequest, UserRegisterRequest, UserResponse
from app.services.auth_service import authenticate_user, register_user

router = APIRouter(prefix="/auth", tags=["auth"])


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
    user = await authenticate_user(db, request.email, request.password)
    access_token = create_access_token(subject=str(user.id))
    return TokenResponse(access_token=access_token)


@router.get("/me", response_model=UserResponse, summary="Get the current authenticated user")
async def read_current_user(current_user: User = Depends(get_current_user)) -> User:
    return current_user
