# -*- coding: utf-8 -*-
"""init_db.py — 数据库建表 + 内置《默认管路模型》初始化（V6.2）

职责：
1. 建立 SQLite 表结构（projects 表）；
2. 从只读的 02 Calculation Sheet Excel 中读取：
   - 默认管路模型_管段明细参数.xlsx（25 个管段：Q / G / l / Σξ）
   - 默认管路模型_环路参数.xlsx（6 条环路的“按水流方向”管段序列）
3. 自动推导拓扑：
   - 建立管段之间的有向邻接（如 7→8、8→10、8→9）；
   - 管段出度 > 1 处生成“分叉节点”，入度 > 1 处生成“汇合节点”；
   - 单入单出相邻处生成 N_7_8 形式的节点；
   - 首段前为热源节点（入度 0），末段后为回水末端节点（出度 0）；
   - 每条管段对应一条 edge，并补齐 dn_locked=None 与 source_node/target_node/edge_id。
4. 若 projects 表为空，写入项目名“教科书默认案例（图4-3-1）”。

本脚本只读 02 Calculation Sheet，不修改原始 Excel；不涉及任何水力计算。
"""
from __future__ import annotations

import os
import re
import uuid
from pathlib import Path

import pandas as pd
from sqlalchemy import inspect, text

import auth
from database import Base, SessionLocal, engine
from models_db import Project
from models_user import User

BASE_DIR = Path(__file__).resolve().parent
CALC_SHEET_DIR = BASE_DIR.parent / "02 Calculation Sheet"
SEGMENT_EXCEL = CALC_SHEET_DIR / "默认管路模型_管段明细参数.xlsx"
LOOP_EXCEL = CALC_SHEET_DIR / "默认管路模型_环路参数.xlsx"

DEFAULT_PROJECT_NAME = "教科书默认案例（图4-3-1）"
DEFAULT_ADMIN_USERNAME = os.environ.get("HEATING_APP_ADMIN_USERNAME", "admin").strip() or "admin"


def _require_admin_password() -> str:
    """读取初始管理员密码；缺失时抛 RuntimeError，禁止回退到固定弱密码。"""
    password = os.environ.get("HEATING_APP_ADMIN_PASSWORD", "")
    if not password:
        raise RuntimeError(
            "缺少环境变量 HEATING_APP_ADMIN_PASSWORD：首次初始化必须显式配置管理员密码，"
            "禁止使用固定弱密码。例如：set HEATING_APP_ADMIN_PASSWORD=<强密码>"
        )
    return password

SOURCE_NODE = "N_SOURCE"
SINK_NODE = "N_SINK"


# ---------------------------------------------------------------------------
# Excel 读取
# ---------------------------------------------------------------------------
def _read_sheet(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(f"未找到默认模型 Excel：{path}")
    frame = pd.read_excel(path, sheet_name="Sheet1", header=1)
    return frame.dropna(how="all")


def _find_column(columns, *keywords: str, required: bool = True) -> str | None:
    for column in columns:
        text = str(column)
        if all(keyword in text for keyword in keywords):
            return column
    if required:
        raise ValueError(f"Excel 表头缺少包含 {keywords!r} 的列；实际列：{list(columns)!r}")
    return None


def load_segment_rows() -> list[dict]:
    """读取 25 个管段（保留原始表内容）。"""
    frame = _read_sheet(SEGMENT_EXCEL)
    id_col = _find_column(frame.columns, "管段ID") or _find_column(frame.columns, "管段", "ID")
    name_col = _find_column(frame.columns, "管段名称", required=False)
    q_col = _find_column(frame.columns, "热负荷")
    g_col = _find_column(frame.columns, "流量")
    l_col = _find_column(frame.columns, "管段长度")
    zeta_col = _find_column(frame.columns, "局部阻力")

    segments: list[dict] = []
    for _, row in frame.iterrows():
        if pd.isna(row[id_col]):
            continue
        segment_id = int(row[id_col])
        segments.append(
            {
                "segment_id": segment_id,
                "segment_name": str(row[name_col]).strip() if name_col and not pd.isna(row[name_col]) else f"管段{segment_id}",
                "Q": float(row[q_col]),
                "G": float(row[g_col]),
                "l": float(row[l_col]),
                "zeta_sum": float(row[zeta_col]),
            }
        )
    if not segments:
        raise RuntimeError("默认管段表为空，无法初始化默认模型")
    ids = [segment["segment_id"] for segment in segments]
    if len(ids) != len(set(ids)):
        raise RuntimeError(f"管段 ID 重复：{ids!r}")
    return segments


def load_loop_sequences(known_segment_ids: set[int]) -> list[dict]:
    """读取 6 条环路的“按水流方向”管段序列。"""
    frame = _read_sheet(LOOP_EXCEL)
    loop_id_col = _find_column(frame.columns, "环路ID")
    loop_name_col = _find_column(frame.columns, "环路名称", required=False)
    segment_list_col = None
    for column in frame.columns:
        text = str(column)
        if "管段" in text and "包含" in text:
            segment_list_col = column
            break
    if segment_list_col is None:
        segment_list_col = _find_column(frame.columns, "管段")
    if segment_list_col is None:
        raise ValueError(f"环路 Excel 未找到管段序列列；实际列：{list(frame.columns)!r}")

    loops: list[dict] = []
    for _, row in frame.iterrows():
        if pd.isna(row[loop_id_col]):
            continue
        raw_segments = str(row[segment_list_col])
        sequence = [int(token) for token in re.findall(r"\d+", raw_segments)]
        if not sequence:
            raise ValueError(f"环路 {row[loop_id_col]!r} 的管段序列为空：{raw_segments!r}")
        unknown = sorted(set(sequence) - known_segment_ids)
        if unknown:
            raise ValueError(f"环路 {row[loop_id_col]!r} 引用了未知管段：{unknown!r}")
        loops.append(
            {
                "loop_id": str(row[loop_id_col]),
                "loop_name": str(row[loop_name_col]).strip() if loop_name_col and not pd.isna(row[loop_name_col]) else str(row[loop_id_col]),
                "segment_ids": sequence,
            }
        )
    if not loops:
        raise RuntimeError("默认环路表为空，无法推导拓扑")
    return loops


# ---------------------------------------------------------------------------
# 拓扑自动推导
# ---------------------------------------------------------------------------
def derive_topology(segments: list[dict], loops: list[dict]) -> dict:
    """由管段表 + 环路序列自动推导 nodes / edges / segments。

    返回 dict：{"nodes": [...], "edges": [...], "segments": [...], "loops": [...], "calculationResult": None}
    """
    segment_ids = [segment["segment_id"] for segment in segments]
    segment_by_id = {segment["segment_id"]: segment for segment in segments}

    successors: dict[int, list[int]] = {segment_id: [] for segment_id in segment_ids}
    predecessors: dict[int, list[int]] = {segment_id: [] for segment_id in segment_ids}
    for loop in loops:
        sequence = loop["segment_ids"]
        for left, right in zip(sequence, sequence[1:]):
            if left == right:
                raise ValueError(f"环路 {loop['loop_id']} 出现自环管段 {left}")
            if right not in successors[left]:
                successors[left].append(right)
            if left not in predecessors[right]:
                predecessors[right].append(left)

    sources = [segment_id for segment_id in segment_ids if not predecessors[segment_id]]
    sinks = [segment_id for segment_id in segment_ids if not successors[segment_id]]
    if len(sources) != 1:
        raise ValueError(f"默认模型应为单热源：入度为 0 的管段应恰好 1 个，实际 {sources!r}")
    if len(sinks) != 1:
        raise ValueError(f"默认模型应汇入单一回水末端：出度为 0 的管段应恰好 1 个，实际 {sinks!r}")

    def pair_node(left: int, right: int) -> str:
        return f"N_{left}_{right}"

    def start_node(segment_id: int) -> str:
        preds = predecessors[segment_id]
        if not preds:
            return SOURCE_NODE
        if len(preds) > 1:
            return f"N_{segment_id}_merge"
        pred = preds[0]
        if len(successors[pred]) > 1:
            return f"N_{pred}_split"
        return pair_node(pred, segment_id)

    def end_node(segment_id: int) -> str:
        succs = successors[segment_id]
        if not succs:
            return SINK_NODE
        if len(succs) > 1:
            return f"N_{segment_id}_split"
        succ = succs[0]
        if len(predecessors[succ]) > 1:
            return f"N_{succ}_merge"
        return pair_node(segment_id, succ)

    # 一致性校验：相邻管段 a→b 处，“a 的终点节点”必须等于“b 的起点节点”
    for left, rights in successors.items():
        for right in rights:
            if end_node(left) != start_node(right):
                raise RuntimeError(
                    f"拓扑推导不一致：管段 {left}→{right} 的节点 "
                    f"{end_node(left)!r} != {start_node(right)!r}（存在同时分叉+汇合的复合结构）"
                )

    def node_label(node_id: str) -> tuple[str, str]:
        """返回 (label, nodeType)。"""
        if node_id == SOURCE_NODE:
            return "锅炉供水（热源）", "boiler"
        if node_id == SINK_NODE:
            return "回水末端（热汇）", "return"
        match = re.fullmatch(r"N_(\d+)_split", node_id)
        if match:
            return f"分叉节点（管段{match.group(1)}后）", "junction"
        match = re.fullmatch(r"N_(\d+)_merge", node_id)
        if match:
            return f"汇合节点（管段{match.group(1)}前）", "junction"
        match = re.fullmatch(r"N_(\d+)_(\d+)", node_id)
        if match:
            return f"节点（{match.group(1)}→{match.group(2)}）", "junction"
        return node_id, "junction"

    # 节点顺序：热源 → 随管段顺序出现的起点/终点 → 末端
    node_ids: list[str] = []
    def register(node_id: str) -> None:
        if node_id not in node_ids:
            node_ids.append(node_id)

    register(SOURCE_NODE)
    for segment_id in segment_ids:
        register(start_node(segment_id))
        register(end_node(segment_id))
    register(SINK_NODE)

    nodes = []
    for index, node_id in enumerate(node_ids):
        label, node_type = node_label(node_id)
        nodes.append(
            {
                "id": node_id,
                "type": "default",
                # 初始给一组网格坐标；前端“一键自动排版”（Dagre）会重新布局
                "position": {"x": (index % 5) * 220, "y": (index // 5) * 120},
                "data": {"label": label, "nodeType": node_type},
            }
        )

    edges = []
    derived_segments = []
    for segment in segments:
        segment_id = segment["segment_id"]
        edge_id = f"edge_{segment_id}"
        start = start_node(segment_id)
        end = end_node(segment_id)
        edges.append(
            {
                "id": edge_id,
                "source": start,
                "target": end,
                "type": "smoothstep",
                "data": {"status": "ok", "segmentId": str(segment_id)},
            }
        )
        derived_segments.append(
            {
                **segment,
                "dn_locked": None,
                "source_node": start,
                "target_node": end,
                "edge_id": edge_id,
            }
        )

    # 拓扑正确性自检：单热源、单末端、无断头路、全连通、边与管段一一对应
    node_set = {node["id"] for node in nodes}
    indegree = {node_id: 0 for node_id in node_set}
    outdegree = {node_id: 0 for node_id in node_set}
    for edge in edges:
        indegree[edge["target"]] += 1
        outdegree[edge["source"]] += 1
    if len(edges) != len(segments):
        raise RuntimeError(f"边数 {len(edges)} 与管段数 {len(segments)} 不一致")
    if len({edge["source"] for edge in edges} | {edge["target"] for edge in edges}) != len(nodes):
        raise RuntimeError("存在未连接的孤立节点")
    source_nodes = [node_id for node_id in node_set if indegree[node_id] == 0]
    sink_nodes = [node_id for node_id in node_set if outdegree[node_id] == 0]
    if source_nodes != [SOURCE_NODE] or sink_nodes != [SINK_NODE]:
        raise RuntimeError(f"单热源/单末端校验失败：sources={source_nodes}, sinks={sink_nodes}")
    for node_id in node_set:
        if node_id != SOURCE_NODE and indegree[node_id] == 0:
            raise RuntimeError(f"断头路：节点 {node_id} 无入边")
        if node_id != SINK_NODE and outdegree[node_id] == 0:
            raise RuntimeError(f"断头路：节点 {node_id} 无出边")
    # 可达性自检（从热源出发全部可达）
    adjacency: dict[str, list[str]] = {node_id: [] for node_id in node_set}
    for edge in edges:
        adjacency[edge["source"]].append(edge["target"])
    visited: set[str] = set()
    stack = [SOURCE_NODE]
    while stack:
        node = stack.pop()
        if node in visited:
            continue
        visited.add(node)
        stack.extend(adjacency[node])
    if visited != node_set:
        raise RuntimeError(f"存在不可达节点：{sorted(node_set - visited)!r}")

    return {
        "nodes": nodes,
        "edges": edges,
        "segments": derived_segments,
        "loops": loops,
        "calculationResult": None,
    }


def derive_default_model() -> dict:
    """读取 Excel 并推导默认模型数据（不写数据库）。"""
    segments = load_segment_rows()
    loops = load_loop_sequences({segment["segment_id"] for segment in segments})
    return derive_topology(segments, loops)


# ---------------------------------------------------------------------------
# 数据库初始化
# ---------------------------------------------------------------------------
def _migrate_projects_user_id_column() -> None:
    """SQLite 在线迁移：为已存在的 projects 表增加 user_id 列（不删库/不删默认案例）。"""
    inspector = inspect(engine)
    if "projects" not in inspector.get_table_names():
        return
    columns = {column["name"] for column in inspector.get_columns("projects")}
    if "user_id" not in columns:
        with engine.begin() as connection:
            connection.execute(
                text("ALTER TABLE projects ADD COLUMN user_id VARCHAR(36) REFERENCES users(id)")
            )
        print("[init_db] 已迁移 projects 表：新增 user_id 列（保留原有案例数据）")
    # 为旧库补建 user_id 查询索引（新库由 SQLAlchemy index=True 自动创建）
    with engine.begin() as connection:
        connection.execute(
            text("CREATE INDEX IF NOT EXISTS ix_projects_user_id ON projects (user_id)")
        )


def _ensure_admin_user(session, admin_password: str) -> User:
    """确保初始管理员存在；密码由 HEATING_APP_ADMIN_PASSWORD 显式提供。"""
    admin = session.query(User).filter(User.username == DEFAULT_ADMIN_USERNAME).first()
    if admin is not None:
        return admin
    admin = User(
        id=str(uuid.uuid4()),
        username=DEFAULT_ADMIN_USERNAME,
        hashed_password=auth.hash_password(admin_password),
    )
    session.add(admin)
    session.commit()
    session.refresh(admin)
    print(f"[init_db] 已创建默认系统用户：{DEFAULT_ADMIN_USERNAME}（密码哈希存储）")
    return admin


def ensure_database() -> bool:
    """建立表结构并执行 V8.3 认证/多租户初始化。

    执行顺序：
    1. create_all：创建 users / projects 表；
    2. 若旧 SQLite 的 projects 表没有 user_id 列 → ALTER TABLE 增加该列（保留数据）；
    3. 确保 admin 用户存在（PBKDF2 密码哈希）；
    4. 将所有 user_id 为空的既有案例（含原默认案例）绑定到 admin；
    5. 若 projects 表为空，则写入内置默认模型并归属 admin。

    @returns True 表示本次写入默认案例；False 表示数据库已有数据。
    """
    admin_password = _require_admin_password()
    Base.metadata.create_all(bind=engine)
    with SessionLocal() as session:
        _migrate_projects_user_id_column()
        admin = _ensure_admin_user(session, admin_password)

        unbound_projects = session.query(Project).filter(Project.user_id.is_(None)).all()
        if unbound_projects:
            for project in unbound_projects:
                project.user_id = admin.id
            session.commit()
            print(
                f"[init_db] 已将 {len(unbound_projects)} 个既有案例归属到 {DEFAULT_ADMIN_USERNAME}"
                "（包含默认案例，数据未删除）"
            )

        existing_count = session.query(Project).count()
        if existing_count > 0:
            print(f"[init_db] projects 已有 {existing_count} 条记录，跳过默认模型初始化")
            return False

        data = derive_default_model()
        project = Project(
            id=str(uuid.uuid4()),
            name=DEFAULT_PROJECT_NAME,
            data=data,
            user_id=admin.id,
        )
        session.add(project)
        session.commit()
        session.refresh(project)
        print(
            "[init_db] 已写入默认模型："
            f"project_id={project.id}, name={project.name}, user_id={admin.id}, "
            f"nodes={len(data['nodes'])}, edges={len(data['edges'])}, segments={len(data['segments'])}"
        )
        return True


if __name__ == "__main__":
    created = ensure_database()
    data = derive_default_model()
    print(f"[init_db] 默认模型推导结果：nodes={len(data['nodes'])}, edges={len(data['edges'])}, segments={len(data['segments'])}")
    print("[init_db] nodes 示例（前 5 个）：")
    for node in data["nodes"][:5]:
        print("   ", node)
    print("[init_db] edges 示例（前 5 条）：")
    for edge in data["edges"][:5]:
        print("   ", edge)
    print("[init_db] 本次是否新写入默认案例：", created)
