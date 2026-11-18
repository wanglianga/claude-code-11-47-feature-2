"""③ 实体消歧模块：把公告匹配到唯一场所主档，歧义与冲突显式待核。

两种解析策略（可在情景报告中按策略对比）：
- ``auto``  ：唯一候选且有足够证据即自动接受；多候选中只有“证据明显领先”
              （唯一同时满足名称/地址/坐标）才接受，其余待人工确认。
- ``manual``：只要存在多个候选就保留人工确认，误合并更少、更多场所暂时不可用。

无论哪种策略：证据不足、候选冲突、人工驳回都只会得到 ``entity_pending``，
绝不猜测合并。人工确认（confirm/reject）只影响其后的报告，旧报告不可改写。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from .shelter_common import (
    distance_m,
    fingerprint,
    get_or_create_module_version,
    name_core,
    normalize_text,
)

COORD_MATCH_M = 150.0          # 坐标匹配半径（米）
SCORE_NAME = 2
SCORE_ADDRESS = 3
SCORE_COORD = 2
DECISIVE_TOP = 4               # auto 策略接受多候选所需的最低分
DECISIVE_MARGIN = 2            # auto 策略接受多候选所需的领先分差
MIN_SUFFICIENT = 2             # 单候选接受的最低证据分

POLICIES = ("auto", "manual")


def _candidate_score(ann: dict, master: dict) -> tuple[int, list[str]]:
    score = 0
    reasons: list[str] = []
    if ann.get("site_name") and name_core(ann["site_name"]) == name_core(master["name"]):
        score += SCORE_NAME
        reasons.append("name")
    if ann.get("address") and normalize_text(ann["address"]) == normalize_text(master["address"]):
        score += SCORE_ADDRESS
        reasons.append("address")
    if ann.get("geom_x") is not None and ann.get("geom_y") is not None:
        if distance_m(float(ann["geom_x"]), float(ann["geom_y"]),
                      master["x"], master["y"]) <= COORD_MATCH_M:
            score += SCORE_COORD
            reasons.append("coordinate")
    return score, reasons


def load_overrides(conn, cutoff: Optional[datetime] = None) -> dict[str, dict]:
    """人工确认/驳回的当前状态（每条公告取最新一次）。

    人工判断是 *分析员决策*，不是带证据截止点的原始资料：它只影响做出决定
    之后生成的报告，而不受报告证据截止点回拨的影响。已生成的旧报告保持
    原样（见报告内容签名去重与不可改写约束）。
    """
    with conn.cursor() as cur:
        cur.execute(
            """SELECT DISTINCT ON (announcement_id) *
               FROM shelter_entity_overrides
               ORDER BY announcement_id, override_id DESC"""
        )
        rows = cur.fetchall()
    return {r["announcement_id"]: dict(r) for r in rows}


def resolve_entities(
    conn,
    *,
    cutoff: datetime,
    catalog: dict[str, Any],
    announcements: dict[str, Any],
    policy: str,
) -> dict[str, Any]:
    """返回 {announcement_id: resolution}，并落一个实体消歧版本。"""
    if policy not in POLICIES:
        raise ValueError(f"unknown entity policy: {policy}")
    masters = list(catalog["sites"].values())
    overrides = load_overrides(conn, cutoff)

    resolutions: dict[str, dict] = {}
    for ann in announcements["items"]:
        ann_id = ann["announcement_id"]
        override = overrides.get(ann_id)
        scored = []
        for m in masters:
            score, reasons = _candidate_score(ann, m)
            if score > 0:
                scored.append({
                    "master_id": m["master_id"],
                    "name": m["name"],
                    "address": m["address"],
                    "score": score,
                    "matched_on": reasons,
                })
        scored.sort(key=lambda c: (c["score"], c["master_id"]), reverse=True)
        candidates = [{**c} for c in scored]

        resolved_id: Optional[str] = None
        method: Optional[str] = None
        pending_reason: Optional[str] = None

        if override is not None and override["action"] == "reject":
            resolutions[ann_id] = {
                "announcement_id": ann_id,
                "entity_status": "rejected",
                "master_id": None,
                "match_method": "manual_reject",
                "candidates": candidates,
                "reason": override.get("note") or "人工驳回：该公告不并入任何场所",
            }
            continue

        if override is not None and override["action"] == "confirm":
            resolved_id, method = override["master_id"], "manual_confirm"
        elif not candidates:
            pending_reason = "无可匹配的场所主档（名称/地址/坐标均未命中）"
        elif len(candidates) == 1:
            if candidates[0]["score"] >= MIN_SUFFICIENT:
                resolved_id, method = candidates[0]["master_id"], "unique_" + "+".join(
                    candidates[0]["matched_on"]
                )
            else:
                pending_reason = "候选证据不足，未达到自动接受门槛"
        else:
            top, second = candidates[0], candidates[1]
            if policy == "auto" and top["score"] >= DECISIVE_TOP \
                    and top["score"] - second["score"] >= DECISIVE_MARGIN:
                resolved_id, method = top["master_id"], "decisive_" + "+".join(top["matched_on"])
            elif policy == "auto":
                pending_reason = (
                    f"{len(candidates)} 个候选且证据不占优（最高 {top['score']} / 次高 {second['score']}），"
                    "需人工确认"
                )
            else:
                pending_reason = (
                    f"{len(candidates)} 个候选：manual 策略对所有多候选保留人工确认"
                )

        if resolved_id is not None:
            resolutions[ann_id] = {
                "announcement_id": ann_id,
                "entity_status": "resolved",
                "master_id": resolved_id,
                "match_method": method,
                "candidates": candidates,
                "reason": None,
            }
        else:
            resolutions[ann_id] = {
                "announcement_id": ann_id,
                "entity_status": "entity_pending",
                "master_id": None,
                "match_method": None,
                "candidates": candidates,
                "reason": pending_reason or "实体匹配冲突，待人工确认",
            }

    fp = fingerprint({
        "policy": policy,
        "catalog": catalog["fingerprint"],
        "announcements": announcements["fingerprint"],
        "overrides": {k: v["action"] + ":" + str(v["master_id"]) for k, v in overrides.items()},
        "resolutions": sorted(
            (a, r["entity_status"], r["master_id"], r["match_method"])
            for a, r in resolutions.items()
        ),
    })
    version = get_or_create_module_version(
        conn, "entity_resolution", fp, cutoff,
        detail={
            "policy": policy,
            "resolved": sum(1 for r in resolutions.values() if r["entity_status"] == "resolved"),
            "pending": sum(1 for r in resolutions.values()
                           if r["entity_status"] == "entity_pending"),
            "rejected": sum(1 for r in resolutions.values() if r["entity_status"] == "rejected"),
        },
    )
    return {"resolutions": resolutions, "version": version,
            "fingerprint": fp, "policy": policy}
