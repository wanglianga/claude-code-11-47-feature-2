"""④ 容量核验模块：逐个场所计算情景时点的可用容量区间与证据状态。

纪律（对应需求）：
- 容量证据只描述“这个场所在情景时点还能接收多少人”，与可达需求彼此独立；
- 单位换算（人 / 床位 / 房间）必须显式：床→人可按既定 1:1 应急口径换算，
  房间数缺少每间人数时 *无法换算*，一律标为容量未知，绝不猜测；
- 现有占用未知 → 可用容量下界 0、上界=核验容量（上限不确定）；
- 核验证明过期 → 容量未知（数字不可信，不沿用旧值）；
- 到达/评价时尚未开放或已撤销 → 未开放（可用容量记 0，不计入可用总量）；
- 实体待核（多候选/冲突/驳回）→ 实体待核，不并入任何场所，也不计入总量。
- 本模块只产出“场所侧”容量，绝不把容量差推成已服务人数。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from ..engine.traversal import is_shelter_open
from ..engine.models import OpenWindow, Shelter
from .shelter_common import fingerprint, get_or_create_module_version

# 单位归一化：原文单位 -> 规范单位
_UNIT_ALIASES = {
    "人": "person", "人位": "person", "个人": "person", "person": "person",
    "persons": "person", "people": "person", "人数": "person",
    "床": "bed", "床位": "bed", "bed": "bed", "beds": "bed", "张床": "bed",
    "间": "room", "房间": "room", "room": "room", "rooms": "room",
}

# 换算到“可接收人数”的系数。缺项 = 无法换算。
_UNIT_TO_PERSONS = {
    "person": 1.0,
    # 应急安置口径：一张床位按 1 人计（明确写入文档的口径，而非隐含假设）
    "bed": 1.0,
}

_UNIT_NOTE = {
    "person": "原文单位即人数，直接采用",
    "bed": "床位按 1 床=1 人应急安置口径换算",
    "room": "仅有房间数，缺少每间可住人数，无法换算为人数",
}


def normalize_unit(raw: Optional[str]) -> Optional[str]:
    if not raw:
        return None
    text = raw.strip().lower()
    return _UNIT_ALIASES.get(text, _UNIT_ALIASES.get(text.rstrip("s")))


def convert_to_persons(value: Optional[float], unit: Optional[str]) -> Optional[float]:
    if value is None or unit is None:
        return None
    factor = _UNIT_TO_PERSONS.get(unit)
    if factor is None:
        return None
    return float(value) * factor


def _latest_by(rows: list[dict], *keys) -> Optional[dict]:
    if not rows:
        return None
    return max(rows, key=lambda r: tuple(
        (r.get(k) is not None, r.get(k)) for k in keys
    ))


def _announcement_sort_key(a: dict) -> tuple:
    return (
        a.get("verified_at") is not None, a.get("verified_at"),
        a.get("received_at"), a.get("revision_no", 0),
    )


def evaluate_site(
    *,
    master: Optional[dict],
    site_key: str,
    announcements: list[dict],
    entity: dict,
    policy: str,
    evaluation_time: datetime,
    frozen_shelter: Optional[Shelter],
) -> dict:
    """计算单个场所的容量区间与证据状态。返回与站点结果表列对齐的 dict。"""
    base = {
        "site_key": site_key,
        "master_id": master["master_id"] if master else None,
        "display_name": (entity.get("display_name") or
                         (master["name"] if master else "（未命名场所）")),
        "address": master["address"] if master else entity.get("address"),
        "geom_x": master["x"] if master else entity.get("geom_x"),
        "geom_y": master["y"] if master else entity.get("geom_y"),
        "entity_status": entity["entity_status"],
        "entity_policy": policy,
        "match_method": entity.get("match_method"),
        "candidates_json": entity.get("candidates", []),
        "candidate_count": len(entity.get("candidates", [])),
        "announcement_ids": [a["announcement_id"] for a in announcements],
        "capacity_unit_raw": None,
        "capacity_unit": None,
        "capacity_value": None,
        "capacity_persons": None,
        "occupancy_known": None,
        "occupancy_value": None,
        "available_low": None,
        "available_high": None,
        "open_status": "unknown",
        "open_from": None,
        "open_to": None,
        "proof_status": "missing",
        "verified_at": None,
        "status": "ok",
        "status_reason": None,
        "included_in_total": False,
        "evidence_sources": [],
    }

    # ---- 实体待核：不并入任何场所、不计入总量 ----
    if entity["entity_status"] == "entity_pending":
        base["status"] = "entity_pending"
        base["status_reason"] = entity.get("reason") or "实体匹配冲突，待人工确认"
        base["included_in_total"] = False
        return base

    # ---- 开放状态（冻结开放区间 + 公告开放/撤销） ----
    open_status, open_from, open_to = _open_status(
        announcements, frozen_shelter, evaluation_time
    )
    base["open_status"] = open_status
    base["open_from"] = open_from
    base["open_to"] = open_to

    # ---- 容量证据：取最新一条 capacity 公告 ----
    cap_rows = [a for a in announcements if a["kind"] == "capacity"]
    cap = _latest_by(cap_rows, "verified_at", "received_at", "revision_no")
    if cap is None:
        base["status"] = "capacity_unknown"
        base["status_reason"] = "没有可见的容量核验公告"
        _attach_sources(base, announcements)
        return base

    unit_raw = cap.get("unit_raw")
    unit = normalize_unit(unit_raw)
    capacity_value = cap.get("capacity_value")
    capacity_persons = convert_to_persons(capacity_value, unit)
    base.update({
        "capacity_unit_raw": unit_raw,
        "capacity_unit": unit,
        "capacity_value": capacity_value,
        "capacity_persons": capacity_persons,
        "verified_at": cap.get("verified_at"),
    })
    _attach_sources(base, announcements)

    # 证明有效期
    expires = cap.get("proof_expires_at")
    if expires is not None and expires <= evaluation_time:
        base["proof_status"] = "expired"
    elif expires is None:
        base["proof_status"] = "missing"
    else:
        base["proof_status"] = "valid"

    # 未开放 / 已撤销：容量数字可以保留作证据，但可用记 0 且不计入总量
    if open_status in ("revoked", "not_open", "not_yet_open", "closed", "no_open_record"):
        base["status"] = "not_open"
        base["status_reason"] = {
            "revoked": "撤销公告已在情景时点前生效，场所不可用",
            "not_yet_open": "情景时点尚未到开放起点",
            "closed": "情景时点已过开放区间",
            "no_open_record": "没有可见的开放记录，无法确认可用",
            "not_open": "情景时点不在任何开放区间内",
        }.get(open_status, "情景时点场所未开放")
        base["available_low"] = 0.0
        base["available_high"] = 0.0
        base["included_in_total"] = False
        return base

    # 单位无法换算
    if unit is None:
        base["status"] = "capacity_unknown"
        base["status_reason"] = f"容量单位无法识别：{unit_raw!r}"
        return base
    if capacity_persons is None:
        base["status"] = "capacity_unknown"
        base["status_reason"] = _UNIT_NOTE.get(unit, "容量单位无法换算为人数")
        return base

    # 证明过期：不沿用旧数字，容量未知
    if base["proof_status"] == "expired":
        base["status"] = "capacity_unknown"
        base["status_reason"] = (
            f"核验证明已于 {expires.isoformat() if expires else '未知时点'} 过期，"
            "容量数字不再可信"
        )
        return base

    # 现有占用
    occupancy_known = bool(cap.get("occupancy_known", True))
    occupancy_value = cap.get("occupancy_value")
    base["occupancy_known"] = occupancy_known
    base["occupancy_value"] = occupancy_value

    if not occupancy_known or occupancy_value is None:
        # 上限不确定：可用区间 [0, 核验容量]
        base["status"] = "upper_bound_uncertain"
        base["status_reason"] = "情景开始时现有占用未知，可用容量只能确定在 [0, 核验容量] 区间"
        base["available_low"] = 0.0
        base["available_high"] = round(max(capacity_persons, 0.0), 3)
        base["included_in_total"] = True
        return base

    available = max(capacity_persons - float(occupancy_value), 0.0)
    base["status"] = "ok"
    base["available_low"] = round(available, 3)
    base["available_high"] = round(available, 3)
    base["included_in_total"] = True
    return base


def _open_status(
    announcements: list[dict],
    frozen_shelter: Optional[Shelter],
    evaluation_time: datetime,
) -> tuple[str, Optional[datetime], Optional[datetime]]:
    """综合撤销公告、公告开放区间与冻结开放区间，判断情景时点开放状态。"""
    # 撤销优先：生效时刻 <= 评价时点 → 已撤销
    revokes = [a for a in announcements if a["kind"] == "revoke"]
    active_revoke = None
    for r in revokes:
        eff = r.get("effective_at") or r.get("received_at")
        if eff is not None and eff <= evaluation_time:
            active_revoke = r
    if active_revoke is not None:
        return "revoked", None, None

    windows: list[OpenWindow] = []
    if frozen_shelter is not None:
        windows.extend(frozen_shelter.windows)
    for a in announcements:
        if a["kind"] == "open" and a.get("open_from") is not None:
            windows.append(OpenWindow(open_from=a["open_from"], open_to=a.get("open_to")))

    if not windows:
        return "no_open_record", None, None

    # 与评价时点相交的区间
    covering = [w for w in windows
                if w.open_from <= evaluation_time and
                (w.open_to is None or evaluation_time < w.open_to)]
    if covering:
        w = covering[0]
        return "open", w.open_from, w.open_to

    # 尚未开放 / 已关闭
    future = [w for w in windows if w.open_from > evaluation_time]
    if future:
        w = min(future, key=lambda w: w.open_from)
        return "not_yet_open", w.open_from, w.open_to
    past = [w for w in windows
            if w.open_to is not None and w.open_to <= evaluation_time]
    if past:
        w = max(past, key=lambda w: w.open_to)
        return "closed", w.open_from, w.open_to
    return "not_open", None, None


def _attach_sources(base: dict, announcements: list[dict]) -> None:
    base["evidence_sources"] = [
        {
            "announcement_id": a["announcement_id"],
            "revision_no": a["revision_no"],
            "kind": a["kind"],
            "source_doc_id": a.get("source_doc_id"),
            "received_at": a.get("received_at"),
        }
        for a in sorted(announcements, key=lambda x: (x["kind"], x["announcement_id"]))
    ]


def build_capacity_version(conn, cutoff: datetime, site_results: list[dict]) -> dict:
    fp = fingerprint(
        [
            {
                "site_key": r["site_key"],
                "status": r["status"],
                "unit": r["capacity_unit"],
                "cap": r["capacity_persons"],
                "occ": r["occupancy_value"],
                "occ_known": r["occupancy_known"],
                "open": r["open_status"],
                "proof": r["proof_status"],
                "low": r["available_low"],
                "high": r["available_high"],
            }
            for r in site_results
        ]
    )
    return get_or_create_module_version(
        conn, "capacity_verification", fp, cutoff,
        detail={
            "site_count": len(site_results),
            "ok": sum(1 for r in site_results if r["status"] == "ok"),
            "capacity_unknown": sum(1 for r in site_results
                                    if r["status"] == "capacity_unknown"),
            "upper_bound_uncertain": sum(1 for r in site_results
                                         if r["status"] == "upper_bound_uncertain"),
            "not_open": sum(1 for r in site_results if r["status"] == "not_open"),
            "entity_pending": sum(1 for r in site_results
                                  if r["status"] == "entity_pending"),
        },
    )


def totals(site_results: list[dict]) -> dict[str, Any]:
    """汇总可用容量区间。只统计 included_in_total 的场所。"""
    low = sum(r["available_low"] or 0.0 for r in site_results if r["included_in_total"])
    high = sum(r["available_high"] or 0.0 for r in site_results if r["included_in_total"])
    known_sites = sum(1 for r in site_results if r["included_in_total"])
    return {
        "capacity_low_total": round(low, 3),
        "capacity_high_total": round(high, 3),
        "counted_site_count": known_sites,
        "excluded_site_count": len(site_results) - known_sites,
    }


def compare_with_demand(low: float, high: float, demand: float) -> str:
    """场所侧容量与需求侧可达人口的比较（仅比较，不分配）。"""
    if high < demand:
        return "capacity_shortfall"
    if low >= demand:
        return "capacity_meets_demand_lower_bound"
    return "inconclusive_between_bounds"
