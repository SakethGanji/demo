import os, sys, psycopg
out = []
def q(url, who, sql):
    try:
        with psycopg.connect(url) as c:
            r = c.execute(sql); n = r.rowcount; c.rollback()
            out.append(f"{who:5} {sql[:95]:95} → OK rows={n}")
    except Exception as e:
        out.append(f"{who:5} {sql[:95]:95} → {type(e).__name__}: {str(e).splitlines()[0]}")
app, own = os.environ["ENSEMBLE_DB_URL"], os.environ["ENSEMBLE_DB_OWNER_URL"]
with psycopg.connect(own) as c:
    print("label triggers:", sorted(r[0] for r in c.execute("SELECT tgname FROM pg_trigger WHERE NOT tgisinternal AND (tgname LIKE 'le_%' OR tgname LIKE 'ri_%' OR tgname LIKE 'audit_%')")))
    print("label tables:", c.execute("SELECT count(*) FROM pg_tables WHERE schemaname='public' AND tablename LIKE 'label_%'").fetchone()[0])
    ev = c.execute("SELECT id FROM label_events WHERE purged_at IS NULL LIMIT 1").fetchone()
    evp = c.execute("SELECT id FROM label_events WHERE purged_at IS NOT NULL LIMIT 1").fetchone()
    ri = c.execute("SELECT run_id FROM run_inputs WHERE label_snapshot_id IS NOT NULL LIMIT 1").fetchone()
    print("events live/purged sample:", ev, evp, "pinned run:", ri)
ev = ev[0] if ev else evp[0]; evp = evp[0] if evp else ev
for url, who in ((app, "app"), (own, "owner")):
    q(url, who, f"UPDATE label_events SET value = '\"x\"' WHERE id = '{ev}'")
    q(url, who, f"UPDATE label_events SET actor = 'mallory' WHERE id = '{evp}'")
    q(url, who, f"DELETE FROM label_events WHERE id = '{ev}'")
    q(url, who, f"UPDATE label_events SET purged_at = 'now', purge_id = 'p', value = NULL, provenance = '{{}}', row_hash_ctx = NULL, row_hash_all = NULL, base_value_hash = NULL, actor = 'mallory' WHERE id = '{ev}'")
    q(url, who, f"UPDATE label_events SET purged_at = 'now', purge_id = 'p', value = NULL, provenance = '{{}}', row_hash_ctx = NULL, row_hash_all = NULL, base_value_hash = NULL WHERE id = '{ev}'")
    if ri: q(url, who, f"UPDATE run_inputs SET label_snapshot_id = 'x' WHERE run_id = '{ri[0]}'")
    if ri: q(url, who, f"UPDATE run_inputs SET labels_fitted_sha256 = 'x' WHERE run_id = '{ri[0]}'")
    q(url, who, "UPDATE audit_log SET actor = 'x'")
    q(url, who, "DELETE FROM audit_log")
    q(url, who, "CREATE TABLE zz (a int)")
    q(url, who, "DROP TRIGGER le_ro_u ON label_events")
print("\n".join(out))
