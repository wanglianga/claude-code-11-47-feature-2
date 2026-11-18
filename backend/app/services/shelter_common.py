"""安置点情报模块的公共工具：内容指纹、独立版本流、文本归一化与距离。

设计纪律：
- 五条职责（目录 / 公告接入 / 实体消歧 / 容量核验 / 情景报告）各自拥有
  独立版本流；版本以 *内容指纹* 寻址，相同内容复用同一版本行，内容变化
  （例如迟到公告）只追加新版本，绝不原地改写旧版本。
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from typing import Any, Optional

from psycopg.types.json import Json

MODULES = (
    "catalog",
    "announcements",
    "entity_resolution",
    "capacity_verification",
    "scenario_report",
)

_PUNCT = re.compile(r"[\s　,，。.;；:：()（）\[\]【】\-—_/\\]+")


def fingerprint(payload: Any) -> str:
    """对任意可 JSON 序列化内容取稳定短指纹。"""
    raw = json.dumps(payload, sort_keys=True, default=str, ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]


def normalize_text(value: Optional[str]) -> str:
    """归一化场所名称/地址：去空白与常见标点，统一小写。"""
    if not value:
        return ""
    return _PUNCT.sub("", value).strip().lower()


def name_core(value: Optional[str]) -> str:
    """去掉通用后缀，便于“南门学校”与“南门学校安置点”这类同义名称比对。"""
    text = normalize_text(value)
    for suffix in ("安置点", "避难场所", "应急避难场所", "安置场所"):
        if text.endswith(suffix) and len(text) > len(suffix):
            text = text[: -len(suffix)]
    return text


def distance_m(x1: float, y1: float, x2: float, y2: float) -> float:
    return math.hypot(x1 - x2, y1 - y2)


def add_source_doc(
    cur,
    *,
    dataset_type: str,
    title: str,
    unit_note: Optional[str],
    occurred,
    cutoff,
    raw: str = "",
    org: str = "市防汛办",
):
    """写入来源原文并返回 doc_id（与既有目录共用 source_documents）。"""
    cur.execute(
        """INSERT INTO source_documents
               (dataset_type, title, source_org, unit_note, source_occurred_at,
                received_at, cutoff_at, revision_no, supersedes_doc_id, raw_text)
           VALUES (%s,%s,%s,%s,%s,%s,%s,1,NULL,%s) RETURNING doc_id""",
        (dataset_type, title, org, unit_note, occurred, cutoff, cutoff, raw),
    )
    return cur.fetchone()["doc_id"]


def get_or_create_module_version(
    conn,
    module: str,
    fp: str,
    cutoff,
    detail: Optional[dict] = None,
) -> dict:
    """按 (module, fingerprint) 取或建版本行；内容相同即复用同一版本号。"""
    if module not in MODULES:
        raise ValueError(f"unknown shelter module: {module}")
    with conn.cursor() as cur:
        cur.execute(
            "SELECT * FROM shelter_module_versions WHERE module=%s AND fingerprint=%s",
            (module, fp),
        )
        row = cur.fetchone()
        if row is not None:
            return row
        cur.execute(
            """INSERT INTO shelter_module_versions (module, cutoff_at, fingerprint, detail)
               VALUES (%s,%s,%s,%s)
               ON CONFLICT (module, fingerprint) DO NOTHING
               RETURNING *""",
            (module, cutoff, fp, Json(detail or {})),
        )
        row = cur.fetchone()
        if row is None:  # 并发下另一请求已插入
            cur.execute(
                "SELECT * FROM shelter_module_versions WHERE module=%s AND fingerprint=%s",
                (module, fp),
            )
            row = cur.fetchone()
        return row

