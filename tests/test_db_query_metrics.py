"""lea-config 的 DB 查询指标：挂在引擎上，自动覆盖所有 SQL。

为什么挂引擎事件而不是在查询点手动调：手动调漏一处不会报错，就是一个静默的
覆盖缺口。lea_config_db_query_duration_seconds 此前就是这么空的 —— record_db_query
写好了，零个调用点，指标在 /metrics 上只有 HELP/TYPE 两行，
DBQueryLatencyP95High 这条告警因此永远不触发。

这个文件钉住三件事：
  1) 真的产出序列（不是"函数能调通"）
  2) operation / table 两个标签都收敛到有界集合
  3) 查询报错时计时栈不泄漏 —— 连接是池化复用的，泄漏会让后续查询耗时算错
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(
    0, str(Path(__file__).resolve().parents[1] / "packages" / "lea-config" / "src")
)

pytest.importorskip("prometheus_client", reason="需要 prometheus-client")
sqlalchemy = pytest.importorskip("sqlalchemy", reason="需要 sqlalchemy")

from lea_config.metrics import _parse_sql, install_db_metrics  # noqa: E402
from prometheus_client import generate_latest  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402


def _series(prefix: str) -> list[str]:
    text_out = generate_latest().decode("utf-8")
    return [
        ln for ln in text_out.splitlines()
        if ln.startswith(prefix) and not ln.startswith("#")
    ]


def _bucket_series(name: str, **labels: str) -> list[str]:
    """histogram 的 _bucket 行。

    不能用前缀匹配：prometheus_client 输出时 le 排在最前面，长这样
        lea_x_bucket{le="0.001",operation="SELECT",table="agents"}
    所以按"指标名 + 每个标签都出现"来筛。
    """
    text_out = generate_latest().decode("utf-8")
    want = [f'{k}="{v}"' for k, v in labels.items()]
    return [
        ln for ln in text_out.splitlines()
        if ln.startswith(name) and not ln.startswith("#")
        and all(w in ln for w in want)
    ]


@pytest.fixture
def eng():
    e = create_engine("sqlite://")
    install_db_metrics(e)
    with e.begin() as c:
        c.execute(text("CREATE TABLE agents (id INTEGER PRIMARY KEY, name TEXT)"))
    return e


# ── 真的产出序列 ────────────────────────────────────────────────────────────

def test_select_produces_series(eng):
    with eng.begin() as c:
        c.execute(text("SELECT id, name FROM agents"))

    total = _series('lea_config_db_queries_total{operation="SELECT",table="agents"}')
    assert total, "SELECT 没产出序列 —— 埋点没接上"
    assert float(total[0].split()[-1]) >= 1

    assert _series('lea_config_db_query_duration_seconds_count{operation="SELECT",table="agents"}')
    assert _bucket_series(
        "lea_config_db_query_duration_seconds_bucket",
        operation="SELECT", table="agents",
    ), "没有 _bucket 的话 histogram_quantile 永远返回 0 条"


def test_insert_update_delete_recorded_separately(eng):
    with eng.begin() as c:
        c.execute(text("INSERT INTO agents (name) VALUES ('a')"))
        c.execute(text("UPDATE agents SET name='b' WHERE id=1"))
        c.execute(text("DELETE FROM agents WHERE id=1"))

    for op in ("INSERT", "UPDATE", "DELETE"):
        assert _series(
            f'lea_config_db_queries_total{{operation="{op}",table="agents"}}'
        ), f"{op} 没单独计数"


# ── 标签有界 ────────────────────────────────────────────────────────────────

def test_unknown_verb_falls_back_to_other():
    """DDL、PRAGMA、事务控制这些不在白名单里的，归 other，不进标签。"""
    for sql in ("CREATE TABLE x (id INT)", "PRAGMA foo", "BEGIN", "VACUUM"):
        op, table = _parse_sql(sql)
        assert op == "other", f"{sql!r} 的 operation 应该是 other，得到 {op!r}"
        assert table == "other"


def test_table_label_cap_is_enforced():
    """表名是从 SQL 文本正则抠出来的，抠错就是一条无界增长路径。

    所以有 _MAX_TABLES 上限：超了统一归 other。指标失真，但不会把 Prometheus
    的内存吃掉 —— 这是刻意的取舍，无界的那一侧后果严重得多。
    """
    from lea_config import metrics as m

    saved = set(m._seen_tables)
    try:
        m._seen_tables.clear()
        # 灌满上限
        for i in range(m._MAX_TABLES):
            op, table = _parse_sql(f"SELECT * FROM t{i}")
            assert op == "SELECT"
            assert table == f"t{i}"
        assert len(m._seen_tables) == m._MAX_TABLES

        # 再来一个新表名 —— 必须被挡掉
        op, table = _parse_sql("SELECT * FROM overflow_table")
        assert op == "SELECT"
        assert table == "other", "超过上限的表名没有收敛到 other"
        assert "overflow_table" not in m._seen_tables

        # 已经见过的照常记
        op, table = _parse_sql("SELECT * FROM t0")
        assert table == "t0", "已知表名不该受上限影响"
    finally:
        m._seen_tables.clear()
        m._seen_tables.update(saved)


def test_sql_injection_like_text_does_not_create_weird_label():
    """SQL 里带引号/分号的内容不该漏进标签。"""
    for sql in (
        "SELECT * FROM `agents`",
        'SELECT * FROM "agents"',
        "SELECT * FROM agents;",
        "SELECT * FROM main.agents",
    ):
        op, table = _parse_sql(sql)
        assert op == "SELECT"
        assert table == "agents", f"{sql!r} 解析出 {table!r}"


# ── 出错时计时栈不泄漏 ──────────────────────────────────────────────────────

def test_failed_query_does_not_leak_timing_stack(eng):
    """查询报错时 after_cursor_execute **不触发**。

    不在 handle_error 里把栈弹掉的话，每个失败查询都往 conn.info 里留一个时间
    戳。连接是池化复用的（pool_recycle=3600，一条连接能活一小时），于是这个
    列表只增不减。

    后果只是内存泄漏，**不是耗时算错** —— 栈是 LIFO，每个查询弹的是自己压进去
    的那个。真实风险是一个持续报错的查询路径（某张表被删了而代码还在查）把长寿
    连接的 info 列表撑大，而且这种泄漏不会自愈。

    这条测试盯的就是那个 pop：注入缺陷（把 pop 删掉）后它会红，残留深度变成
    [1, 2, 3, 4, 5]。
    """
    from sqlalchemy.exc import SQLAlchemyError

    depths = []
    for _ in range(5):
        try:
            with eng.begin() as c:
                c.execute(text("SELECT * FROM table_that_does_not_exist"))
        except SQLAlchemyError:
            pass
        # 每轮用一个新连接，所以查当前池里那条连接的残留
        with eng.connect() as c:
            depths.append(len(c.connection.info.get("_lea_q_start", [])))

    assert all(d == 0 for d in depths), f"计时栈有残留: {depths}"


#
# 这里原来还有一条 test_duration_not_inflated_after_failures，断言"失败之后的正常
# 查询耗时不会被算成天文数字"。**删掉了**：把 handle_error 里的 pop 注入掉之后它
# 照样通过 —— 因为栈是 LIFO，每个查询弹的是自己压进去的那个时间戳，泄漏的那些沉
# 在栈底不会被碰到。也就是说泄漏的后果只有内存增长，没有耗时算错，那条测试的前提
# 本身是错的，留着就是个永远不会红的装饰。


def test_install_is_idempotent(eng):
    """重复调用只装一次 —— 装两次的话每个查询会被记两遍，计数翻倍。"""
    before = _series('lea_config_db_queries_total{operation="SELECT",table="agents"}')
    base = float(before[0].split()[-1]) if before else 0.0

    install_db_metrics(eng)
    install_db_metrics(eng)
    with eng.begin() as c:
        c.execute(text("SELECT 1 FROM agents"))

    after = _series('lea_config_db_queries_total{operation="SELECT",table="agents"}')
    assert float(after[0].split()[-1]) == base + 1, "重复安装导致了重复计数"
