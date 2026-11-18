"""安置点实体解析与容量证据核验测试。

验收样例：同名异址、床位/人数单位混用、占用未知、撤销公告迟到补传、
场所容量低于可达需求、证明过期、到达时尚未开放、实体匹配冲突。
"""
from datetime import date, datetime, timezone

import pytest

from app.seed import C1, T0
from app.services.compute import manager
from app.services.shelter_capacity import (
    compare_with_demand,
    convert_to_persons,
    normalize_unit,
)
from app.services.shelter_report import build_scenario_report

AS_OF = date(2026, 6, 1)
EVAL = T0  # 情景时点 = 出发时刻 08:00


def _task(conn, *, name="情报任务", minutes=30):
    return manager.get_or_create(
        conn, name=name, population_as_of=AS_OF, departure_time=T0,
        max_trip_minutes=minutes, road_cutoff=C1, viewer_id="intel-test",
    )


def _sites_by_key(out):
    return {s["site_key"]: s for s in out["sites"]}


# ------------------------------------------------------------------ 单位 --
def test_unit_normalization_and_conversion():
    assert normalize_unit("床位") == "bed"
    assert normalize_unit("人") == "person"
    assert normalize_unit("间") == "room"
    assert normalize_unit("未知单位") is None
    # 床位按 1:1 应急口径换算；房间数无法换算 → None
    assert convert_to_persons(400, "bed") == 400.0
    assert convert_to_persons(300, "person") == 300.0
    assert convert_to_persons(40, "room") is None


# ------------------------------------------------------------- 主流程 --
def test_report_separates_demand_and_capacity(fresh_db):
    """需求可达人口与场所容量是两个独立指标，NULL 不混算。"""
    conn = fresh_db
    task = _task(conn)
    out = build_scenario_report(conn, task_id=task["task_id"], policy="auto")
    rep = out["report"]
    summary = rep["summary_json"]

    # 需求侧只引用冻结覆盖汇总中的可达人口
    with conn.cursor() as cur:
        cur.execute(
            "SELECT covered_population FROM coverage_summaries WHERE task_id=%s",
            (task["task_id"],),
        )
        frozen_covered = float(cur.fetchone()["covered_population"])
    assert rep["reachable_demand_population"] == frozen_covered
    assert summary["demand_side"]["reachable_demand_population"] == frozen_covered
    assert summary["demand_side"]["source"] == "frozen_coverage_summary.covered_population"

    # 场所侧容量独立汇总（只含 included_in_total 的场所）
    counted_low = sum(
        s["available_low"] for s in out["sites"] if s["included_in_total"]
    )
    assert abs(rep["capacity_low_total"] - counted_low) < 1e-6
    # 没有“已服务人数”之类字段被偷偷产出
    assert "served_population" not in rep
    assert "assigned_population" not in summary


def test_bed_and_person_units_are_converted_without_mixing(fresh_db):
    conn = fresh_db
    task = _task(conn)
    out = build_scenario_report(conn, task_id=task["task_id"], policy="auto")
    sites = _sites_by_key(out)

    # M_S2A 原文为 400 床位，按 1:1 换算 400 人，占用 100 → 可用 300
    s2a = sites["M_S2A"]
    assert s2a["capacity_unit_raw"] == "床"
    assert s2a["capacity_unit"] == "bed"
    assert s2a["capacity_persons"] == 400.0
    assert s2a["available_low"] == 300.0 and s2a["available_high"] == 300.0

    # M_S1 原文为人，800 - 200 = 600
    s1 = sites["M_S1"]
    assert s1["capacity_unit"] == "person"
    assert s1["available_low"] == 600.0


def test_unknown_occupancy_gives_uncertain_upper_bound(fresh_db):
    conn = fresh_db
    out = build_scenario_report(conn, task_id=_task(conn)["task_id"], policy="auto")
    s5 = _sites_by_key(out)["M_S5"]
    assert s5["status"] == "upper_bound_uncertain"
    assert s5["occupancy_known"] is False
    assert s5["available_low"] == 0.0
    assert s5["available_high"] == 400.0
    # 上限不确定仍计入总量（下界 0、上界 400），但不会被当成确定可用
    assert s5["included_in_total"] is True


def test_unconvertible_unit_is_capacity_unknown(fresh_db):
    conn = fresh_db
    out = build_scenario_report(conn, task_id=_task(conn)["task_id"], policy="auto")
    s8 = _sites_by_key(out)["M_S8"]
    assert s8["capacity_unit"] == "room"
    assert s8["capacity_persons"] is None
    assert s8["status"] == "capacity_unknown"
    assert s8["included_in_total"] is False
    assert s8["available_low"] is None and s8["available_high"] is None


def test_expired_proof_is_capacity_unknown(fresh_db):
    conn = fresh_db
    out = build_scenario_report(conn, task_id=_task(conn)["task_id"], policy="auto")
    s6 = _sites_by_key(out)["M_S6"]
    assert s6["proof_status"] == "expired"
    assert s6["status"] == "capacity_unknown"
    assert s6["included_in_total"] is False


def test_not_yet_open_site_excluded_from_capacity(fresh_db):
    conn = fresh_db
    out = build_scenario_report(conn, task_id=_task(conn)["task_id"], policy="auto")
    sites = _sites_by_key(out)
    s3 = sites["M_S3"]
    assert s3["open_status"] == "not_yet_open"   # 09:00 才开，08:00 评价
    assert s3["status"] == "not_open"
    assert s3["available_low"] == 0.0 and s3["included_in_total"] is False
    # 到评价时点已开放的新场所不受影响
    assert sites["M_S4"]["status"] == "ok"


def test_same_name_different_address_stays_pending(fresh_db):
    """同名异址：仅名称的歧义公告在两种策略下都必须待核，绝不猜测合并。"""
    conn = fresh_db
    task = _task(conn)
    auto = _sites_by_key(build_scenario_report(conn, task_id=task["task_id"], policy="auto"))
    pending = auto["PENDING:ANN-AMB-S2"]
    assert pending["entity_status"] == "entity_pending"
    assert pending["status"] == "entity_pending"
    assert pending["candidate_count"] == 2
    assert pending["included_in_total"] is False
    cands = {c["master_id"] for c in pending["candidates_json"]}
    assert cands == {"M_S2A", "M_S2B"}
    # 有地址+坐标的两条容量公告在 auto 下被唯一解析
    assert auto["M_S2A"]["entity_status"] == "resolved"
    assert auto["M_S2B"]["entity_status"] == "resolved"


def test_manual_policy_keeps_all_multicandidate_for_confirmation(fresh_db):
    conn = fresh_db
    task = _task(conn)
    manual = build_scenario_report(conn, task_id=task["task_id"], policy="manual")
    by_key = _sites_by_key(manual)
    # 两条南门学校容量公告在 manual 下也待核（各自都有 2 个候选）
    pending_keys = {k for k, v in by_key.items() if v["entity_status"] == "entity_pending"}
    assert "PENDING:ANN-CAP-S2A" in pending_keys
    assert "PENDING:ANN-CAP-S2B" in pending_keys
    assert "PENDING:ANN-AMB-S2" in pending_keys
    # manual 的计入容量少于 auto（更多场所暂时不可用）
    auto = build_scenario_report(conn, task_id=task["task_id"], policy="auto")
    assert manual["report"]["capacity_high_total"] < auto["report"]["capacity_high_total"]


def test_late_revoke_produces_new_report_and_lists_affected(fresh_db):
    """撤销公告迟到补传：旧截止点报告不变，新截止点报告列出受影响场所。"""
    conn = fresh_db
    task = _task(conn)
    early = build_scenario_report(conn, task_id=task["task_id"], policy="auto")
    assert _sites_by_key(early)["M_S4"]["status"] == "ok"

    # 截止点推进到撤销公告到达之后（09:30 之后）
    late = build_scenario_report(
        conn,
        task_id=task["task_id"],
        policy="auto",
        evidence_cutoff=datetime(2026, 7, 15, 10, tzinfo=timezone.utc),
    )
    sites = _sites_by_key(late)
    assert sites["M_S4"]["open_status"] == "revoked"
    assert sites["M_S4"]["status"] == "not_open"
    affected = {a["site_key"] for a in late["report"]["affected_sites"]}
    assert "M_S4" in affected

    # 两份报告同时存在，旧报告未被改写
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) AS n FROM shelter_scenario_reports")
        assert cur.fetchone()["n"] == 2
    old = build_scenario_report(conn, task_id=task["task_id"], policy="auto")
    # 相同内容幂等 → 复用旧报告（不新增第三份）
    assert old["report"]["report_id"] == early["report"]["report_id"]
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) AS n FROM shelter_scenario_reports")
        assert cur.fetchone()["n"] == 2


def test_capacity_below_reachable_demand_is_shortfall(fresh_db):
    """场所容量低于可达需求总量：如实给出短缺比较，不排除任何网格、不分配。"""
    conn = fresh_db
    task = _task(conn, minutes=60)
    out = build_scenario_report(conn, task_id=task["task_id"], policy="auto")
    rep = out["report"]
    demand = rep["reachable_demand_population"]
    assert demand > 0
    assert rep["capacity_high_total"] < demand
    assert rep["comparison"] == "capacity_shortfall"

    # 网格覆盖结果不受场所容量影响：同一冻结任务仍有 covered/uncovered/unknown
    with conn.cursor() as cur:
        cur.execute(
            "SELECT DISTINCT status FROM grid_share_results WHERE task_id=%s",
            (task["task_id"],),
        )
        statuses = {r["status"] for r in cur.fetchall()}
    assert {"covered", "uncovered", "unknown"} <= statuses
    # 容量短缺没有把网格删掉或标成 unknown
    with conn.cursor() as cur:
        cur.execute(
            "SELECT count(DISTINCT grid_id) AS n FROM grid_share_results WHERE task_id=%s",
            (task["task_id"],),
        )
        assert cur.fetchone()["n"] >= 8


def test_compare_with_demand_states():
    assert compare_with_demand(10, 20, 30) == "capacity_shortfall"
    assert compare_with_demand(40, 50, 30) == "capacity_meets_demand_lower_bound"
    assert compare_with_demand(20, 40, 30) == "inconclusive_between_bounds"


def test_five_module_versions_are_independent(fresh_db):
    conn = fresh_db
    task = _task(conn)
    build_scenario_report(conn, task_id=task["task_id"], policy="auto")
    with conn.cursor() as cur:
        cur.execute(
            "SELECT module, count(*) AS n FROM shelter_module_versions GROUP BY module"
        )
        by_module = {r["module"]: r["n"] for r in cur.fetchall()}
    for module in ("catalog", "announcements", "entity_resolution",
                   "capacity_verification", "scenario_report"):
        assert by_module.get(module, 0) >= 1


def test_entity_override_changes_only_subsequent_reports(fresh_db):
    """人工确认歧义公告只影响其后的报告；旧报告不可改写。"""
    conn = fresh_db
    task = _task(conn)
    before = build_scenario_report(conn, task_id=task["task_id"], policy="auto")
    assert "PENDING:ANN-AMB-S2" in _sites_by_key(before)

    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO shelter_entity_overrides
                   (announcement_id, master_id, action, note)
               VALUES ('ANN-AMB-S2','M_S2B','confirm','现场核对为 88 号校区')"""
        )
    conn.commit()

    after = build_scenario_report(conn, task_id=task["task_id"], policy="auto")
    keys = _sites_by_key(after)
    assert "PENDING:ANN-AMB-S2" not in keys
    # 确认的公告并入 M_S2B
    assert "ANN-AMB-S2" in keys["M_S2B"]["announcement_ids"]

    # 旧报告仍保持待核，未被原地改写
    with conn.cursor() as cur:
        cur.execute(
            """SELECT count(*) AS n FROM shelter_scenario_site_results
               WHERE report_id=%s AND site_key='PENDING:ANN-AMB-S2'""",
            (before["report"]["report_id"],),
        )
        assert cur.fetchone()["n"] == 1


def test_coordinate_revision_is_visible_at_cutoff(fresh_db):
    conn = fresh_db
    from app.services.shelter_catalog import load_catalog
    cat = load_catalog(conn, C1)
    s1 = cat["sites"]["M_S1"]
    assert s1["x"] == 2000.0 and s1["y"] == 0.0     # 已应用坐标修订
    assert s1["coordinate_revision"] == 1
    # 截止点早于坐标修订到达时，应看到原始坐标
    early = load_catalog(conn, datetime(2026, 7, 5, tzinfo=timezone.utc))
    assert early["sites"]["M_S1"]["x"] == 1990.0
    assert early["sites"]["M_S1"]["coordinate_revision"] == 0
