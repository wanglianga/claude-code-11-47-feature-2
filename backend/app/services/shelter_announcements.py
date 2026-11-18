"""② 公告接入模块：按证据截止点取每条公告的最新可见修订。

公告是追加式的：同一 announcement_id 可有多条修订，按 received_at <= cutoff
取 revision_no 最大者。迟到公告（received_at 晚于某报告的证据截止点）只会
在此后生成的报告中出现，旧报告保持原样。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from .shelter_common import add_source_doc, fingerprint, get_or_create_module_version


def load_announcements(conn, cutoff: datetime) -> dict[str, Any]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT DISTINCT ON (announcement_id) *
            FROM shelter_announcements
            WHERE received_at <= %s
            ORDER BY announcement_id, revision_no DESC
            """,
            (cutoff,),
        )
        rows = cur.fetchall()

    items = [dict(r) for r in rows]
    fp = fingerprint(
        [
            {
                "announcement_id": a["announcement_id"],
                "revision_no": a["revision_no"],
                "kind": a["kind"],
                "site_name": a["site_name"],
                "address": a["address"],
                "geom": [a["geom_x"], a["geom_y"]],
                "unit_raw": a["unit_raw"],
                "capacity_value": a["capacity_value"],
                "occupancy_value": a["occupancy_value"],
                "occupancy_known": a["occupancy_known"],
                "verified_at": a["verified_at"],
                "proof_expires_at": a["proof_expires_at"],
                "open_from": a["open_from"],
                "open_to": a["open_to"],
                "effective_at": a["effective_at"],
            }
            for a in items
        ]
    )
    version = get_or_create_module_version(
        conn, "announcements", fp, cutoff,
        detail={"announcement_count": len(items),
                "by_kind": _count_by_kind(items)},
    )
    return {"items": items, "version": version, "fingerprint": fp}


def _count_by_kind(items: list[dict]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for a in items:
        counts[a["kind"]] = counts.get(a["kind"], 0) + 1
    return counts


def record_announcement(
    conn,
    *,
    announcement_id: str,
    kind: str,
    site_name: str,
    address: Optional[str] = None,
    geom_x: Optional[float] = None,
    geom_y: Optional[float] = None,
    unit_raw: Optional[str] = None,
    capacity_value: Optional[float] = None,
    occupancy_value: Optional[float] = None,
    occupancy_known: bool = True,
    verified_at: Optional[datetime] = None,
    proof_expires_at: Optional[datetime] = None,
    open_from: Optional[datetime] = None,
    open_to: Optional[datetime] = None,
    effective_at: Optional[datetime] = None,
    note: Optional[str] = None,
    title: Optional[str] = None,
    raw_text: str = "",
    received_at: Optional[datetime] = None,
    source_occurred_at: Optional[datetime] = None,
) -> dict:
    """接入一条公告修订。received_at 缺省为数据库当前时刻（迟到补传可显式指定）。"""
    with conn.cursor() as cur:
        doc_id = add_source_doc(
            cur,
            dataset_type="shelter",
            title=title or f"安置点公告 {announcement_id}（{kind}）",
            unit_note=unit_raw or "容量单位见原文",
            occurred=source_occurred_at or verified_at or open_from,
            cutoff=received_at or datetime.now().astimezone(),
            raw=raw_text or f"{site_name} {kind} 公告",
        )
        cur.execute(
            "SELECT COALESCE(MAX(revision_no),0)+1 AS next_rev "
            "FROM shelter_announcements WHERE announcement_id=%s",
            (announcement_id,),
        )
        next_rev = cur.fetchone()["next_rev"]
        cur.execute(
            """INSERT INTO shelter_announcements
                   (announcement_id, revision_no, kind, site_name, address,
                    geom_x, geom_y, unit_raw, capacity_value, occupancy_value,
                    occupancy_known, verified_at, proof_expires_at, open_from,
                    open_to, effective_at, note, source_doc_id, received_at)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                       COALESCE(%s, now()))
               RETURNING *""",
            (
                announcement_id, next_rev, kind, site_name, address,
                geom_x, geom_y, unit_raw, capacity_value, occupancy_value,
                occupancy_known, verified_at, proof_expires_at, open_from,
                open_to, effective_at, note, doc_id, received_at,
            ),
        )
        row = cur.fetchone()
        conn.commit()
    return dict(row)
