"""F-X5a: a 10M-row seed like X5's (13 columns: zip, account_no, branch, app_date, ... y), written by DuckDB."""
import sys, duckdb
n = int(sys.argv[1]); out = sys.argv[2]
con = duckdb.connect()
con.execute(f"""COPY (
 SELECT lpad(CAST((i * 7919) % 99999 AS VARCHAR), 5, '0') AS zip,
        'A' || lpad(CAST(i AS VARCHAR), 9, '0') AS account_no,
        lpad(CAST((i * 31) % 10000 AS VARCHAR), 4, '0') AS branch,
        DATE '2020-01-01' + CAST((i * 13) % 1500 AS INTEGER) AS app_date,
        round(((i * 2654435761) % 1000003) / 37.0, 2) AS amount,
        round(20000 + ((i * 40503) % 150000), 0) AS income,
        18 + (i * 17) % 60 AS age,
        300 + (i * 101) % 550 AS score,
        round(((i * 97) % 1000) / 1000.0, 3) AS util,
        ['retail','sme','corp','private','public'][1 + (i % 5)] AS segment,
        ['web','branch','phone'][1 + ((i // 7) % 3)] AS channel,
        CASE WHEN i % 11 = 0 THEN NULL ELSE 'note ' || CAST(i % 997 AS VARCHAR) END AS note,
        CASE WHEN ((i * 2654435761) % 1000003) / 1000003.0 + ((i * 101) % 550) / 1100.0 + ((i * 97) % 1000) / 4000.0 > 0.95 THEN 1 ELSE 0 END AS y
 FROM range({n}) t(i)) TO '{out}' (HEADER, DELIMITER ',')""")
print(con.execute(f"SELECT count(*), avg(y) FROM read_csv_auto('{out}')").fetchall())
