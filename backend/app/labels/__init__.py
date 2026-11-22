"""Label layers on the versioned lake (docs/plans/labeling.md). LB-A core: schema, append-only events, carry-over status,
resolution, frozen snapshots, the training overlay and materialise-to-version.

The Python contract (LB-B tasks / queues / LLM, LB-C UI call these; signatures are stable):

    from app import labels as L
    L.create_layer(db, dataset_id, table, name, by, kind='labels', purpose='', spec=None) -> layer
    L.add_schema(db, layer_id, spec, by, guidelines='', class_map=None) -> schema
    L.append_events(db, layer_id, events, by, version_id=None, source='human', task_id=None,
                    allowed_sources=None, ctx_columns=None) -> {appended, first_seq, last_seq, events, seen_version_id}
        event: {row_id, column, value | is_null, kind='label'|'confirm'|'reject'|'adjudicate'|'retract'|'propose'|'flag',
                item_key='', source?, confidence?, refers_to?, assignment_id?, task_id?, provenance?}
    L.events(db, layer_id, after_seq=0, limit=500, row_id=None) -> {events, next_after_seq}
    L.resolve(db, layer_id, policy=None, through_seq=None, columns=None, row_ids=None) -> {through_seq, policy, rows}
    L.status(db, layer_id, version_id=None, policy=None, with_rows=False) -> {totals, columns, rows?}
    L.create_snapshot(db, store, layer_id, policy, by) -> snapshot ; L.get_snapshot(db, sid) ; L.verify_snapshot(db, store, sid)
    L.materialise(app, snapshot_id, version_id, by, columns=None, reason='') -> {version_id, rows_changed, …}
Errors: labels.LabelError(status, code, message, **extra) → `.payload()` for the HTTP body.
"""

from .values import LabelError  # noqa: F401
from .store import (add_schema, append_events, create_layer, events, layer as _layer, layers, patch_layer,  # noqa: F401
                    current_schema)
from .resolve import resolve  # noqa: F401
from .status import status  # noqa: F401
from .snapshot import create as create_snapshot, get as get_snapshot, verify as verify_snapshot, load as load_snapshot  # noqa: F401


def get_layer(db, layer_id):
    with db.connect() as con:
        d = _layer(con, layer_id)
        sch = current_schema(con, layer_id)
    d["schema"] = sch
    d["n_events"] = d["next_seq"] - 1
    return d


def materialise(app, snapshot_id, version_id, by, columns=None, reason=""):
    from .materialise_snapshot import materialise as _m

    return _m(app, snapshot_id, version_id, by, columns=columns, reason=reason)
