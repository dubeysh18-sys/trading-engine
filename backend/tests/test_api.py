import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from database import Base, get_db, Alert
import database
from main import app

# Create an in-memory SQLite database specifically for testing
SQLALCHEMY_DATABASE_URL = "sqlite:///./test_trading.db"
engine = create_engine(SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False})
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

def override_get_db():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()

# Override the FastAPI dependency
app.dependency_overrides[get_db] = override_get_db
database.SessionLocal = TestingSessionLocal
database.engine = engine
client = TestClient(app)

@pytest.fixture(autouse=True)
def setup_db():
    """Create and wipe tables before and after each test."""
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)

def test_webhook_negative_missing_payload():
    """Negative Testing: Send an empty request to the webhook."""
    response = client.post("/api/webhook/chartink")
    assert response.status_code == 422  # Unprocessable Entity (Pydantic validation failure)

def test_webhook_negative_invalid_stock():
    """Negative Testing: Send a stock that doesn't exist."""
    payload = {
        "stocks": "INVALID_STOCK_123,ANOTHER_INVALID",
        "trigger_prices": "100,200",
        "scan_name": "Test Scan"
    }
    response = client.post("/api/webhook/chartink", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "accepted"
    # Even though it's processing in the background, we've successfully passed validation.

def test_get_alerts_empty():
    """Integration Test: Ensure /api/alerts returns 200 with empty list on fresh DB."""
    response = client.get("/api/alerts?date=2026-01-01")
    assert response.status_code == 200
    assert response.json() == []

def test_create_and_fetch_alerts():
    """Integration Test: Manually insert an alert into the test DB and fetch it via API."""
    db = TestingSessionLocal()
    dummy_alert = Alert(
        stock="RELIANCE",
        scan_name="Dummy Scan",
        trigger_time="10:00 am",
        trigger_date="2026-05-22",
        verdict="ENTER"
    )
    db.add(dummy_alert)
    db.commit()
    db.close()

    response = client.get("/api/alerts?date=2026-05-22")
    assert response.status_code == 200
    alerts = response.json()
    assert len(alerts) == 1
    assert alerts[0]["stock"] == "RELIANCE"
    assert alerts[0]["verdict"] == "ENTER"
