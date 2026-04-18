from fastapi import FastAPI

from api.routers.events import router as events_router


app = FastAPI(
    title="Flood Events API",
    description="Query flood events ingested from the EM-DAT data source.",
    version="0.1.0",
)


@app.get("/health")
def healthcheck() -> dict[str, str]:
    return {"status": "ok"}


app.include_router(events_router)
