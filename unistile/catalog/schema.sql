-- Catalog：可重建的派生物。删掉整个 runtime/ 后可由 OKF Bundle + Resource 重建。
CREATE TABLE IF NOT EXISTS concepts (
  uid             TEXT PRIMARY KEY,
  okf_path        TEXT UNIQUE NOT NULL,
  title           TEXT NOT NULL,
  description     TEXT,
  type            TEXT NOT NULL,
  status          TEXT NOT NULL,
  evidence_class  TEXT NOT NULL,
  media_type      TEXT,
  resource_uri    TEXT,
  source_sha256   TEXT,
  external_id     TEXT,
  aliases_json    TEXT NOT NULL DEFAULT '[]',
  domain          TEXT,
  version         INTEGER NOT NULL DEFAULT 1,
  content_hash    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS concept_edges (
  source_uid    TEXT NOT NULL,
  relation_type TEXT NOT NULL,
  target_uid    TEXT NOT NULL,
  provenance    TEXT,
  metadata      TEXT,
  PRIMARY KEY (source_uid, relation_type, target_uid)
);

CREATE TABLE IF NOT EXISTS resource_revisions (
  resource_uri           TEXT NOT NULL,
  revision               INTEGER NOT NULL,
  source_sha256          TEXT NOT NULL,
  normalized_text_sha256 TEXT NOT NULL,
  extractor_version      TEXT NOT NULL,
  PRIMARY KEY (resource_uri, revision)
);

-- 热插拔的实际机制：同一 Concept 可同时绑定多个 Provider。
-- role=shadow 只读不影响回答；切主 = 改两行 role，可秒级回滚。
CREATE TABLE IF NOT EXISTS resource_bindings (
  binding_id        TEXT PRIMARY KEY,
  concept_uid       TEXT NOT NULL,
  resource_uri      TEXT NOT NULL,
  resource_revision INTEGER NOT NULL,
  provider_id       TEXT NOT NULL,
  provider_version  TEXT NOT NULL,
  backend_object_id TEXT NOT NULL,
  source_sha256     TEXT NOT NULL,
  indexed_sha256    TEXT,
  status            TEXT NOT NULL,
  role              TEXT NOT NULL,
  updated_at        TEXT NOT NULL,
  UNIQUE (concept_uid, provider_id, resource_revision),
  FOREIGN KEY (concept_uid) REFERENCES concepts(uid)
);

CREATE INDEX IF NOT EXISTS idx_bindings_concept ON resource_bindings(concept_uid, role);
CREATE INDEX IF NOT EXISTS idx_edges_target ON concept_edges(target_uid, relation_type);

-- 投影：同一个 Concept 可以出现在多棵导航树下，Canonical Concept 仍只有一个。
-- 中间分组节点（项目、供应商、时间段）没有 concept_uid，只是导航容器。
CREATE TABLE IF NOT EXISTS projections (
  projection_id TEXT PRIMARY KEY,
  title         TEXT NOT NULL,
  description   TEXT,
  kind          TEXT NOT NULL,          -- materialized（YAML 定义）/ query_backed（规则派生）
  source        TEXT                    -- 定义文件路径或规则名
);

CREATE TABLE IF NOT EXISTS projection_nodes (
  projection_id  TEXT NOT NULL,
  node_id        TEXT NOT NULL,
  concept_uid    TEXT,                  -- NULL = 分组节点
  parent_node_id TEXT,
  label          TEXT NOT NULL,
  rank           INTEGER NOT NULL DEFAULT 0,
  view_metadata  TEXT,
  PRIMARY KEY (projection_id, node_id),
  FOREIGN KEY (concept_uid) REFERENCES concepts(uid)
);

CREATE INDEX IF NOT EXISTS idx_pnodes_parent ON projection_nodes(projection_id, parent_node_id, rank);
CREATE INDEX IF NOT EXISTS idx_pnodes_concept ON projection_nodes(concept_uid);
