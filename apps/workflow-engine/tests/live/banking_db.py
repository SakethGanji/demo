"""Synthetic banking operations data for the opt-in real-provider benchmark."""
import json
import os
from decimal import Decimal

from psycopg2 import sql
from psycopg2.extras import RealDictCursor

from dashboard_db import DemoDatabase


class BankingDatabase(DemoDatabase):
    def setup(self):
        super().setup()
        with self.admin() as conn, conn.cursor() as cur:
            cur.execute('''
                CREATE TABLE bank_branches (id integer PRIMARY KEY, name text, region text);
                CREATE TABLE bank_accounts (id integer PRIMARY KEY, name text, branch_id integer REFERENCES bank_branches, balance numeric(14,2));
                CREATE TABLE bank_transactions (id text PRIMARY KEY, account_id integer REFERENCES bank_accounts, amount numeric(12,2), status text, booked_at date, channel text, reference text);
                CREATE TABLE bank_cases (id text PRIMARY KEY, transaction_id text REFERENCES bank_transactions, severity text, owner text, state text, reason text);
                CREATE TABLE bank_events (id integer PRIMARY KEY, transaction_id text REFERENCES bank_transactions, occurred_at timestamp, message text);
            ''')
            cur.executemany('INSERT INTO bank_branches VALUES (%s,%s,%s)', [
                (i, f'Branch {i:02}', ['Americas', 'EMEA', 'APAC', 'EMEA'][(i-1) % 4]) for i in range(1, 9)])
            cur.executemany('INSERT INTO bank_accounts VALUES (%s,%s,%s,%s)', [
                (i, f'Synthetic Client {i:03}', (i-1) % 8+1, Decimal(i)*Decimal('105000.25')) for i in range(1, 41)])
            txs=[]; cases=[]; events=[]
            for i in range(1, 601):
                tid=f'TX-{i:06}'
                status='exception' if i % 6 == 0 else ('pending' if i % 6 == 1 else 'settled')
                txs.append((tid, (i-1) % 40+1, Decimal((i % 97+1)*100)+Decimal('.25'), status,
                            f'2026-10-{(i-1)%15+1:02}', ['wire','ach','card'][i%3], f'SYNTH-REF-{i:06}'))
                if status == 'exception':
                    cases.append((f'CASE-{i:06}', tid, 'high' if i%12==0 else 'medium',
                                  ['Avery Chen','Morgan Patel','Taylor Jones'][i%3], 'open',
                                  'Reconciliation mismatch' if i%12==0 else 'Counterparty review'))
                events.extend([(i*2-1,tid,f'2026-10-{(i-1)%15+1:02} 09:00:00','Payment received'),
                               (i*2,tid,f'2026-10-{(i-1)%15+1:02} 09:05:00',f'Status changed to {status}')])
            cur.executemany('INSERT INTO bank_transactions VALUES (%s,%s,%s,%s,%s,%s,%s)',txs)
            cur.executemany('INSERT INTO bank_cases VALUES (%s,%s,%s,%s,%s,%s)',cases)
            cur.executemany('INSERT INTO bank_events VALUES (%s,%s,%s,%s)',events)
            cur.execute(sql.SQL('GRANT SELECT ON bank_branches,bank_accounts,bank_transactions,bank_cases,bank_events TO {}').format(sql.Identifier(self.user)))
        if os.environ.get('BANKING_FULL_E2E') == '1':
            with self.admin() as conn, conn.cursor() as cur:
                cur.execute("""CREATE TABLE bank_case_actions (
                    id bigserial PRIMARY KEY, case_id text REFERENCES bank_cases(id),
                    state text NOT NULL, owner text NOT NULL, note text NOT NULL,
                    occurred_at timestamptz NOT NULL DEFAULT now())""")
                cur.execute(sql.SQL('GRANT UPDATE ON bank_cases TO {}').format(sql.Identifier(self.user)))
                cur.execute(sql.SQL('GRANT SELECT,INSERT ON bank_case_actions TO {}').format(sql.Identifier(self.user)))
                cur.execute(sql.SQL('GRANT USAGE,SELECT ON SEQUENCE bank_case_actions_id_seq TO {}').format(sql.Identifier(self.user)))
        with self.admin() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT count(*)::int AS transactions, sum(amount) AS volume, count(*) FILTER(WHERE status='exception')::int AS exceptions, count(*) FILTER(WHERE status='pending')::int AS pending FROM bank_transactions")
            expected=dict(cur.fetchone())
            expected.update(accounts=40,branches=8,cases=100,events=1200)
        (self.tmp/'banking-expected.json').write_text(json.dumps(expected,default=float))
