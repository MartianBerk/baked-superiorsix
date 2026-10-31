import uvicorn
from fastapi import FastAPI

from baked_superiorsix import __version__
from baked_superiorsix.api.router import api_router


def create_app() -> FastAPI:
    app = FastAPI(
        title="baked-superiorsix",
        version=__version__,
        description="Superior Six game API. Identity via baked-auth JWT; profiles via baked-user.",
    )
    app.include_router(api_router)
    return app


app = create_app()


def run() -> None:
    uvicorn.run(
        "baked_superiorsix.main:app",
        host="0.0.0.0",
        port=8002,
        reload=False,
    )


if __name__ == "__main__":
    run()
