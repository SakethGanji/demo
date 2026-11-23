"""External Postgres destinations must honor broker host bindings."""
import pytest
from src.broker.policy import EgressGuard, PolicyError, check_read_only


def test_bound_postgres_destination():
    EgressGuard(['db.example.com'], {'DB_URL': ['db.example.com']}).check_postgres_connection(
        'postgresql://reader:password@db.example.com:5432/demo')


@pytest.mark.parametrize('dsn', [
    'postgresql://reader:password@evil.example.com/demo',
    'host=db.example.com hostaddr=127.0.0.1 dbname=demo',
    'host=db.example.com,evil.example.com dbname=demo',
    'host=/tmp dbname=demo', 'service=other', 'dbname=demo',
    'host=db.example.com port=5432,5433 dbname=demo',
])
def test_alternative_destinations_cannot_bypass_binding(dsn):
    with pytest.raises(PolicyError):
        EgressGuard([], {'DB_URL': ['db.example.com']}).check_postgres_connection(dsn)


def test_project_allowlist_also_applies():
    with pytest.raises(PolicyError, match='project egress allowlist'):
        EgressGuard(['other.example.com'], {'DB_URL': ['db.example.com']}).check_postgres_connection(
            'host=db.example.com dbname=demo')


def test_generation_self_tests_still_refuse_external_sql():
    with pytest.raises(PolicyError) as error:
        check_read_only('Postgres', {'query': 'SELECT 1'})
    assert error.value.code == 'read_only'
