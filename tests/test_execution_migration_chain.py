"""Unmodified SQL scripts, full 001–023 chain, disposable PostgreSQL only."""
from pathlib import Path
import uuid
import pytest
from sqlalchemy import text
from test_signal_database import engine, TEST_URL

pytestmark=pytest.mark.skipif(not TEST_URL,reason='disposable PostgreSQL required')


def test_full_chain_and_applied_version_bookkeeping(engine):
    schema='fullchain_'+uuid.uuid4().hex
    paths=sorted((Path(__file__).resolve().parents[1]/'database/migrations').glob('[0-9]*.sql'))
    with engine.connect().execution_options(isolation_level='AUTOCOMMIT') as c:
        c.exec_driver_sql(f'CREATE SCHEMA "{schema}"')
        try:
            c.exec_driver_sql(f'SET search_path TO "{schema}", public')
            # Use a raw cursor without parameters: SQL comments contain '%' and
            # psycopg must not interpret those as parameter placeholders.
            prior_signal = None
            for path in paths:
                if path.name.startswith('020_'):
                    c.execute(text('''INSERT INTO signals
                        (symbol,exchange,timeframe,direction,strategy_name,strategy_version,
                         candle_open_time,candle_close_time,generated_at,entry_price,stop_loss,status,environment)
                        VALUES ('BTC','synthetic','1h','LONG','test','v1',
                         '2001-01-01T00:00:00Z','2001-01-01T01:00:00Z','2001-01-01T01:00:01Z',
                         100,90,'PENDING','research')'''))
                    prior_signal=c.execute(text('SELECT to_jsonb(s) FROM signals s')).scalar_one()
                with c.connection.dbapi_connection.cursor() as cursor:
                    cursor.execute(path.read_text())
            assert c.execute(text('SELECT to_jsonb(s) FROM signals s')).scalar_one()==prior_signal
            assert set(c.execute(text('SELECT version FROM schema_migrations')).scalars()) == {
                p.name.split('_')[0] for p in paths}
            for table in ('signals','publication_batches','execution_fill_evidence',
                          'manager_execution_observations','manager_order_observations'):
                assert c.execute(text('SELECT to_regclass(:name)'),{'name':f'{schema}.{table}'}).scalar()
            # 017 is intentionally repeatable for databases that previously
            # applied the table but never registered its version.
            with c.connection.dbapi_connection.cursor() as cursor:
                cursor.execute(next(p for p in paths if p.name.startswith('017_')).read_text())
            assert c.execute(text("SELECT count(*) FROM schema_migrations WHERE version='017'")).scalar()==1
        finally:
            c.exec_driver_sql('ROLLBACK')
            c.exec_driver_sql(f'DROP SCHEMA "{schema}" CASCADE')
