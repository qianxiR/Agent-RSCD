-- 业务库 Schema 迁移脚本 (幂等)
-- 对接清单: #2 空间数据入库 / #4 元数据管理
--
-- 设计原则:
--   1. 所有 CREATE 语句带 IF NOT EXISTS, 可重复执行
--   2. ★ PostGIS 缺失时降级: GEOMETRY 列改为 JSONB 存 bbox (业务代码 ensure_business_schema 自动适配)
--   3. 强制 CGCS2000 (EPSG:4490) 入库 (PostGIS 可用时 ST_Transform)
--
-- 运行方式:
--   psql -h localhost -U postgres -d cd -f scripts/migrate_business_schema.sql
--   或由 backend/main.py startup 自动调用 ensure_business_schema()

-- ==================== 检测 PostGIS 是否可用 ====================
-- 业务代码会用 SELECT postgis_version() 探测, 此处只保留扩展创建 (不可用则忽略)
CREATE EXTENSION IF NOT EXISTS postgis;

-- ==================== 影像元数据表 (#4) ====================
-- 管理影像的时间戳/分辨率/覆盖范围/坐标系, 支持按时间/区域检索
CREATE TABLE IF NOT EXISTS image_metadata (
    id              BIGSERIAL PRIMARY KEY,
    layer_name      TEXT NOT NULL UNIQUE,        -- GeoServer 图层名 (上传后获得)
    workspace       TEXT,                         -- GeoServer 工作空间
    file_path       TEXT,                         -- agent-files 下的原始文件路径
    sensor          TEXT,                         -- 影像来源/传感器 (可选)
    acquired_at     TIMESTAMP,                    -- 影像获取时间 (可选)
    resolution      REAL,                         -- 空间分辨率 (米)
    -- ★ bbox: PostGIS 可用时是 GEOMETRY(POLYGON, 4490), 不可用时由业务代码改用 JSONB 列
    bbox_geom       GEOMETRY(POLYGON, 4490),
    bbox_jsonb      JSONB,                        -- 兜底: {minx, miny, maxx, maxy, srid} (无 PostGIS 时用)
    srid            INTEGER DEFAULT 4490,         -- CGCS2000
    width           INTEGER,                      -- 影像宽度 (像素)
    height          INTEGER,                      -- 影像高度 (像素)
    band_count      INTEGER,                      -- 波段数
    uploaded_at     TIMESTAMP DEFAULT NOW(),
    uploaded_by     TEXT DEFAULT 'study_user'
);

-- 空间索引 (PostGIS 可用时生效; 否则此语句会失败, 由业务代码 try/except 跳过)
CREATE INDEX IF NOT EXISTS idx_image_metadata_bbox_geom
    ON image_metadata USING GIST (bbox_geom);

-- 时间索引 (永远可用)
CREATE INDEX IF NOT EXISTS idx_image_metadata_time
    ON image_metadata (acquired_at);

CREATE INDEX IF NOT EXISTS idx_image_metadata_uploaded
    ON image_metadata (uploaded_at DESC);


-- ==================== 矢量图层元数据表 (#2 入库 / 后续规则匹配基础) ====================
-- 管理矢量图层 (规则/变化/行政区), 复用于变化图斑登记
CREATE TABLE IF NOT EXISTS vector_layer (
    id              BIGSERIAL PRIMARY KEY,
    layer_name      TEXT NOT NULL UNIQUE,         -- 图层名 (GeoServer 发布名 或 变化检测产物名)
    workspace       TEXT,
    kind            TEXT NOT NULL DEFAULT 'change',  -- rule / change / administrative
    file_path       TEXT,                          -- 矢量文件路径 (agent-files 下)
    source_task_id  BIGINT,                        -- 来源 ai_task (变化检测产物的关联)
    bbox_geom       GEOMETRY(POLYGON, 4490),
    bbox_jsonb      JSONB,
    srid            INTEGER DEFAULT 4490,
    feature_count   INTEGER,                       -- 要素数
    total_area_m2   REAL,                          -- 总面积 (平方米)
    attrs           JSONB,                         -- 自定义属性 (类别分布/变化类型等)
    created_at      TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_vector_layer_bbox_geom
    ON vector_layer USING GIST (bbox_geom);

CREATE INDEX IF NOT EXISTS idx_vector_layer_kind
    ON vector_layer (kind, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_vector_layer_source_task
    ON vector_layer (source_task_id);
