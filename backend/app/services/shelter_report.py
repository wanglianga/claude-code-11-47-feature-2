"""⑤ 情景报告模块：把可达需求与场所可用容量分开展示，绝不做分配。

核心不变量（对应需求）：
- 需求侧 ``reachable_demand_population`` 只来自冻结覆盖任务的网格级去重合计，
  本模块不重新计算、不改变覆盖结果，也不把容量差推成“已服务人数”；
- 场所侧 ``capacity_low/high`` 只来自容量核验模块，逐个场所独立报告；
- 两侧只在报告层面并列与比较（capacity_shortfall / meets / inconclusive），
  不决定网格→场所先后次序、不逐格扣减容量；
- 报告按内容签名去重；迟到公告产生新签名 → 新报告，并列出受影响场所与
  与上一版的差异。旧报告不可改写。
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Optional

from psycopg.types.json import Json

from .shelter_announcements import load_announcements
from .shelter_capacity import (
    build_capacity_version,
    compare_with_demand,
    evaluate_site,
    totals,
)
from .shelter_catalog import load_catalog
from .shelter_common import fingerprint, get_or_create_module_version
from .shelter_resolution import resolve_entities


def _task_context(conn, task_id: str) -> dict:
    with conn.cursor() as cur:
        cur.execute(
            """SELECT t.task_id, t.scenario_id, t.evidence_cutoff_at,
                      s.departure_time, s.road_cutoff, s.name AS scenario_name,
                      s.population_as_of, s.max_trip_minutes
               FROM compute_tasks t JOIN scenarios s ON s.scenario_id=t.scenario_id
               WHERE t.task_id=%s""",
            (task_id,),
        )
        task = cur.fetchone()
        cur.execute("SELECT * FROM coverage_summaries WHERE task_id=%s", (task_id,))
        summary = cur.fetchone()
    return {"task": task, "summary": summary}


def build_scenario_report(
    conn,
    *,
    task_id: str,
    policy: str = "auto",
    evidence_cutoff: Optional[datetime] = None,
    evaluation_time: Optional[datetime] = None,
    created_by: str = "analyst",
) -> dict[str, Any]:
    if policy not in ("auto", "manual"):
        raise ValueError(f"unknown entity policy: {policy}")
    ctx = _task_context(conn, task_id)
    task = ctx["task"]
    if task is None:
        raise ValueError("任务不存在")
    if ctx["summary"] is None:
        raise ValueError("该任务尚无覆盖汇总，无法生成场所情景报告")

    cutoff = evidence_cutoff or task["evidence_cutoff_at"]
    eval_time = evaluation_time or task["departure_time"]

    catalog = load_catalog(conn, cutoff)
    announcements = load_announcements(conn, cutoff)
    entities = resolve_entities(
        conn, cutoff=cutoff, catalog=catalog, announcements=announcements, policy=policy
    )

    # 冻结基线中的既有安置点（只读，用于开放区间证据，不修改冻结语义）
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM shelters ORDER BY shelter_id")
        shelter_rows = cur.fetchall()
        cur.execute("SELECT * FROM shelter_open_windows ORDER BY shelter_id, open_from")
        window_rows = cur.fetchall()
    frozen = _frozen_shelters(shelter_rows, window_rows)

    # 按场所分组公告
    by_master: dict[str, list[dict]] = {}
    pending_anns: dict[str, list[dict]] = {}
    for ann in announcements["items"]:
        res = entities["resolutions"][ann["announcement_id"]]
        if res["entity_status"] == "rejected":
            continue
        if res["entity_status"] == "resolved":
            by_master.setdefault(res["master_id"], []).append(ann)
        else:
            pending_anns.setdefault(ann["announcement_id"], []).append(ann)

    site_results: list[dict] = []

    # 1) 已解析场所：目录中每个场所都出一行（无公告也会标容量未知）
    for master_id, master in sorted(catalog["sites"].items()):
        anns = by_master.get(master_id, [])
        res = _resolution_for_master(entities, master_id)
        fshelter = frozen.get(master.get("shelter_id")) if master.get("shelter_id") else None
        entity = ({**res, "display_name": master["name"]} if res else
                  {"entity_status": "resolved", "match_method": "catalog_only",
                   "candidates": [], "display_name": master["name"]})
        result = evaluate_site(
            master=master, site_key=master_id, announcements=anns,
            entity=entity, policy=policy, evaluation_time=eval_time,
            frozen_shelter=fshelter,
        )
        site_results.append(result)

    # 2) 实体待核公告：每个公告一行，独立待核，不并入任何场所
    for ann_id, anns in sorted(pending_anns.items()):
        res = entities["resolutions"][ann_id]
        primary = anns[0]
        result = evaluate_site(
            master=None, site_key=f"PENDING:{ann_id}", announcements=anns,
            entity={**res, "display_name": primary["site_name"],
                    "address": primary.get("address"),
                    "geom_x": primary.get("geom_x"), "geom_y": primary.get("geom_y")},
            policy=policy, evaluation_time=eval_time, frozen_shelter=None,
        )
        site_results.append(result)

    cap_version = build_capacity_version(conn, cutoff, site_results)
    agg = totals(site_results)

    # 需求侧：只读冻结覆盖汇总中的可达（去重后）人口
    demand = float(ctx["summary"]["covered_population"])
    comparison = compare_with_demand(
        agg["capacity_low_total"], agg["capacity_high_total"], demand
    )

    summary_json = {
        "demand_side": {
            "reachable_demand_population": round(demand, 3),
            "source": "frozen_coverage_summary.covered_population",
            "frozen_task_evidence_cutoff_at": str(task["evidence_cutoff_at"]),
            "total_population": float(ctx["summary"]["total_population"]),
            "uncovered_population": float(ctx["summary"]["uncovered_population"]),
            "unknown_population": float(ctx["summary"]["unknown_population"]),
            "note": "仅报告按冻结人口基线在旧指标口径下的可达需求人口；不做任何分配",
        },
        "capacity_side": {
            **agg,
            "note": "仅报告各场所可用容量区间之和；容量不是已服务人数，也未与需求逐格配对",
        },
        "comparison": comparison,
        "policy": policy,
        "evaluation_time": eval_time.isoformat(),
        "shelter_evidence_cutoff_at": cutoff.isoformat(),
        "non_allocation_invariants": [
            "不分配居民到任何场所",
            "不确定网格→场所的先后次序",
            "不逐格扣减容量",
            "未分配容量不被推算为已服务人数",
        ],
        "status_counts": _status_counts(site_results),
    }

    sig = fingerprint({
        "task_id": str(task_id),
        "policy": policy,
        "eval": eval_time.isoformat(),
        "cutoff": cutoff.isoformat(),
        "catalog": catalog["fingerprint"],
        "announcements": announcements["fingerprint"],
        "entity": entities["fingerprint"],
        "capacity": cap_version["fingerprint"],
        "sites": [
            (r["site_key"], r["status"], r["available_low"], r["available_high"])
            for r in site_results
        ],
        "demand": demand,
    })

    # 幂等：相同内容复用既有报告
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM shelter_scenario_reports WHERE signature=%s", (sig,))
        existing = cur.fetchone()
    if existing is not None:
        return _load_report(conn, existing)
    conn.commit()

    previous = _previous_report(conn, task_id, policy, sig)
    affected = _diff_affected(conn, previous, site_results) if previous else []

    report_version = get_or_create_module_version(
        conn, "scenario_report", sig, cutoff,
        detail={"policy": policy, "task_id": str(task_id), "comparison": comparison},
    )

    report_id = uuid.uuid4()
    # 报告头与站点行同事务写入：要么整份报告可见，要么完全不可见
    conn.autocommit = False
    try:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO shelter_scenario_reports
                   (report_id, signature, task_id, policy, evaluation_time,
                    evidence_cutoff_at, version_catalog, version_announcements,
                    version_entity, version_capacity, version_report,
                    previous_report_id, reachable_demand_population,
                    capacity_low_total, capacity_high_total, comparison,
                    affected_sites, summary_json, created_by)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (
                    report_id, sig, task_id, policy, eval_time, cutoff,
                    catalog["version"]["version_id"], announcements["version"]["version_id"],
                    entities["version"]["version_id"], cap_version["version_id"],
                    report_version["version_id"],
                    previous["report_id"] if previous else None,
                    demand, agg["capacity_low_total"], agg["capacity_high_total"],
                    comparison, Json(affected), Json(_jsonable(summary_json)), created_by,
                ),
            )
            cur.executemany(
                """INSERT INTO shelter_scenario_site_results
                       (report_id, site_key, master_id, display_name, address, geom_x,
                        geom_y, entity_status, entity_policy, match_method,
                        candidate_count, candidates_json, announcement_ids,
                        capacity_unit_raw, capacity_unit, capacity_value,
                        capacity_persons, occupancy_known, occupancy_value,
                        available_low, available_high, open_status, open_from, open_to,
                        proof_status, verified_at, status, status_reason,
                        included_in_total, evidence_sources)
                   VALUES (%(report_id)s,%(site_key)s,%(master_id)s,%(display_name)s,
                           %(address)s,%(geom_x)s,%(geom_y)s,%(entity_status)s,
                           %(entity_policy)s,%(match_method)s,%(candidate_count)s,
                           %(candidates_json)s,%(announcement_ids)s,%(capacity_unit_raw)s,
                           %(capacity_unit)s,%(capacity_value)s,%(capacity_persons)s,
                           %(occupancy_known)s,%(occupancy_value)s,%(available_low)s,
                           %(available_high)s,%(open_status)s,%(open_from)s,%(open_to)s,
                           %(proof_status)s,%(verified_at)s,%(status)s,%(status_reason)s,
                           %(included_in_total)s,%(evidence_sources)s)""",
                [
                    {**r, "report_id": report_id,
                     "candidates_json": Json(r["candidates_json"]),
                     "announcement_ids": Json(r["announcement_ids"]),
                     "evidence_sources": Json(_jsonable(r["evidence_sources"]))}
                    for r in site_results
                ],
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.autocommit = True

    with conn.cursor() as cur:
        cur.execute("SELECT * FROM shelter_scenario_reports WHERE report_id=%s", (report_id,))
        row = cur.fetchone()
    return _load_report(conn, row)


# --------------------------------------------------------------- 内部工具 --
def _frozen_shelters(shelter_rows, window_rows) -> dict[str, Any]:
    from ..engine.models import OpenWindow, Shelter
    windows: dict[str, list[OpenWindow]] = {}
    for w in window_rows:
        windows.setdefault(w["shelter_id"], []).append(
            OpenWindow(open_from=w["open_from"], open_to=w["open_to"])
        )
    return {
        s["shelter_id"]: Shelter(
            shelter_id=s["shelter_id"], name=s["name"], node_id=s["node_id"],
            x=float(s["geom_x"]), y=float(s["geom_y"]),
            windows=tuple(windows.get(s["shelter_id"], [])),
        )
        for s in shelter_rows
    }


def _resolution_for_master(entities: dict, master_id: str) -> Optional[dict]:
    for r in entities["resolutions"].values():
        if r["entity_status"] == "resolved" and r["master_id"] == master_id:
            return r
    return None


def _status_counts(site_results: list[dict]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for r in site_results:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    return counts


def _previous_report(conn, task_id: str, policy: str, signature: str) -> Optional[dict]:
    with conn.cursor() as cur:
        cur.execute(
            """SELECT * FROM shelter_scenario_reports
               WHERE task_id=%s AND policy=%s AND signature<>%s
               ORDER BY created_at DESC LIMIT 1""",
            (task_id, policy, signature),
        )
        return cur.fetchone()


def _diff_affected(conn, previous: dict, site_results: list[dict]) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT * FROM shelter_scenario_site_results WHERE report_id=%s",
            (previous["report_id"],),
        )
        prev_rows = {r["site_key"]: r for r in cur.fetchall()}
    now_rows = {r["site_key"]: r for r in site_results}
    affected: list[dict] = []
    for key in sorted(set(prev_rows) | set(now_rows)):
        before = prev_rows.get(key)
        after = now_rows.get(key)
        if before is None:
            affected.append({"site_key": key, "change": "added",
                             "status_after": after["status"] if after else None})
        elif after is None:
            affected.append({"site_key": key, "change": "removed",
                             "status_before": before["status"]})
        elif (before["status"] != after["status"]
              or _num(before["available_low"]) != _num(after["available_low"])
              or _num(before["available_high"]) != _num(after["available_high"])):
            affected.append({
                "site_key": key,
                "change": "changed",
                "status_before": before["status"],
                "status_after": after["status"],
                "available_before": [_num(before["available_low"]),
                                     _num(before["available_high"])],
                "available_after": [after["available_low"], after["available_high"]],
            })
    return affected


def _num(v) -> Optional[float]:
    return None if v is None else round(float(v), 3)


def _jsonable(obj):
    if isinstance(obj, dict):
        return {k: _jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_jsonable(v) for v in obj]
    if isinstance(obj, (datetime,)):
        return obj.isoformat()
    return obj


def _load_report(conn, report: dict) -> dict:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT * FROM shelter_scenario_site_results WHERE report_id=%s "
            "ORDER BY entity_status, site_key",
            (report["report_id"],),
        )
        sites = [dict(r) for r in cur.fetchall()]
    return {"report": dict(report), "sites": sites}
