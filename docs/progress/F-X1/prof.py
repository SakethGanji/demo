import time, duckdb, sys, resource
W=int(sys.argv[1]); p=f"$SCRATCH/w{W}.csv"
open(p,"w").write(",".join(f"c{i}" for i in range(W))+"\n"+",".join("1"*W)+"\n"+",".join("2"*W)+"\n")
con=duckdb.connect(); con.execute("SET memory_limit='1536MB'")
def m(): return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/1e6
t=time.time(); con.execute(f"SELECT Columns FROM sniff_csv('{p}', header=true, sample_size=20480)").fetchone(); print("sniff",round(time.time()-t,2),m())
cs="{"+",".join(f"'c{i}':'VARCHAR'" for i in range(W))+"}"
con.execute(f"CREATE TEMP VIEW src AS SELECT * FROM read_csv('{p}', header=true, columns={cs}, auto_detect=false)")
t=time.time(); con.execute("SELECT count(*) FROM src").fetchone(); print("count",round(time.time()-t,2),m())
aggs=[]
for i in range(W):
    c=f'"c{i}"'; aggs+= [f"count({c})", f"count(*) FILTER (WHERE regexp_matches({c}, '^0'))", f"count({c})-count(TRY_CAST({c} AS BIGINT))"]
t=time.time(); con.execute(f"SELECT {','.join(aggs)} FROM src").fetchone(); print("aggs one query",round(time.time()-t,2),m())
t=time.time()
for k in range(0,len(aggs),300): con.execute(f"SELECT {','.join(aggs[k:k+300])} FROM src").fetchone()
print("aggs chunked 100 cols",round(time.time()-t,2),m())
t=time.time(); con.execute(f"COPY (SELECT {','.join(f'TRY_CAST(c{i} AS BIGINT) AS c{i}' for i in range(W))} FROM src) TO '{p}.parquet' (FORMAT parquet)"); print("copy",round(time.time()-t,2),m())
