from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from api.routers import collections, query

app = FastAPI(title="KB Agent API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://localhost:3001",
        "http://localhost:3002",
        "http://localhost:3003",
        "http://localhost:3004",
    ],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(collections.router, prefix="/collections", tags=["collections"])
app.include_router(query.router, prefix="/query", tags=["query"])
