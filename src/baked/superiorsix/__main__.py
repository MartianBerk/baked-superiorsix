import uvicorn


def main() -> None:
    uvicorn.run(
        "baked.superiorsix.api.app:create_app",
        factory=True,
        host="0.0.0.0",
        port=8002,
        reload=True,
    )


if __name__ == "__main__":
    main()
