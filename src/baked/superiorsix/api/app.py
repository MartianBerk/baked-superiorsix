from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from baked.superiorsix.api.routers import (
    admin_router,
    euros_router,
    game_router,
    health_router,
    stats_router,
    user_router,
    worldcup_router,
)
from baked.superiorsix.core.config import Settings, get_settings
from baked.superiorsix.core.security import JwtKeySet, load_key_set
from baked.superiorsix.db.session import create_engine, create_session_factory, init_db


def create_app(
    settings: Settings | None = None,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
    engine: AsyncEngine | None = None,
    key_set: JwtKeySet | None = None,
) -> FastAPI:
    settings = settings or get_settings()
    owned_engine = engine is None
    engine = engine or create_engine(settings.database_url)
    session_factory = session_factory or create_session_factory(engine)
    key_set = key_set or load_key_set(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.settings = settings
        app.state.session_factory = session_factory
        app.state.key_set = key_set
        await init_db(engine)
        yield
        if owned_engine:
            await engine.dispose()

    app = FastAPI(
        title=settings.app_name,
        version="0.1.0",
        description="Superior Six game API. Identity via baked-auth JWT; profiles via baked-user.",
        lifespan=lifespan,
        openapi_url="/openapi.json",
        docs_url="/docs",
        redoc_url="/redoc",
    )
    app.state.settings = settings
    app.state.session_factory = session_factory
    app.state.key_set = key_set
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.dependency_overrides[get_settings] = lambda: settings

    app.include_router(health_router)
    app.include_router(game_router)
    app.include_router(user_router)
    app.include_router(stats_router)
    app.include_router(admin_router)
    app.include_router(euros_router)
    app.include_router(worldcup_router)
    return app
