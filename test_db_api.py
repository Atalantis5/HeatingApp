# -*- coding: utf-8 -*-
"""test_db_api.py — V6.2 SQLite 案例持久化与默认模型测试

覆盖：
1. 默认模型推导：读取 02 Calculation Sheet 的 2 个只读 Excel，断言：
   - 25 个管段、21 个节点、25 条边；
   - 管段 Q / l / Σξ 与 Excel 逐行完全一致，dn_locked 全部为 None；
   - 单热源、单末端、无断头路、全连通、有向无环（tree-like DAG）。
2. Projects CRUD：GET 列表（仅 id/name/updated_at）、GET 详情、POST、PUT、DELETE、404。
3. SQLite 文件持久化：写入后释放 session，重新连接同一数据库文件仍可读取（重启等价）。

运行：pytest test_db_api.py -q
"""
from __future__ import annotations

import uuid
from pathlib import Path

import networkx as nx
import pandas as pd
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import init_db
import main
from auth import get_current_user
from database import Base, get_db
from models_db import Project
from models_user import User

CALC_SHEET_DIR = Path(__file__).resolve().parent.parent / "02 Calculation Sheet"
SEGMENT_EXCEL = CALC_SHEET_DIR / "默认管路模型_管段明细参数.xlsx"
LOOP_EXCEL = CALC_SHEET_DIR / "默认管路模型_环路参数.xlsx"


def _make_engine(database_url: str, **kwargs):
    return create_engine(database_url, connect_args={"check_same_thread": False}, future=True, **kwargs)


@pytest.fixture()
def client():
    """内存 SQLite + dependency_overrides 的 TestClient（不触碰真实 projects.db）。"""
    engine = _make_engine("sqlite://", poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    TestingSession = sessionmaker(
        bind=engine, autoflush=False, autocommit=False, expire_on_commit=False, future=True
    )

    def override_get_db():
        db = TestingSession()
        try:
            yield db
        finally:
            db.close()

    main.app.dependency_overrides[get_db] = override_get_db

    # V8.3：/projects 已加登录依赖；CRUD 单元测试使用固定测试用户覆盖认证依赖
    with TestingSession() as session:
        test_user = User(id="test-user-id", username="pytest-user", hashed_password="not-used")
        session.add(test_user)
        session.commit()
        session.refresh(test_user)

    def override_get_current_user():
        return test_user

    main.app.dependency_overrides[get_current_user] = override_get_current_user
    test_client = TestClient(main.app)
    try:
        yield test_client
    finally:
        main.app.dependency_overrides.clear()
        engine.dispose()


# ---------------------------------------------------------------------------
# [1] 默认模型推导（读取 Excel，与原始数据逐项比对）
# ---------------------------------------------------------------------------
def test_default_model_derivation_matches_excel():
    data = init_db.derive_default_model()
    nodes, edges, segments = data["nodes"], data["edges"], data["segments"]

    assert len(data["nodes"]) == 21
    assert len(edges) == 25
    assert len(segments) == 25
    assert [node["id"] for node in nodes].count(init_db.SOURCE_NODE) == 1

    # 管段数据与 Excel 完全一致（Q / l / Σξ），dn_locked 统一 None
    excel = pd.read_excel(SEGMENT_EXCEL, sheet_name="Sheet1", header=1)
    excel_rows = excel[excel["管段ID"].notna()].set_index(excel["管段ID"].astype(int))
    db_rows = {int(segment["segment_id"]): segment for segment in segments}
    assert set(db_rows) == set(excel_rows.index)
    for segment_id, excel_row in excel_rows.iterrows():
        segment = db_rows[segment_id]
        assert segment["Q"] == pytest.approx(float(excel_row["热负荷Q(W)"]))
        assert segment["l"] == pytest.approx(float(excel_row["管段长度L(m)"]))
        assert segment["zeta_sum"] == pytest.approx(float(excel_row["局部阻力系数之和Σξ"]))
        assert segment["dn_locked"] is None
        assert segment["source_node"] and segment["target_node"] and segment["edge_id"]

    # 环路序列来自第二个 Excel，且被推导为连接关系
    loops_excel = pd.read_excel(LOOP_EXCEL, sheet_name="Sheet1", header=1)
    loop_count = loops_excel["环路ID"].notna().sum()
    assert len(data["loops"]) == loop_count == 6

    # 拓扑正确性：单热源 / 单末端 / 无断头路 / 全连通 / DAG
    graph = nx.DiGraph()
    graph.add_nodes_from(node["id"] for node in nodes)
    for edge in edges:
        graph.add_edge(edge["source"], edge["target"])
    sources = [node for node in graph.nodes if graph.in_degree(node) == 0]
    sinks = [node for node in graph.nodes if graph.out_degree(node) == 0]
    assert sources == [init_db.SOURCE_NODE]
    assert sinks == [init_db.SINK_NODE]
    assert nx.is_directed_acyclic_graph(graph)
    assert set(nx.descendants(graph, init_db.SOURCE_NODE)) | {init_db.SOURCE_NODE} == set(graph.nodes)
    for edge in edges:
        assert edge["source"] in graph and edge["target"] in graph
    # 每条管段恰好对应一条边 / 一个 edge_id
    assert len({segment["edge_id"] for segment in segments}) == 25
    assert {edge["id"] for edge in edges} == {segment["edge_id"] for segment in segments}


# ---------------------------------------------------------------------------
# [2] Projects CRUD
# ---------------------------------------------------------------------------
PROJECT_DATA = {
    "nodes": [{"id": "N_SOURCE", "type": "default", "position": {"x": 0, "y": 0}, "data": {"label": "锅炉"}}],
    "edges": [],
    "segments": [{"segment_id": 1, "Q": 1000, "l": 2, "zeta_sum": 1, "dn_locked": None}],
    "calculationResult": None,
}


def test_projects_crud(client: TestClient):
    # 空列表
    response = client.get("/projects")
    assert response.status_code == 200
    assert response.json() == []

    # 新建
    created = client.post("/projects", json={"name": "CRUD 测试案例", "data": PROJECT_DATA})
    assert created.status_code == 201, created.text
    created_body = created.json()
    project_id = created_body["id"]
    assert len(project_id) == 36
    assert created_body["name"] == "CRUD 测试案例"
    assert created_body["data"] == PROJECT_DATA
    assert created_body["created_at"] and created_body["updated_at"]

    # 列表只返回 id / name / updated_at（契约）
    listing = client.get("/projects").json()
    assert len(listing) == 1
    assert set(listing[0].keys()) == {"id", "name", "updated_at"}
    assert listing[0]["id"] == project_id

    # 详情
    detail = client.get(f"/projects/{project_id}")
    assert detail.status_code == 200
    assert detail.json()["data"] == PROJECT_DATA

    # 更新（覆盖 data）
    new_data = {**PROJECT_DATA, "segments": [{**PROJECT_DATA["segments"][0], "Q": 2000}]}
    updated = client.put(
        f"/projects/{project_id}",
        json={"name": "CRUD 测试案例（已改名）", "data": new_data},
    )
    assert updated.status_code == 200, updated.text
    updated_body = updated.json()
    assert updated_body["name"] == "CRUD 测试案例（已改名）"
    assert updated_body["data"] == new_data
    assert client.get(f"/projects/{project_id}").json()["data"] == new_data

    # 404 场景
    assert client.get("/projects/not-exist").status_code == 404
    assert client.put("/projects/not-exist", json={"data": PROJECT_DATA}).status_code == 404
    assert client.delete("/projects/not-exist").status_code == 404

    # 删除
    deleted = client.delete(f"/projects/{project_id}")
    assert deleted.status_code == 200
    assert deleted.json() == {"deleted": True, "id": project_id}
    assert client.get(f"/projects/{project_id}").status_code == 404
    assert client.get("/projects").json() == []


def test_projects_create_requires_name(client: TestClient):
    assert client.post("/projects", json={"data": PROJECT_DATA}).status_code == 422
    assert client.post("/projects", json={"name": "", "data": PROJECT_DATA}).status_code == 422


# ---------------------------------------------------------------------------
# [3] SQLite 文件持久化（等价于“重启后端后数据仍在”）
# ---------------------------------------------------------------------------
def test_sqlite_file_persists_between_sessions():
    # 不使用 pytest tmp_path（沙箱环境下系统临时目录可能不可读），改用项目内临时文件
    temp_dir = Path(__file__).resolve().parent / ".test_db_tmp"
    temp_dir.mkdir(exist_ok=True)
    db_path = temp_dir / f"test_projects_{uuid.uuid4().hex}.db"
    database_url = f"sqlite:///{db_path.as_posix()}"

    # 第一个“进程”：建表、写入、关闭
    engine1 = _make_engine(database_url)
    Base.metadata.create_all(bind=engine1)
    Session1 = sessionmaker(bind=engine1, expire_on_commit=False, future=True)
    project_id = None
    with Session1() as session:
        project = Project(
            id="11111111-2222-3333-4444-555555555555",
            name="持久化测试案例",
            data={"nodes": [], "edges": [], "segments": [], "calculationResult": None},
        )
        session.add(project)
        session.commit()
        project_id = project.id
    engine1.dispose()

    # 第二个“进程”：重新连接同一 SQLite 文件，数据仍存在
    engine2 = _make_engine(database_url)
    Base.metadata.create_all(bind=engine2)
    Session2 = sessionmaker(bind=engine2, expire_on_commit=False, future=True)
    with Session2() as session:
        restored = session.get(Project, project_id)
        assert restored is not None
        assert restored.name == "持久化测试案例"
        assert restored.data["nodes"] == []
    engine2.dispose()
    assert db_path.exists()
    db_path.unlink(missing_ok=True)
    try:
        temp_dir.rmdir()
    except OSError:
        pass
