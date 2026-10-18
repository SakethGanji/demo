"""The journeys run on the same harness as tests/integration: a throwaway database and a fake-LLM engine."""

from ..integration.conftest import engine, fake_api, fake_stack, test_db, upstream  # noqa: F401  (fixtures)


def pytest_configure(config) -> None:
    config.addinivalue_line("markers", "integration: real engine over HTTP against a throwaway DB")
