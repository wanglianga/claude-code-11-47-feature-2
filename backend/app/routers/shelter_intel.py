"""安置点实体解析与容量证据核验接口。

纪律：需求侧（可达人口）与场所侧（可用容量）分开展示；接口不提供任何
“分配居民 / 网格→场所排序 / 逐格扣减容量”的入口，也不把容量差算成
已服务人数。五条职责的版本可单独查询。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from ..schemas_api import AnnouncementCreate, EntityOverrideCreate, ShelterReportRequest
from ..services.shelter_announcements import load_announcements, record_announcement
from ..services.shelter_catalog import load_catalog
from ..services.shelter_common import MODULES
from ..services.shelter_report import build_scenario_report
from ..services.shelter_resolution import load_overrides, resolve_entities
from .deps import get_db, jsonable

router = APIRouter(prefix="/shelter-intel", tags=["安置点情报"])


@router.get("/catalog")
def catalog(cutoff: str, conn=Depends(get_db)):
    """截止点可见的场所目录（含坐标修订后的坐标与来源）。"""
    catalog_data = load_catalog(conn, _parse_dt(cutoff))
    return jsonable({
        "sites": list(catalog_data["sites"].values()),
        "version": catalog_data["version"],
    })


@router.get("/announcements")
def announcements(cutoff: str, conn=Depends(get_db)):
    """截止点可见的每条公告最新修订（迟到公告按 received_at 过滤）。"""
    data = load_announcements(conn, _parse_dt(cutoff))
    return jsonable({"announcements": data["items"], "version": data["version"]})


@router.post("/announcements")
def create_announcement(payload: AnnouncementCreate, conn=Depends(get_db)):
    """接入一条公告（可模拟迟到补传：指定早于生效时刻的 received_at）。"""
    row = record_announcement(
        conn,
        announcement_id=payload.announcement_id,
        kind=payload.kind,
        site_name=payload.site_name,
        address=payload.address,
        geom_x=payload.geom_x,
        geom_y=payload.geom_y,
        unit_raw=payload.unit_raw,
        capacity_value=payload.capacity_value,
        occupancy_value=payload.occupancy_value,
        occupancy_known=payload.occupancy_known,
        verified_at=payload.verified_at,
        proof_expires_at=payload.proof_expires_at,
        open_from=payload.open_from,
        open_to=payload.open_to,
        effective_at=payload.effective_at,
        note=payload.note,
        title=payload.title,
        raw_text=payload.raw_text,
        received_at=payload.received_at,
        source_occurred_at=payload.source_occurred_at,
    )
    return jsonable({"announcement": row})


@router.get("/entities")
def entities(cutoff: str, policy: str = Query("auto", pattern="^(auto|manual)$"),
             conn=Depends(get_db)):
    """实体消歧结果（不影响冻结覆盖结果）。"""
    cut = _parse_dt(cutoff)
    cat = load_catalog(conn, cut)
    anns = load_announcements(conn, cut)
    res = resolve_entities(conn, cutoff=cut, catalog=cat, announcements=anns, policy=policy)
    return jsonable({
        "policy": policy,
        "resolutions": list(res["resolutions"].values()),
        "version": res["version"],
    })


@router.post("/entities/override")
def override_entity(payload: EntityOverrideCreate, conn=Depends(get_db)):
    """人工确认/驳回歧义公告；只影响其后的报告。"""
    if payload.action == "confirm" and not payload.master_id:
        raise HTTPException(400, "confirm 必须提供 master_id")
    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO shelter_entity_overrides
                   (announcement_id, master_id, action, note, created_by)
               VALUES (%s,%s,%s,%s,%s) RETURNING *""",
            (payload.announcement_id,
             payload.master_id if payload.action == "confirm" else None,
             payload.action, payload.note, payload.created_by),
        )
        row = cur.fetchone()
    conn.commit()
    return jsonable({"override": row})


@router.get("/overrides")
def overrides(cutoff: str, conn=Depends(get_db)):
    return jsonable({"overrides": list(load_overrides(conn, _parse_dt(cutoff)).values())})


@router.post("/reports")
def create_report(payload: ShelterReportRequest, conn=Depends(get_db)):
    """生成情景报告：需求侧可达人口 + 场所侧容量区间（两个独立指标）。"""
    try:
        result = build_scenario_report(
            conn,
            task_id=payload.task_id,
            policy=payload.policy,
            evidence_cutoff=payload.evidence_cutoff,
            evaluation_time=payload.evaluation_time,
            created_by=payload.created_by,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return jsonable(result)


@router.get("/reports")
def list_reports(task_id: str | None = None, conn=Depends(get_db)):
    with conn.cursor() as cur:
        if task_id:
            cur.execute(
                """SELECT report_id, signature, task_id, policy, evaluation_time,
                          evidence_cutoff_at, reachable_demand_population,
                          capacity_low_total, capacity_high_total, comparison,
                          previous_report_id, created_at
                   FROM shelter_scenario_reports WHERE task_id=%s
                   ORDER BY created_at DESC""",
                (task_id,),
            )
        else:
            cur.execute(
                """SELECT report_id, signature, task_id, policy, evaluation_time,
                          evidence_cutoff_at, reachable_demand_population,
                          capacity_low_total, capacity_high_total, comparison,
                          previous_report_id, created_at
                   FROM shelter_scenario_reports ORDER BY created_at DESC LIMIT 200"""
            )
        rows = cur.fetchall()
    return jsonable({"reports": rows})


@router.get("/reports/{report_id}")
def get_report(report_id: str, conn=Depends(get_db)):
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM shelter_scenario_reports WHERE report_id=%s", (report_id,))
        report = cur.fetchone()
        if report is None:
            raise HTTPException(404, "报告不存在")
        cur.execute(
            "SELECT * FROM shelter_scenario_site_results WHERE report_id=%s "
            "ORDER BY entity_status, site_key",
            (report_id,),
        )
        sites = cur.fetchall()
    return jsonable({"report": report, "sites": sites})


@router.get("/versions")
def versions(module: str | None = None, conn=Depends(get_db)):
    """五条职责各自的版本流。"""
    if module is not None and module not in MODULES:
        raise HTTPException(400, f"module 必须是 {MODULES} 之一")
    with conn.cursor() as cur:
        if module:
            cur.execute(
                "SELECT * FROM shelter_module_versions WHERE module=%s ORDER BY version_id",
                (module,),
            )
        else:
            cur.execute("SELECT * FROM shelter_module_versions ORDER BY module, version_id")
        rows = cur.fetchall()
    return jsonable({"versions": rows})


def _parse_dt(value: str):
    from datetime import datetime

    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise HTTPException(400, f"无法解析时间：{value!r}")
