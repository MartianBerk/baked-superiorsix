from baked.superiorsix.api.routers.admin import router as admin_router
from baked.superiorsix.api.routers.euros import router as euros_router
from baked.superiorsix.api.routers.game import router as game_router
from baked.superiorsix.api.routers.health import router as health_router
from baked.superiorsix.api.routers.stats import router as stats_router
from baked.superiorsix.api.routers.user import router as user_router
from baked.superiorsix.api.routers.worldcup import router as worldcup_router

__all__ = [
    "admin_router",
    "euros_router",
    "game_router",
    "health_router",
    "stats_router",
    "user_router",
    "worldcup_router",
]
