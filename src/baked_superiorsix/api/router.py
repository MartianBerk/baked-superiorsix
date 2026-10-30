from fastapi import APIRouter

from baked_superiorsix.api.routes import admin, euros, game, health, stats, user, worldcup

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(game.router)
api_router.include_router(user.router)
api_router.include_router(stats.router)
api_router.include_router(admin.router)
api_router.include_router(euros.router)
api_router.include_router(worldcup.router)
