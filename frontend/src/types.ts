export interface ScenarioCreatePayload {
  name: string;
  population_as_of: string;
  departure_time: string;
  max_trip_minutes: number;
  road_cutoff: string;
  boundary_id?: string | null;
  viewer_id: string;
  note?: string;
}

export interface ScenarioResult {
  scenario: Record<string, unknown>;
  task_id: string;
  status: string;
  cache_hit: boolean;
  evidence_cutoff_at: string;
  message: string;
}

export interface Summary {
  total_population: number;
  located_population: number;
  unknown_population: number;
  covered_population: number;
  uncovered_population: number;
  covered_grid_count: number;
  unknown_grid_count: number;
  computed_at?: string;
}

export type ShareStatus = "covered" | "uncovered" | "unknown";

export interface ShareRow {
  task_id: string;
  grid_id: string;
  grid_name?: string;
  geom_x?: number;
  geom_y?: number;
  boundary_id?: string | null;
  connector_node: string;
  share_index: number;
  share_population: number;
  reachable: boolean;
  shelter_id: string | null;
  departure_time: string | null;
  arrival_time: string | null;
  total_wait_sec: number;
  witness_edge_path: string[] | null;
  status: ShareStatus;
  reason: string | null;
  evidence_cutoff_at: string;
}

export interface ArrivalRow {
  task_id: string;
  node_id: string;
  arrival_time: string;
  wait_cost_sec: number;
  witness_node_path: string[];
  witness_edge_path: string[];
  geom_x: number;
  geom_y: number;
  allow_wait: boolean;
}

export interface SourceDoc {
  doc_id: number;
  dataset_type: string;
  title: string;
  source_org: string | null;
  unit_note: string | null;
  source_occurred_at: string | null;
  received_at: string;
  cutoff_at: string;
  revision_no: number;
  supersedes_doc_id: number | null;
  raw_text: string | null;
}

export interface RoadEventRow {
  event_id: string;
  event_type: string;
  edge_id: string;
  effective_from: string;
  effective_to: string | null;
  speed_limit_kmh: number | null;
  revision_no: number;
  status: string;
  source_title?: string;
  source_raw?: string;
}

export interface EdgeRevision {
  edge_id: string;
  revision_no: number;
  from_node: string;
  to_node: string;
  length_m: number;
  base_speed_kmh: number;
  valid_from: string;
  valid_to: string | null;
}

export interface NodeRow {
  node_id: string;
  name: string | null;
  geom_x: number;
  geom_y: number;
  allow_wait: boolean;
}

export interface ShelterRow {
  shelter_id: string;
  name: string;
  node_id: string;
  geom_x: number;
  geom_y: number;
  active: boolean;
}

export interface OpenWindow {
  shelter_id: string;
  open_from: string;
  open_to: string | null;
}

export interface GridRow {
  grid_id: string;
  name: string | null;
  geom_x: number;
  geom_y: number;
  boundary_id: string | null;
  population: number;
  pop_revision: number;
  pop_as_of: string;
}

export interface ConnectorRow {
  grid_id: string;
  node_id: string;
  allocation_ratio: number;
  revision_no: number;
  source_title?: string;
}

export interface CacheRow {
  signature: string;
  task_id: string;
  graph_version_id: number;
  departure_time: string;
  max_trip_minutes: number;
  evidence_cutoff_at: string;
  stale: boolean;
  computed_at: string;
  status?: string;
  kind?: string;
  task_stale?: boolean;
  scenario_name?: string;
}

export interface TaskRow {
  task_id: string;
  scenario_id: string;
  signature: string;
  graph_version_id: number;
  kind: string;
  status: string;
  ref_count: number;
  stale: boolean;
  evidence_cutoff_at: string;
  error: string | null;
  created_at: string;
  scenario_name?: string;
}

export interface ScenarioListRow {
  scenario_id: string;
  name: string;
  population_as_of: string;
  departure_time: string;
  max_trip_minutes: number;
  road_cutoff: string;
  boundary_id: string | null;
  signature: string;
  evidence_cutoff_at: string;
  task_id: string | null;
  task_status: string | null;
  task_stale: boolean | null;
  task_kind: string | null;
  ref_count: number | null;
}

export interface ComparisonGrid {
  grid_id: string;
  boundary_id: string | null;
  population_a: number;
  population_b: number;
  covered_pop_a: number;
  covered_pop_b: number;
  unknown_pop_a: number;
  unknown_pop_b: number;
  shelters_a: string[];
  shelters_b: string[];
  reasons_a: string[];
  reasons_b: string[];
  classification: string;
}

export interface Comparison {
  scenario_a: Record<string, unknown>;
  scenario_b: Record<string, unknown>;
  common_denominator: {
    grid_count: number;
    population_a: number;
    population_b: number;
    covered_a: number;
    covered_b: number;
    unknown_a: number;
    unknown_b: number;
    coverage_rate_a: number;
    coverage_rate_b: number;
    rate_delta: number;
  };
  attribution: {
    road_or_open_window_effect_population: number;
    newly_covered_grids: ComparisonGrid[];
    lost_coverage_grids: ComparisonGrid[];
    population_revision_effect: Array<{
      grid_id: string;
      population_delta: number;
      covered_a: number;
      covered_b: number;
    }>;
    boundary_added_grids: string[];
    boundary_removed_grids: string[];
    unknown_changed_grids: ComparisonGrid[];
    stable_covered_count: number;
    stable_uncovered_count: number;
  };
  per_grid: ComparisonGrid[];
  warnings: string[];
  basis_checks: Record<string, boolean>;
}

// ------------------------------------------------------ 安置点情报 --
export type SiteStatus =
  | "ok"
  | "capacity_unknown"
  | "upper_bound_uncertain"
  | "not_open"
  | "entity_pending";

export type EntityPolicy = "auto" | "manual";

export interface ShelterCatalogSite {
  master_id: string;
  name: string;
  address: string;
  x: number;
  y: number;
  node_id: string | null;
  shelter_id: string | null;
  coordinate_revision: number;
  coordinate_changed: boolean;
}

export interface ShelterAnnouncementRow {
  announcement_id: string;
  revision_no: number;
  kind: "open" | "capacity" | "revoke" | "coordinate";
  site_name: string;
  address: string | null;
  unit_raw: string | null;
  capacity_value: number | null;
  occupancy_value: number | null;
  occupancy_known: boolean;
  verified_at: string | null;
  proof_expires_at: string | null;
  open_from: string | null;
  open_to: string | null;
  effective_at: string | null;
  received_at: string;
  source_doc_id: number | null;
  note: string | null;
}

export interface EntityCandidate {
  master_id: string;
  name: string;
  address: string;
  score: number;
  matched_on: string[];
}

export interface EntityResolution {
  announcement_id: string;
  entity_status: "resolved" | "entity_pending" | "rejected";
  master_id: string | null;
  match_method: string | null;
  candidates: EntityCandidate[];
  reason: string | null;
}

export interface ShelterSiteResult {
  site_key: string;
  master_id: string | null;
  display_name: string;
  address: string | null;
  geom_x: number | null;
  geom_y: number | null;
  entity_status: "resolved" | "entity_pending";
  entity_policy: EntityPolicy;
  match_method: string | null;
  candidate_count: number;
  candidates_json: EntityCandidate[];
  announcement_ids: string[];
  capacity_unit_raw: string | null;
  capacity_unit: string | null;
  capacity_value: number | null;
  capacity_persons: number | null;
  occupancy_known: boolean | null;
  occupancy_value: number | null;
  available_low: number | null;
  available_high: number | null;
  open_status: string;
  open_from: string | null;
  open_to: string | null;
  proof_status: "valid" | "expired" | "missing";
  verified_at: string | null;
  status: SiteStatus;
  status_reason: string | null;
  included_in_total: boolean;
  evidence_sources: Array<{
    announcement_id: string;
    revision_no: number;
    kind: string;
    source_doc_id: number | null;
    received_at: string | null;
  }>;
}

export interface ShelterReport {
  report_id: string;
  signature: string;
  task_id: string;
  policy: EntityPolicy;
  evaluation_time: string;
  evidence_cutoff_at: string;
  version_catalog: number;
  version_announcements: number;
  version_entity: number;
  version_capacity: number;
  version_report: number;
  previous_report_id: string | null;
  reachable_demand_population: number;
  capacity_low_total: number;
  capacity_high_total: number;
  comparison:
    | "capacity_shortfall"
    | "capacity_meets_demand_lower_bound"
    | "inconclusive_between_bounds";
  affected_sites: Array<Record<string, unknown>>;
  summary_json: {
    demand_side: Record<string, unknown>;
    capacity_side: Record<string, unknown>;
    comparison: string;
    policy: EntityPolicy;
    evaluation_time: string;
    shelter_evidence_cutoff_at: string;
    non_allocation_invariants: string[];
    status_counts: Record<string, number>;
  };
  created_at?: string;
}

export interface ShelterReportResult {
  report: ShelterReport;
  sites: ShelterSiteResult[];
}

export interface ShelterModuleVersion {
  module: string;
  version_id: number;
  cutoff_at: string;
  fingerprint: string;
  detail: Record<string, unknown> | null;
  created_at: string;
}
