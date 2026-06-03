"""Shared test fixtures — single test database for all test modules."""

import os

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

# Disable scheduler in tests
os.environ["CANCELKIT_SCHEDULER"] = "false"

from app.database import Base, get_db
from app.main import app
from app.seed import seed_database

TEST_DB_URL = "sqlite:///./test_cancelkit.db"
engine = create_engine(TEST_DB_URL, connect_args={"check_same_thread": False})
TestSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def override_get_db():
    db = TestSessionLocal()
    try:
        yield db
    finally:
        db.close()


app.dependency_overrides[get_db] = override_get_db


@pytest.fixture(autouse=True)
def setup_db():
    Base.metadata.create_all(bind=engine)
    db = TestSessionLocal()
    seed_database(db)
    db.close()
    yield
    Base.metadata.drop_all(bind=engine)


@pytest.fixture
def db():
    db = TestSessionLocal()
    yield db
    db.close()


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def api_key(client: TestClient):
    resp = client.post("/v1/keys", json={"name": "Test User", "email": "test@example.com"})
    assert resp.status_code == 201
    return resp.json()["key"]
