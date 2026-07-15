"""
业务数据库访问层
- 入参: 无 (从 backend.config.settings 读取连接串)
- 方法: PostgreSQL 连接池管理、通用表查询与 CRUD
- 出参: 连接池实例、查询结果 dict/list

若数据库不可用, 返回 None, 调用方自动降级为 mock 模式。
"""
import logging
from contextlib import contextmanager
from typing import Optional, Dict, Any, List

from backend.config import settings

logger = logging.getLogger(__name__)

# 延迟导入 psycopg2, 避免未安装时模块加载失败
_psycopg2 = None
_pool = None


def _get_psycopg2():
    """返回二元组 (psycopg2, pool_mod); 不可用返回 (None, None)."""
    global _psycopg2
    if _psycopg2 is None:
        try:
            import psycopg2
            from psycopg2 import pool as _pool_mod
            _psycopg2 = (psycopg2, _pool_mod)
        except ImportError:
            logger.warning("psycopg2 未安装, 数据库功能不可用")
            return None, None
    return _psycopg2


def _get_json():
    """惰性取 psycopg2.extras.Json (JSONB 序列化用); 不可用返回 None."""
    try:
        from psycopg2.extras import Json
        return Json
    except ImportError:
        return None


def _get_pool():
    """懒初始化连接池"""
    global _pool
    if _pool is not None:
        return _pool
    psycopg2, pool_mod = _get_psycopg2()
    if psycopg2 is None:
        return None
    try:
        _pool = pool_mod.ThreadedConnectionPool(
            minconn=1,
            maxconn=5,
            host=settings.postgres_host,
            port=settings.postgres_port,
            dbname=settings.postgres_db,
            user=settings.postgres_user,
            password=settings.postgres_password,
        )
        logger.info(f"数据库连接池已创建: {settings.postgres_host}:{settings.postgres_port}/{settings.postgres_db}")
        return _pool
    except Exception as e:
        logger.warning(f"数据库连接失败, 将使用 mock 模式: {e}")
        return None


@contextmanager
def get_conn():
    """获取数据库连接 (上下文管理器)"""
    pool = _get_pool()
    if pool is None:
        yield None
        return
    conn = None
    try:
        conn = pool.getconn()
        yield conn
    finally:
        if conn is not None and pool is not None:
            pool.putconn(conn)


def db_available() -> bool:
    """检查数据库是否可用"""
    pool = _get_pool()
    if pool is None:
        return False
    try:
        with get_conn() as conn:
            if conn is None:
                return False
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
            return True
    except Exception:
        return False



# ==================== 通用表查询 ====================

def list_tables(schema: str = "public") -> List[Dict[str, Any]]:
    """列出数据库中的所有用户表"""
    with get_conn() as conn:
        if conn is None:
            return []
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT table_name,
                              (SELECT COUNT(*) FROM information_schema.columns WHERE table_name = t.table_name) AS column_count,
                              pg_size_pretty(pg_total_relation_size(quote_ident(t.table_name))) AS size
                       FROM information_schema.tables t
                       WHERE table_schema = %s AND table_type = 'BASE TABLE'
                       ORDER BY table_name""",
                    (schema,),
                )
                return [
                    {"table_name": r[0], "column_count": r[1], "size": r[2]}
                    for r in cur.fetchall()
                ]
        except Exception as e:
            logger.error(f"列出表失败: {e}")
            return []


def read_table_data(table_name: str, limit: int = 100, offset: int = 0) -> Optional["pd.DataFrame"]:
    """读取任意表的全部数据, 返回 DataFrame"""
    try:
        import pandas as pd
    except ImportError:
        return None

    with get_conn() as conn:
        if conn is None:
            return None
        try:
            df = _read_sql_df(
                f'SELECT * FROM "{table_name}" LIMIT %s OFFSET %s',
                conn,
                (limit, offset),
            )
            return df
        except Exception as e:
            logger.error(f"读取表 {table_name} 失败: {e}")
            return None


def get_table_statistics(table_name: str) -> Optional[Dict[str, Any]]:
    """获取表的统计信息: 行数、列信息、数值列统计"""
    with get_conn() as conn:
        if conn is None:
            return None
        try:
            result = {"table_name": table_name, "columns": []}

            # 获取列信息
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT column_name, data_type FROM information_schema.columns WHERE table_name = %s ORDER BY ordinal_position",
                    (table_name,),
                )
                cols = [{"name": r[0], "type": r[1]} for r in cur.fetchall()]

            # 行数 + 数值列统计
            with conn.cursor() as cur:
                cur.execute(f'SELECT COUNT(*) FROM "{table_name}"')
                result["row_count"] = cur.fetchone()[0]

            # 对数值列计算基本统计
            numeric_cols = [
                c["name"]
                for c in cols
                if c["type"] in ("integer", "bigint", "smallint", "real", "double precision", "numeric", "float")
            ]
            if numeric_cols:
                agg_exprs = []
                for nc in numeric_cols[:10]:  # 最多 10 列
                    agg_exprs.append(f'MIN("{nc}") AS "{nc}_min"')
                    agg_exprs.append(f'MAX("{nc}") AS "{nc}_max"')
                    agg_exprs.append(f'AVG("{nc}") AS "{nc}_avg"')
                with conn.cursor() as cur:
                    cur.execute(f'SELECT {", ".join(agg_exprs)} FROM "{table_name}"')
                    row = cur.fetchone()
                    for nc in numeric_cols[:10]:
                        idx = numeric_cols[:10].index(nc) * 3
                        result.setdefault("numeric_stats", {})[nc] = {
                            "min": float(row[idx]) if row[idx] is not None else None,
                            "max": float(row[idx + 1]) if row[idx + 1] is not None else None,
                            "avg": float(row[idx + 2]) if row[idx + 2] is not None else None,
                        }

            result["columns"] = cols
            return result
        except Exception as e:
            logger.error(f"统计表 {table_name} 失败: {e}")
            return None


# ==================== 数据增删改 (CRUD) ====================

def insert_data(table_name: str, data: Dict[str, Any]) -> Dict[str, Any]:
    """
    向指定表插入一行数据。

    入参:
        - table_name: 表名
        - data: 列名→值的字典, 如 {"name": "青海湖", "area": 4500}

    出参: {status, table_name, row_count, message}
    """
    with get_conn() as conn:
        if conn is None:
            return {"status": "error", "msg": "数据库不可用"}
        try:
            columns = list(data.keys())
            values = list(data.values())
            placeholders = ", ".join(["%s"] * len(columns))
            cols_sql = ", ".join(f'"{c}"' for c in columns)

            with conn.cursor() as cur:
                cur.execute(
                    f'INSERT INTO "{table_name}" ({cols_sql}) VALUES ({placeholders})',
                    values,
                )
                conn.commit()
                row_count = cur.rowcount
                logger.info(f"插入成功: {table_name}, {row_count} 行")
                return {
                    "status": "success",
                    "table_name": table_name,
                    "row_count": row_count,
                    "message": f"成功向 {table_name} 插入 {row_count} 行",
                }
        except Exception as e:
            logger.error(f"插入失败 [{table_name}]: {e}")
            return {"status": "error", "msg": str(e)}


def update_data(table_name: str, data: Dict[str, Any], condition: str = None,
                condition_params: list = None) -> Dict[str, Any]:
    """
    更新表中符合条件的行。

    入参:
        - table_name: 表名
        - data: 要更新的列名→新值字典
        - condition: WHERE 条件子句, 如 "id = %s"
        - condition_params: 条件参数列表, 如 [1]

    出参: {status, table_name, row_count, message}
    """
    with get_conn() as conn:
        if conn is None:
            return {"status": "error", "msg": "数据库不可用"}
        try:
            set_clauses = ", ".join(f'"{k}" = %s' for k in data.keys())
            set_values = list(data.values())

            if condition:
                sql = f'UPDATE "{table_name}" SET {set_clauses} WHERE {condition}'
                params = set_values + (condition_params or [])
            else:
                sql = f'UPDATE "{table_name}" SET {set_clauses}'
                params = set_values

            with conn.cursor() as cur:
                cur.execute(sql, params)
                conn.commit()
                row_count = cur.rowcount
                logger.info(f"更新成功: {table_name}, {row_count} 行")
                return {
                    "status": "success",
                    "table_name": table_name,
                    "row_count": row_count,
                    "message": f"成功更新 {table_name} 中 {row_count} 行",
                }
        except Exception as e:
            logger.error(f"更新失败 [{table_name}]: {e}")
            return {"status": "error", "msg": str(e)}


def delete_data(table_name: str, condition: str = None,
                condition_params: list = None) -> Dict[str, Any]:
    """
    删除表中符合条件的行。

    入参:
        - table_name: 表名
        - condition: WHERE 条件子句, 如 "id = %s"
        - condition_params: 条件参数列表, 如 [1]

    出参: {status, table_name, row_count, message}
    """
    with get_conn() as conn:
        if conn is None:
            return {"status": "error", "msg": "数据库不可用"}
        try:
            if condition:
                sql = f'DELETE FROM "{table_name}" WHERE {condition}'
                params = condition_params or []
            else:
                sql = f'DELETE FROM "{table_name}"'
                params = []

            with conn.cursor() as cur:
                cur.execute(sql, params)
                conn.commit()
                row_count = cur.rowcount
                logger.info(f"删除成功: {table_name}, {row_count} 行")
                return {
                    "status": "success",
                    "table_name": table_name,
                    "row_count": row_count,
                    "message": f"成功从 {table_name} 删除 {row_count} 行",
                }
        except Exception as e:
            logger.error(f"删除失败 [{table_name}]: {e}")
            return {"status": "error", "msg": str(e)}


# ==================== 业务 Schema 与元数据登记 (#2/#4) ====================
# 对接清单: #2 空间数据入库 / #4 元数据管理
# PostGIS 缺失时自动降级: bbox 列用 JSONB 存, 空间查询走 Python 内存过滤

_postgis_available = None  # 缓存: None=未检测, True/False


def postgis_available() -> bool:
    """检测 PostGIS 扩展是否可用 (惰性检测一次, 缓存结果)."""
    global _postgis_available
    if _postgis_available is not None:
        return _postgis_available
    with get_conn() as conn:
        if conn is None:
            _postgis_available = False
            return False
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT postgis_version()")
                cur.fetchone()
            _postgis_available = True
            logger.info("[BusinessDB] PostGIS 可用, 将使用 GIST 空间索引")
        except Exception:
            _postgis_available = False
            logger.info("[BusinessDB] PostGIS 不可用, 空间数据将降级为 JSONB 存储")
    return _postgis_available


def ensure_business_schema() -> bool:
    """
    幂等建表: image_metadata / vector_layer.
    PostGIS 可用时建 GEOMETRY 列; 不可用时只建 JSONB 列 (跳过 GIST 索引).
    """
    if not db_available():
        logger.warning("[BusinessDB] 业务库不可用, schema 未初始化")
        return False
    has_postgis = postgis_available()
    with get_conn() as conn:
        if conn is None:
            return False
        try:
            with conn.cursor() as cur:
                # image_metadata
                bbox_geom_col = "bbox_geom GEOMETRY(POLYGON, 4490)," if has_postgis else ""
                cur.execute(f"""
                    CREATE TABLE IF NOT EXISTS image_metadata (
                        id              BIGSERIAL PRIMARY KEY,
                        layer_name      TEXT NOT NULL UNIQUE,
                        workspace       TEXT,
                        file_path       TEXT,
                        sensor          TEXT,
                        acquired_at     TIMESTAMP,
                        resolution      REAL,
                        {bbox_geom_col}
                        bbox_jsonb      JSONB,
                        srid            INTEGER DEFAULT 4490,
                        width           INTEGER,
                        height          INTEGER,
                        band_count      INTEGER,
                        uploaded_at     TIMESTAMP DEFAULT NOW(),
                        uploaded_by     TEXT DEFAULT 'study_user'
                    )
                """)
                cur.execute("ALTER TABLE image_metadata ADD COLUMN IF NOT EXISTS bbox_jsonb JSONB")
                cur.execute("ALTER TABLE image_metadata ADD COLUMN IF NOT EXISTS width INTEGER")
                cur.execute("ALTER TABLE image_metadata ADD COLUMN IF NOT EXISTS height INTEGER")
                cur.execute("ALTER TABLE image_metadata ADD COLUMN IF NOT EXISTS band_count INTEGER")
                if has_postgis:
                    try:
                        cur.execute("CREATE EXTENSION IF NOT EXISTS postgis")
                        cur.execute("""
                            CREATE INDEX IF NOT EXISTS idx_image_metadata_bbox_geom
                            ON image_metadata USING GIST (bbox_geom)
                        """)
                        cur.execute("ALTER TABLE image_metadata ADD COLUMN IF NOT EXISTS bbox_geom GEOMETRY(POLYGON, 4490)")
                    except Exception as e:
                        logger.warning(f"[BusinessDB] GIST 索引创建失败 (降级 JSONB): {e}")
                cur.execute("CREATE INDEX IF NOT EXISTS idx_image_metadata_time ON image_metadata (acquired_at)")
                cur.execute("CREATE INDEX IF NOT EXISTS idx_image_metadata_uploaded ON image_metadata (uploaded_at DESC)")

                # vector_layer
                vgeom_col = "bbox_geom GEOMETRY(POLYGON, 4490)," if has_postgis else ""
                cur.execute(f"""
                    CREATE TABLE IF NOT EXISTS vector_layer (
                        id              BIGSERIAL PRIMARY KEY,
                        layer_name      TEXT NOT NULL UNIQUE,
                        workspace       TEXT,
                        kind            TEXT NOT NULL DEFAULT 'change',
                        file_path       TEXT,
                        source_task_id  BIGINT,
                        {vgeom_col}
                        bbox_jsonb      JSONB,
                        srid            INTEGER DEFAULT 4490,
                        feature_count   INTEGER,
                        total_area_m2   REAL,
                        attrs           JSONB,
                        created_at      TIMESTAMP DEFAULT NOW()
                    )
                """)
                cur.execute("ALTER TABLE vector_layer ADD COLUMN IF NOT EXISTS bbox_jsonb JSONB")
                cur.execute("ALTER TABLE vector_layer ADD COLUMN IF NOT EXISTS source_task_id BIGINT")
                cur.execute("ALTER TABLE vector_layer ADD COLUMN IF NOT EXISTS feature_count INTEGER")
                cur.execute("ALTER TABLE vector_layer ADD COLUMN IF NOT EXISTS total_area_m2 REAL")
                cur.execute("ALTER TABLE vector_layer ADD COLUMN IF NOT EXISTS attrs JSONB")
                if has_postgis:
                    try:
                        cur.execute("""
                            CREATE INDEX IF NOT EXISTS idx_vector_layer_bbox_geom
                            ON vector_layer USING GIST (bbox_geom)
                        """)
                        cur.execute("ALTER TABLE vector_layer ADD COLUMN IF NOT EXISTS bbox_geom GEOMETRY(POLYGON, 4490)")
                    except Exception as e:
                        logger.warning(f"[BusinessDB] vector_layer GIST 失败 (降级 JSONB): {e}")
                cur.execute("CREATE INDEX IF NOT EXISTS idx_vector_layer_kind ON vector_layer (kind, created_at DESC)")
            conn.commit()
            logger.info("[BusinessDB] schema 就绪 (image_metadata / vector_layer)")
            return True
        except Exception as e:
            conn.rollback()
            logger.error(f"[BusinessDB] schema 初始化失败: {e}")
            return False


def _bbox_to_wkt(bbox: tuple, srid: int = 4490) -> str:
    """(minx, miny, maxx, maxy) → POLYGON WKT (用于 ST_GeomFromText)."""
    minx, miny, maxx, maxy = bbox
    return f"POLYGON(({minx} {miny},{maxx} {miny},{maxx} {maxy},{minx} {maxy},{minx} {miny}))"


def register_image_metadata(
    layer_name: str,
    workspace: str = None,
    file_path: str = None,
    bbox: tuple = None,
    srid: int = 4490,
    resolution: float = None,
    width: int = None,
    height: int = None,
    band_count: int = None,
    acquired_at: str = None,
    sensor: str = None,
    uploaded_by: str = "study_user",
) -> Optional[int]:
    """
    登记一条影像元数据 (UPSERT: layer_name 唯一).
    - bbox: (minx, miny, maxx, maxy), 自动转 SRID=4490 (PostGIS 可用时 ST_Transform)
    - 返回: id; 失败返回 None
    """
    Json = _get_json()
    if Json is None:
        return None
    has_postgis = postgis_available()
    sets_common = [
        "workspace = EXCLUDED.workspace",
        "file_path = COALESCE(EXCLUDED.file_path, image_metadata.file_path)",
        "resolution = COALESCE(EXCLUDED.resolution, image_metadata.resolution)",
        "width = COALESCE(EXCLUDED.width, image_metadata.width)",
        "height = COALESCE(EXCLUDED.height, image_metadata.height)",
        "band_count = COALESCE(EXCLUDED.band_count, image_metadata.band_count)",
        "acquired_at = COALESCE(EXCLUDED.acquired_at, image_metadata.acquired_at)",
        "sensor = COALESCE(EXCLUDED.sensor, image_metadata.sensor)",
        "uploaded_by = COALESCE(EXCLUDED.uploaded_by, image_metadata.uploaded_by)",
        "uploaded_at = NOW()",
    ]
    bbox_json = None
    if bbox and len(bbox) == 4:
        bbox_json = {"minx": float(bbox[0]), "miny": float(bbox[1]),
                     "maxx": float(bbox[2]), "maxy": float(bbox[3]), "srid": int(srid)}
    sets_common.append("bbox_jsonb = COALESCE(EXCLUDED.bbox_jsonb, image_metadata.bbox_jsonb)")

    cols = ["layer_name", "workspace", "file_path", "resolution", "width", "height",
            "band_count", "acquired_at", "sensor", "uploaded_by", "bbox_jsonb"]
    vals = [layer_name, workspace, file_path, resolution, width, height,
            band_count, acquired_at, sensor, uploaded_by, Json(bbox_json) if bbox_json else None]

    # PostGIS 可用: 多插一列 bbox_geom
    if has_postgis and bbox and len(bbox) == 4:
        cols.append("bbox_geom")
        wkt = _bbox_to_wkt(bbox, srid)
        if srid == 4490:
            vals.append(f"ST_GeomFromText('{wkt}', 4490)")
            sets_common.append("bbox_geom = COALESCE(EXCLUDED.bbox_geom, image_metadata.bbox_geom)")
        else:
            # 非目标 SRID → 转 4490
            vals.append(f"ST_Transform(ST_SetSRID(ST_GeomFromText('{wkt}'), {int(srid)}), 4490)")
            sets_common.append("bbox_geom = COALESCE(EXCLUDED.bbox_geom, image_metadata.bbox_geom)")
        # 注意: 含 SQL 函数的值要原样拼入, 不能用 %s 占位 (用 placeholder 表)
        placeholders = []
        idx = 0
        sql_vals = []
        for c in cols:
            v = vals[idx]
            if isinstance(v, str) and v.startswith("ST_"):
                placeholders.append(v)  # SQL 函数原样
            else:
                placeholders.append("%s")
                sql_vals.append(v)
            idx += 1
    else:
        placeholders = ["%s"] * len(cols)
        sql_vals = list(vals)

    cols_sql = ", ".join(cols)
    ph_sql = ", ".join(placeholders)
    on_conflict_sets = ", ".join(sets_common)
    sql = (
        f"INSERT INTO image_metadata ({cols_sql}) VALUES ({ph_sql}) "
        f"ON CONFLICT (layer_name) DO UPDATE SET {on_conflict_sets} "
        f"RETURNING id"
    )
    with get_conn() as conn:
        if conn is None:
            return None
        try:
            with conn.cursor() as cur:
                cur.execute(sql, sql_vals)
                row = cur.fetchone()
            conn.commit()
            mid = row[0] if row else None
            if mid:
                logger.info(f"[BusinessDB] 影像元数据登记: layer={layer_name} id={mid} bbox={'有' if bbox else '无'} postgis={has_postgis}")
            return mid
        except Exception as e:
            conn.rollback()
            logger.error(f"[BusinessDB] 影像元数据登记失败 [{layer_name}]: {e}")
            return None


def register_vector_layer(
    layer_name: str,
    kind: str = "change",
    workspace: str = None,
    file_path: str = None,
    source_task_id: int = None,
    bbox: tuple = None,
    srid: int = 4490,
    feature_count: int = None,
    total_area_m2: float = None,
    attrs: dict = None,
) -> Optional[int]:
    """
    登记一条矢量图层元数据 (UPSERT: layer_name 唯一).
    - kind: rule / change / administrative
    - bbox: (minx, miny, maxx, maxy)
    - 返回: id; 失败返回 None
    """
    Json = _get_json()
    if Json is None:
        return None
    has_postgis = postgis_available()
    bbox_json = None
    if bbox and len(bbox) == 4:
        bbox_json = {"minx": float(bbox[0]), "miny": float(bbox[1]),
                     "maxx": float(bbox[2]), "maxy": float(bbox[3]), "srid": int(srid)}

    sets_common = [
        "kind = EXCLUDED.kind",
        "workspace = COALESCE(EXCLUDED.workspace, vector_layer.workspace)",
        "file_path = COALESCE(EXCLUDED.file_path, vector_layer.file_path)",
        "source_task_id = COALESCE(EXCLUDED.source_task_id, vector_layer.source_task_id)",
        "feature_count = COALESCE(EXCLUDED.feature_count, vector_layer.feature_count)",
        "total_area_m2 = COALESCE(EXCLUDED.total_area_m2, vector_layer.total_area_m2)",
        "attrs = COALESCE(EXCLUDED.attrs, vector_layer.attrs)",
        "bbox_jsonb = COALESCE(EXCLUDED.bbox_jsonb, vector_layer.bbox_jsonb)",
    ]
    cols = ["layer_name", "kind", "workspace", "file_path", "source_task_id",
            "feature_count", "total_area_m2", "attrs", "bbox_jsonb"]
    vals = [layer_name, kind, workspace, file_path, source_task_id,
            feature_count, total_area_m2, Json(attrs) if attrs else None,
            Json(bbox_json) if bbox_json else None]

    if has_postgis and bbox and len(bbox) == 4:
        cols.append("bbox_geom")
        wkt = _bbox_to_wkt(bbox, srid)
        if srid == 4490:
            vals.append(f"ST_GeomFromText('{wkt}', 4490)")
        else:
            vals.append(f"ST_Transform(ST_SetSRID(ST_GeomFromText('{wkt}'), {int(srid)}), 4490)")
        sets_common.append("bbox_geom = COALESCE(EXCLUDED.bbox_geom, vector_layer.bbox_geom)")
        placeholders = []
        sql_vals = []
        for v in vals:
            if isinstance(v, str) and v.startswith("ST_"):
                placeholders.append(v)
            else:
                placeholders.append("%s")
                sql_vals.append(v)
    else:
        placeholders = ["%s"] * len(cols)
        sql_vals = list(vals)

    cols_sql = ", ".join(cols)
    ph_sql = ", ".join(placeholders)
    on_conflict_sets = ", ".join(sets_common)
    sql = (
        f"INSERT INTO vector_layer ({cols_sql}) VALUES ({ph_sql}) "
        f"ON CONFLICT (layer_name) DO UPDATE SET {on_conflict_sets} "
        f"RETURNING id"
    )
    with get_conn() as conn:
        if conn is None:
            return None
        try:
            with conn.cursor() as cur:
                cur.execute(sql, sql_vals)
                row = cur.fetchone()
            conn.commit()
            vid = row[0] if row else None
            if vid:
                logger.info(f"[BusinessDB] 矢量图层登记: layer={layer_name} kind={kind} id={vid} features={feature_count}")
            return vid
        except Exception as e:
            conn.rollback()
            logger.error(f"[BusinessDB] 矢量图层登记失败 [{layer_name}]: {e}")
            return None


def query_images_by_time(start: str = None, end: str = None, limit: int = 100) -> List[Dict[str, Any]]:
    """
    按获取时间范围检索影像元数据 (#4 时间检索).
    - start/end: ISO 时间字符串 (YYYY-MM-DD 或 YYYY-MM-DDTHH:MM:SS), 任一可省略
    返回: [{id, layer_name, acquired_at, resolution, bbox, ...}]
    """
    where = []
    params = []
    if start:
        where.append("acquired_at >= %s")
        params.append(start)
    if end:
        where.append("acquired_at <= %s")
        params.append(end)
    where_clause = ("WHERE " + " AND ".join(where)) if where else ""
    params.append(limit)
    with get_conn() as conn:
        if conn is None:
            return []
        try:
            with conn.cursor() as cur:
                cur.execute(f"""
                    SELECT id, layer_name, workspace, file_path, sensor, acquired_at,
                           resolution, bbox_jsonb, srid, width, height, band_count, uploaded_at
                    FROM image_metadata
                    {where_clause}
                    ORDER BY acquired_at DESC NULLS LAST
                    LIMIT %s
                """, params)
                rows = cur.fetchall()
            return [
                {
                    "id": r[0], "layer_name": r[1], "workspace": r[2], "file_path": r[3],
                    "sensor": r[4], "acquired_at": r[5].isoformat() if r[5] else None,
                    "resolution": r[6], "bbox": r[7], "srid": r[8],
                    "width": r[9], "height": r[10], "band_count": r[11],
                    "uploaded_at": r[12].isoformat() if r[12] else None,
                }
                for r in rows
            ]
        except Exception as e:
            logger.error(f"[BusinessDB] 时间检索失败: {e}")
            return []


def query_images_by_region(bbox: tuple, limit: int = 100) -> List[Dict[str, Any]]:
    """
    按空间范围检索影像元数据 (#4 区域检索).
    - bbox: (minx, miny, maxx, maxy), SRID=4490
    - PostGIS 可用: ST_Intersects 数据库内过滤; 否则 Python 内存过滤 bbox_jsonb
    """
    minx, miny, maxx, maxy = bbox
    has_postgis = postgis_available()
    with get_conn() as conn:
        if conn is None:
            return []
        try:
            with conn.cursor() as cur:
                if has_postgis:
                    wkt = _bbox_to_wkt(bbox, 4490)
                    cur.execute(f"""
                        SELECT id, layer_name, workspace, file_path, sensor, acquired_at,
                               resolution, bbox_jsonb, srid, width, height, band_count, uploaded_at
                        FROM image_metadata
                        WHERE bbox_geom IS NOT NULL
                          AND ST_Intersects(bbox_geom, ST_GeomFromText(%s, 4490))
                        ORDER BY acquired_at DESC NULLS LAST
                        LIMIT %s
                    """, (wkt, limit))
                else:
                    cur.execute("""
                        SELECT id, layer_name, workspace, file_path, sensor, acquired_at,
                               resolution, bbox_jsonb, srid, width, height, band_count, uploaded_at
                        FROM image_metadata
                        WHERE bbox_jsonb IS NOT NULL
                        ORDER BY acquired_at DESC NULLS LAST
                    """)
                    rows = cur.fetchall()
                    # Python 内存过滤 bbox_jsonb
                    rows = [r for r in rows if r[7] and _bbox_intersects(r[7], minx, miny, maxx, maxy)][:limit]
                    return [
                        {
                            "id": r[0], "layer_name": r[1], "workspace": r[2], "file_path": r[3],
                            "sensor": r[4], "acquired_at": r[5].isoformat() if r[5] else None,
                            "resolution": r[6], "bbox": r[7], "srid": r[8],
                            "width": r[9], "height": r[10], "band_count": r[11],
                            "uploaded_at": r[12].isoformat() if r[12] else None,
                        }
                        for r in rows
                    ]
                rows = cur.fetchall()
            return [
                {
                    "id": r[0], "layer_name": r[1], "workspace": r[2], "file_path": r[3],
                    "sensor": r[4], "acquired_at": r[5].isoformat() if r[5] else None,
                    "resolution": r[6], "bbox": r[7], "srid": r[8],
                    "width": r[9], "height": r[10], "band_count": r[11],
                    "uploaded_at": r[12].isoformat() if r[12] else None,
                }
                for r in rows
            ]
        except Exception as e:
            logger.error(f"[BusinessDB] 区域检索失败: {e}")
            return []


def _bbox_intersects(bbox_json: dict, minx, miny, maxx, maxy) -> bool:
    """JSONB bbox 内存相交判断 (无 PostGIS 时的降级)."""
    try:
        a_minx = float(bbox_json.get("minx", 0))
        a_miny = float(bbox_json.get("miny", 0))
        a_maxx = float(bbox_json.get("maxx", 0))
        a_maxy = float(bbox_json.get("maxy", 0))
        return not (a_maxx < minx or a_minx > maxx or a_maxy < miny or a_miny > maxy)
    except Exception:
        return False


def list_image_metadata(limit: int = 100) -> List[Dict[str, Any]]:
    """列出全部影像元数据 (按登记时间倒序)."""
    with get_conn() as conn:
        if conn is None:
            return []
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT id, layer_name, workspace, file_path, sensor, acquired_at,
                           resolution, bbox_jsonb, srid, width, height, band_count, uploaded_at
                    FROM image_metadata
                    ORDER BY uploaded_at DESC
                    LIMIT %s
                """, (limit,))
                rows = cur.fetchall()
            return [
                {
                    "id": r[0], "layer_name": r[1], "workspace": r[2], "file_path": r[3],
                    "sensor": r[4], "acquired_at": r[5].isoformat() if r[5] else None,
                    "resolution": r[6], "bbox": r[7], "srid": r[8],
                    "width": r[9], "height": r[10], "band_count": r[11],
                    "uploaded_at": r[12].isoformat() if r[12] else None,
                }
                for r in rows
            ]
        except Exception as e:
            logger.error(f"[BusinessDB] 列出元数据失败: {e}")
            return []


def list_vector_layers(kind: str = None, limit: int = 100) -> List[Dict[str, Any]]:
    """列出矢量图层元数据, 可按 kind 过滤."""
    where = "WHERE kind = %s" if kind else ""
    params = [kind, limit] if kind else [limit]
    with get_conn() as conn:
        if conn is None:
            return []
        try:
            with conn.cursor() as cur:
                cur.execute(f"""
                    SELECT id, layer_name, workspace, kind, file_path, source_task_id,
                           bbox_jsonb, feature_count, total_area_m2, attrs, created_at
                    FROM vector_layer
                    {where}
                    ORDER BY created_at DESC
                    LIMIT %s
                """, params)
                rows = cur.fetchall()
            return [
                {
                    "id": r[0], "layer_name": r[1], "workspace": r[2], "kind": r[3],
                    "file_path": r[4], "source_task_id": r[5], "bbox": r[6],
                    "feature_count": r[7], "total_area_m2": r[8], "attrs": r[9],
                    "created_at": r[10].isoformat() if r[10] else None,
                }
                for r in rows
            ]
        except Exception as e:
            logger.error(f"[BusinessDB] 列出矢量图层失败: {e}")
            return []
