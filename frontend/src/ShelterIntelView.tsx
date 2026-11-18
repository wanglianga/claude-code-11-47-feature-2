import { useEffect, useMemo, useState } from "react";
import { api } from "./api";
import type {
  EntityPolicy,
  ShelterAnnouncementRow,
  ShelterCatalogSite,
  ShelterModuleVersion,
  ShelterReport,
  ShelterReportResult,
  ShelterSiteResult,
  ScenarioListRow,
} from "./types";

const C1 = "2026-07-15T07:30:00+00:00";
const LATE = "2026-07-15T10:00:00+00:00";

const STATUS_LABEL: Record<string, string> = {
  ok: "可用（容量已核验）",
  capacity_unknown: "容量未知",
  upper_bound_uncertain: "上限不确定",
  not_open: "未开放 / 已撤销",
  entity_pending: "实体待核",
};

const COMPARISON_LABEL: Record<string, { text: string; cls: string }> = {
  capacity_shortfall: { text: "容量低于可达需求总量（短缺）", cls: "warn" },
  capacity_meets_demand_lower_bound: { text: "容量下界已覆盖可达需求", cls: "good" },
  inconclusive_between_bounds: { text: "区间内无法判定（落在上下界之间）", cls: "" },
};

function fmt(ts: string | null | undefined): string {
  return ts ? ts.replace("T", " ").slice(0, 16) : "—";
}

function range(low: number | null, high: number | null): string {
  if (low == null && high == null) return "—";
  if (low != null && high != null && Math.abs(low - high) < 1e-9) return `${low}`;
  return `[${low ?? 0} – ${high ?? "?"}]`;
}

export default function ShelterIntelView({
  pushToast,
}: {
  pushToast: (t: string, k?: "info" | "error") => void;
}) {
  const [cutoff, setCutoff] = useState(C1);
  const [policy, setPolicy] = useState<EntityPolicy>("auto");
  const [sites, setSites] = useState<ShelterCatalogSite[]>([]);
  const [announcements, setAnnouncements] = useState<ShelterAnnouncementRow[]>([]);
  const [catalogVersion, setCatalogVersion] = useState<ShelterModuleVersion | null>(null);
  const [versions, setVersions] = useState<ShelterModuleVersion[]>([]);
  const [tasks, setTasks] = useState<ScenarioListRow[]>([]);
  const [taskId, setTaskId] = useState("");
  const [reports, setReports] = useState<ShelterReport[]>([]);
  const [current, setCurrent] = useState<ShelterReportResult | null>(null);
  const [busy, setBusy] = useState(false);

  const loadCatalog = async (c: string) => {
    try {
      const [cat, anns] = await Promise.all([
        api.shelterCatalog(c),
        api.shelterAnnouncements(c),
      ]);
      setSites(cat.sites);
      setCatalogVersion(cat.version);
      setAnnouncements(anns.announcements);
    } catch (e) {
      pushToast(String(e), "error");
    }
  };

  useEffect(() => {
    loadCatalog(cutoff);
    api.shelterVersions().then((r) => setVersions(r.versions)).catch(() => undefined);
    api
      .listScenarios()
      .then((r) => {
        setTasks(r.scenarios);
        if (r.scenarios.length > 0 && !taskId) {
          setTaskId(r.scenarios[0].task_id ?? "");
        }
      })
      .catch(() => undefined);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [cutoff]);

  useEffect(() => {
    if (!taskId) return;
    api
      .listShelterReports(taskId)
      .then((r) => setReports(r.reports))
      .catch(() => undefined);
  }, [taskId]);

  const createReport = async () => {
    if (!taskId) {
      pushToast("请先在「情景分析」创建一个情景任务", "error");
      return;
    }
    setBusy(true);
    try {
      const r = await api.createShelterReport({
        task_id: taskId,
        policy,
        evidence_cutoff: cutoff,
      });
      setCurrent(r);
      const listing = await api.listShelterReports(taskId);
      setReports(listing.reports);
      pushToast(
        `报告已生成（${policy}）｜需求 ${r.report.reachable_demand_population} 人 vs ` +
          `容量 ${range(r.report.capacity_low_total, r.report.capacity_high_total)}`
      );
    } catch (e) {
      pushToast(String(e), "error");
    } finally {
      setBusy(false);
    }
  };

  const openReport = async (id: string) => {
    try {
      setCurrent(await api.getShelterReport(id));
    } catch (e) {
      pushToast(String(e), "error");
    }
  };

  const ingestLateRevoke = async () => {
    setBusy(true);
    try {
      await api.ingestAnnouncement({
        announcement_id: "ANN-REV-LATE-UI",
        kind: "revoke",
        site_name: "江畔小学安置点",
        address: "江畔路7号",
        geom_x: 3000,
        geom_y: 0,
        effective_at: "2026-07-15T07:00:00+00:00",
        received_at: "2026-07-15T09:45:00+00:00",
        note: "界面演示：撤销公告迟到补传",
      });
      pushToast("已补传撤销公告（received_at=09:45）。旧报告不变，新截止点报告会列出受影响场所。");
      await loadCatalog(cutoff);
    } catch (e) {
      pushToast(String(e), "error");
    } finally {
      setBusy(false);
    }
  };

  const renderSiteRow = (s: ShelterSiteResult) => (
    <tr key={s.site_key}>
      <td>
        <div>{s.display_name}</div>
        <div className="small muted mono">{s.site_key.startsWith("PENDING:") ? "待核公告" : s.site_key}</div>
        <div className="small muted">{s.address ?? "—"}</div>
      </td>
      <td>
        <span className={`pill ${s.status}`}>{STATUS_LABEL[s.status] ?? s.status}</span>
        {s.status_reason && <div className="small muted">{s.status_reason}</div>}
      </td>
      <td className="small">
        {s.entity_status === "entity_pending" ? (
          <span className="warn">候选 {s.candidate_count} 个，待人工确认</span>
        ) : (
          <>
            <span className="good">已解析</span>
            {s.match_method && <div className="small muted">{s.match_method}</div>}
          </>
        )}
      </td>
      <td className="small">
        {s.capacity_unit_raw ? `${s.capacity_unit_raw}` : "—"}
        {s.capacity_unit && s.capacity_unit !== "person" && (
          <div className="small muted">→ {s.capacity_unit}</div>
        )}
        {s.capacity_persons != null && (
          <div className="small muted">{s.capacity_persons} 人</div>
        )}
      </td>
      <td className="small">
        {s.occupancy_known === null ? "—" : s.occupancy_known ? s.occupancy_value ?? 0 : "未知"}
      </td>
      <td>
        <b>{range(s.available_low, s.available_high)}</b>
        <div className="small muted">{s.included_in_total ? "计入总量" : "不计入总量"}</div>
      </td>
      <td className="small">
        {s.open_status}
        <div className="small muted">
          {fmt(s.open_from)} ~ {fmt(s.open_to)}
        </div>
        <span className={`pill ${s.proof_status}`}>{s.proof_status}</span>
      </td>
    </tr>
  );

  const byStatus = useMemo(() => {
    const counts: Record<string, number> = {};
    for (const s of current?.sites ?? []) {
      counts[s.status] = (counts[s.status] ?? 0) + 1;
    }
    return counts;
  }, [current]);

  return (
    <div className="full-page">
      <div className="card">
        <h3>安置点实体解析与容量证据核验</h3>
        <div className="small muted" style={{ marginBottom: 10 }}>
          在冻结的道路时态与场所开放区间之上，把公告、场所主档、坐标修订、开放/撤销记录、
          容量单位与核验时点关联成带来源的场所实体。<b>需求可达人口</b>与<b>场所可用容量</b>
          是两个永远不混淆的指标：本模块不分配居民、不确定网格→场所先后次序、不逐格扣减容量，
          也不把未分配容量推算成已服务人数。
        </div>
        <div className="row" style={{ alignItems: "end", flexWrap: "wrap" }}>
          <label className="field" style={{ minWidth: 220 }}>
            <span>场所证据截止点</span>
            <input
              value={cutoff}
              onChange={(e) => setCutoff(e.target.value)}
              className="mono"
            />
          </label>
          <label className="field" style={{ minWidth: 200 }}>
            <span>实体解析策略</span>
            <select value={policy} onChange={(e) => setPolicy(e.target.value as EntityPolicy)}>
              <option value="auto">auto（唯一且证据充分即接受）</option>
              <option value="manual">manual（所有多候选待人工确认）</option>
            </select>
          </label>
          <label className="field" style={{ minWidth: 320 }}>
            <span>情景任务</span>
            <select value={taskId} onChange={(e) => setTaskId(e.target.value)}>
              <option value="">— 选择任务 —</option>
              {tasks.map((t) => (
                <option key={t.task_id ?? t.scenario_id} value={t.task_id ?? ""}>
                  {t.name}（{fmt(t.departure_time)}，{t.max_trip_minutes}min）
                </option>
              ))}
            </select>
          </label>
          <div className="field" style={{ maxWidth: 180 }}>
            <button className="btn" onClick={createReport} disabled={busy}>
              {busy ? "计算中…" : "生成场所情景报告"}
            </button>
          </div>
        </div>
        <div className="row" style={{ marginTop: 6 }}>
          <button className="btn secondary" onClick={() => setCutoff(C1)}>
            截止点 C1 07:30
          </button>
          <button className="btn secondary" onClick={() => setCutoff(LATE)}>
            截止点 10:00（含迟到补传）
          </button>
          <button className="btn secondary" onClick={ingestLateRevoke} disabled={busy}>
            演示：补传一条撤销公告（09:45）
          </button>
        </div>
        {catalogVersion && (
          <div className="evidence" style={{ marginTop: 10 }}>
            场所目录版本 catalog#v{catalogVersion.version_id}｜指纹{" "}
            <span className="mono">{catalogVersion.fingerprint}</span>｜可见场所 {sites.length} 个，
            公告 {announcements.length} 条
          </div>
        )}
      </div>

      {current && (
        <>
          <div className="card">
            <h3>
              情景报告：需求与容量分开展示（策略 {current.report.policy}）
            </h3>
            <div className="report-grid">
              <div className="side-box demand">
                <h4>需求侧 · 冻结人口基线可达人口</h4>
                <div>
                  <span className="big">{current.report.reachable_demand_population}</span>
                  <span className="unit">人（网格级去重后，不分配）</span>
                </div>
                <div className="small muted" style={{ marginTop: 6 }}>
                  来源：冻结覆盖汇总 covered_population｜任务证据截止{" "}
                  {fmt(
                    String(current.report.summary_json.demand_side["frozen_task_evidence_cutoff_at"] ?? "")
                  )}
                </div>
              </div>
              <div className="side-box capacity">
                <h4>场所侧 · 可用容量区间（各场所独立报告）</h4>
                <div>
                  <span className="big">
                    {range(current.report.capacity_low_total, current.report.capacity_high_total)}
                  </span>
                  <span className="unit">人</span>
                </div>
                <div className="small muted" style={{ marginTop: 6 }}>
                  仅含已计入总量的场所；容量不是已服务人数，也未与需求逐格配对。
                </div>
              </div>
            </div>
            <div className="evidence" style={{ marginTop: 10 }}>
              比较结论：
              <b className={COMPARISON_LABEL[current.report.comparison]?.cls}>
                {" "}
                {COMPARISON_LABEL[current.report.comparison]?.text}
              </b>
              ｜评价时点 {fmt(current.report.evaluation_time)}｜报告证据截止{" "}
              {fmt(current.report.evidence_cutoff_at)}｜状态计数{" "}
              {Object.entries(byStatus)
                .map(([k, v]) => `${STATUS_LABEL[k] ?? k}:${v}`)
                .join("，")}
            </div>
            <div className="never-merge">
              <b>不变量：</b>
              {current.report.summary_json.non_allocation_invariants.join("；")}
            </div>
            <div className="legend" style={{ marginTop: 8 }}>
              模块版本：catalog#v{current.report.version_catalog} · announcements#v
              {current.report.version_announcements} · entity#v{current.report.version_entity} ·
              capacity#v{current.report.version_capacity} · report#v
              {current.report.version_report}
            </div>
            {current.report.previous_report_id && (
              <div className="small muted" style={{ marginTop: 6 }}>
                本报告基于上一版报告生成；受影响场所：
                {current.report.affected_sites.length === 0
                  ? "（无差异）"
                  : current.report.affected_sites
                      .map((a) => `${String(a.site_key)}:${String(a.change)}`)
                      .join("，")}
              </div>
            )}
          </div>

          <div className="card scroll-x">
            <h3>逐场所可用容量与证据状态</h3>
            <table>
              <thead>
                <tr>
                  <th>场所（实体）</th>
                  <th>状态</th>
                  <th>实体匹配</th>
                  <th>容量单位</th>
                  <th>现有占用</th>
                  <th>可用容量（人）</th>
                  <th>开放 / 证明</th>
                </tr>
              </thead>
              <tbody>{current.sites.map(renderSiteRow)}</tbody>
            </table>
            <div className="small muted" style={{ marginTop: 8 }}>
              容量未知 / 上限不确定 / 未开放 / 实体待核 四类场所都不计入可用容量总量；
              它们不会被“容量不足”为由排除任何网格，也不影响需求侧可达人口。
            </div>
          </div>
        </>
      )}

      <div className="card">
        <h3>报告历史（迟到公告只会新增报告，旧报告不改写）</h3>
        <table>
          <thead>
            <tr>
              <th>报告</th>
              <th>策略</th>
              <th>评价时点</th>
              <th>证据截止</th>
              <th>需求（人）</th>
              <th>容量区间（人）</th>
              <th>比较</th>
              <th>受影响场所</th>
            </tr>
          </thead>
          <tbody>
            {reports.map((r) => (
              <tr key={r.report_id} className="clickable" onClick={() => openReport(r.report_id)}>
                <td className="mono small">{r.report_id.slice(0, 8)}</td>
                <td>{r.policy}</td>
                <td className="small">{fmt(r.evaluation_time)}</td>
                <td className="small">{fmt(r.evidence_cutoff_at)}</td>
                <td>{r.reachable_demand_population}</td>
                <td>{range(r.capacity_low_total, r.capacity_high_total)}</td>
                <td className="small">{COMPARISON_LABEL[r.comparison]?.text ?? r.comparison}</td>
                <td className="small">{r.affected_sites?.length ?? 0}</td>
              </tr>
            ))}
            {reports.length === 0 && (
              <tr>
                <td colSpan={8} className="muted small">
                  尚无报告；选择一个任务并点击「生成场所情景报告」。
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      <div className="card scroll-x">
        <h3>场所目录（含坐标修订）</h3>
        <table>
          <thead>
            <tr>
              <th>主档</th>
              <th>名称</th>
              <th>地址</th>
              <th>坐标</th>
              <th>坐标修订</th>
              <th>路网节点</th>
            </tr>
          </thead>
          <tbody>
            {sites.map((s) => (
              <tr key={s.master_id}>
                <td className="mono">{s.master_id}</td>
                <td>{s.name}</td>
                <td className="small">{s.address}</td>
                <td className="small mono">
                  ({s.x}, {s.y})
                </td>
                <td className="small">
                  r{s.coordinate_revision}
                  {s.coordinate_changed && <span className="warn"> 已校正</span>}
                </td>
                <td className="mono small">{s.node_id ?? "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="card scroll-x">
        <h3>截止点可见公告（按 received_at 过滤，迟到补传只在新截止点出现）</h3>
        <table>
          <thead>
            <tr>
              <th>公告</th>
              <th>类型</th>
              <th>场所名称</th>
              <th>单位</th>
              <th>容量</th>
              <th>占用</th>
              <th>核验 / 证明</th>
              <th>开放 / 撤销</th>
              <th>接收时间</th>
            </tr>
          </thead>
          <tbody>
            {announcements.map((a) => (
              <tr key={`${a.announcement_id}-r${a.revision_no}`}>
                <td className="mono small">
                  {a.announcement_id}
                  <div className="muted">r{a.revision_no}</div>
                </td>
                <td>
                  <span className="pill done">{a.kind}</span>
                </td>
                <td className="small">{a.site_name}</td>
                <td className="small">{a.unit_raw ?? "—"}</td>
                <td>{a.capacity_value ?? "—"}</td>
                <td className="small">
                  {a.occupancy_known ? a.occupancy_value ?? "—" : "未知"}
                </td>
                <td className="small">
                  {fmt(a.verified_at)}
                  <div className="muted">证明至 {fmt(a.proof_expires_at)}</div>
                </td>
                <td className="small">
                  {fmt(a.open_from)} ~ {fmt(a.open_to)}
                  {a.effective_at && <div className="warn">撤销生效 {fmt(a.effective_at)}</div>}
                </td>
                <td className="small">{fmt(a.received_at)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="card scroll-x">
        <h3>五条职责的独立版本流</h3>
        <table>
          <thead>
            <tr>
              <th>职责模块</th>
              <th>版本</th>
              <th>截止点</th>
              <th>指纹</th>
              <th>内容摘要</th>
            </tr>
          </thead>
          <tbody>
            {versions.map((v) => (
              <tr key={`${v.module}-${v.version_id}`}>
                <td className="mono">{v.module}</td>
                <td>v{v.version_id}</td>
                <td className="small">{fmt(v.cutoff_at)}</td>
                <td className="small mono">{v.fingerprint}</td>
                <td className="small muted">
                  {v.detail ? JSON.stringify(v.detail) : "—"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
