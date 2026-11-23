-- Synthetic demonstration data only. Run in a dedicated empty demo database.
-- Existing tables cause the transaction to fail; this script never drops data.
BEGIN;
CREATE TABLE bank_branches (id integer PRIMARY KEY, name text, region text);
CREATE TABLE bank_accounts (id integer PRIMARY KEY, name text, branch_id integer REFERENCES bank_branches, balance numeric(14,2));
CREATE TABLE bank_transactions (id text PRIMARY KEY, account_id integer REFERENCES bank_accounts, amount numeric(12,2), status text, booked_at date, channel text, reference text);
CREATE TABLE bank_cases (id text PRIMARY KEY, transaction_id text REFERENCES bank_transactions, severity text, owner text, state text, reason text);
CREATE TABLE bank_events (id integer PRIMARY KEY, transaction_id text REFERENCES bank_transactions, occurred_at timestamp, message text);
INSERT INTO bank_branches
SELECT i, 'Branch ' || lpad(i::text,2,'0'), (ARRAY['Americas','EMEA','APAC','EMEA'])[(i-1)%4+1]
FROM generate_series(1,8) AS i;
INSERT INTO bank_accounts
SELECT i, 'Synthetic Client ' || lpad(i::text,3,'0'), (i-1)%8+1, i*105000.25
FROM generate_series(1,40) AS i;
INSERT INTO bank_transactions
SELECT 'TX-' || lpad(i::text,6,'0'), (i-1)%40+1, (i%97+1)*100+.25,
       CASE WHEN i%6=0 THEN 'exception' WHEN i%6=1 THEN 'pending' ELSE 'settled' END,
       DATE '2026-10-01' + ((i-1)%15), (ARRAY['wire','ach','card'])[i%3+1],
       'SYNTH-REF-' || lpad(i::text,6,'0')
FROM generate_series(1,600) AS i;
INSERT INTO bank_cases
SELECT 'CASE-' || lpad(i::text,6,'0'), 'TX-' || lpad(i::text,6,'0'),
       CASE WHEN i%12=0 THEN 'high' ELSE 'medium' END,
       (ARRAY['Avery Chen','Morgan Patel','Taylor Jones'])[i%3+1], 'open',
       CASE WHEN i%12=0 THEN 'Reconciliation mismatch' ELSE 'Counterparty review' END
FROM generate_series(1,600) AS i WHERE i%6=0;
INSERT INTO bank_events
SELECT i*2-1, 'TX-' || lpad(i::text,6,'0'), TIMESTAMP '2026-10-01 09:00:00' + ((i-1)%15)*INTERVAL '1 day', 'Payment received'
FROM generate_series(1,600) AS i;
INSERT INTO bank_events
SELECT i*2, 'TX-' || lpad(i::text,6,'0'), TIMESTAMP '2026-10-01 09:05:00' + ((i-1)%15)*INTERVAL '1 day',
       'Status changed to ' || CASE WHEN i%6=0 THEN 'exception' WHEN i%6=1 THEN 'pending' ELSE 'settled' END
FROM generate_series(1,600) AS i;
CREATE INDEX bank_transactions_account_idx ON bank_transactions(account_id);
CREATE INDEX bank_transactions_status_idx ON bank_transactions(status);
CREATE INDEX bank_events_transaction_idx ON bank_events(transaction_id);
COMMIT;
