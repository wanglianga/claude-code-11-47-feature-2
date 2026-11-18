"""① 场所目录模块：主档 + 坐标修订，按证据截止点物化为带来源的场所实体。

版本化：目录内容（每个场所在截止点可见的名称/地址/坐标/节点）取内容指纹，
内容不变复用同一版本，坐标修订或主档变更只追加新版本。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from .shelter_common import fingerprint, get_or_create_module_version


def load_catalog(conn, cutoff: datetime) -> dict[str, Any]:
    """返回截止点可见的场所目录（含坐标修订后的最终坐标与来源列表）。"""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT * FROM shelter_masters WHERE received_at <= %s AND active = TRUE "
            "ORDER BY master_id",
            (cutoff,),
        )
        masters = cur.fetchall()
        cur.execute(
            """
            SELECT DISTINCT ON (master_id) master_id, revision_no, geom_x, geom_y,
                   change_note, source_doc_id, received_at
            FROM shelter_coordinate_revisions
            WHERE received_at <= %s
            ORDER BY master_id, revision_no DESC
            """,
            (cutoff,),
        )
        coord_rows = cur.fetchall()

    revisions = {r["master_id"]: r for r in coord_rows}
    sites: dict[str, dict] = {}
    for m in masters:
        rev = revisions.get(m["master_id"])
        x = float(rev["geom_x"]) if rev else float(m["geom_x"])
        y = float(rev["geom_y"]) if rev else float(m["geom_y"])
        sites[m["master_id"]] = {
            "master_id": m["master_id"],
            "name": m["name"],
            "address": m["address"],
            "x": x,
            "y": y,
            "node_id": m["node_id"],
            "shelter_id": m["shelter_id"],
            "source_doc_id": m["source_doc_id"],
            "coordinate_revision": rev["revision_no"] if rev else 0,
            "coordinate_source_doc_id": rev["source_doc_id"] if rev else m["source_doc_id"],
            "coordinate_changed": bool(rev and rev["revision_no"] > 0),
        }

    fp = fingerprint(
        [
            {k: s[k] for k in ("master_id", "name", "address", "x", "y",
                               "node_id", "shelter_id", "coordinate_revision")}
            for s in sites.values()
        ]
    )
    version = get_or_create_module_version(
        conn, "catalog", fp, cutoff,
        detail={"site_count": len(sites), "coordinate_revisioned": sum(
            1 for s in sites.values() if s["coordinate_changed"])},
    )
    return {"sites": sites, "version": version, "fingerprint": fp}
