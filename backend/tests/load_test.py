import pytest
import asyncio
import httpx
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from database import Base, get_db
import database
from main import app

# Sandboxed DB for load testing
SQLALCHEMY_DATABASE_URL = "sqlite:///./test_trading.db"
engine = create_engine(SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False})
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

def override_get_db():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()

app.dependency_overrides[get_db] = override_get_db
database.SessionLocal = TestingSessionLocal
database.engine = engine
client = TestClient(app)

@pytest.fixture(autouse=True)
def setup_db():
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)

@pytest.mark.asyncio
async def test_webhook_load_100_concurrent():
    """
    Load Testing: Simulate 100 concurrent webhook requests hitting the engine.
    Ensures the application does not crash under typical scanning bursts.
    """
    payload = {
        "stocks": "HDFCBANK",
        "trigger_prices": "1500",
        "scan_name": "Load Test Scan"
    }

    # Using httpx.AsyncClient to hit the FastAPI test client
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as ac:
        tasks = []
        for i in range(100):
            tasks.append(ac.post("/api/webhook/chartink", json=payload))
        
        # Execute 100 requests simultaneously
        responses = await asyncio.gather(*tasks)
        
        # Verify all 100 requests were accepted
        assert len(responses) == 100
        for r in responses:
            assert r.status_code == 200
            assert r.json()["status"] == "accepted"
