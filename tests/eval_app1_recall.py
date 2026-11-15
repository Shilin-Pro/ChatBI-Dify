#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
「数据库查询1」召回率测试（父子分段模式）

测两件事：
1. RAG 召回率：知识库检索是否命中"答案所在的文档"
2. 端到端准确率：生成的 SQL 结果是否正确

召回判定：从 Dify 的 workflow_node_executions 表读取 Knowledge Retrieval 节点
         返回的分段，看是否包含期望的文档名

用法:
    set DIFY_APP_KEY=app-xxxx
    set DB_PASSWORD=xxx
    python eval_app1_recall.py
"""
import argparse
import json
import os
import re
import subprocess
import sys
import io
import time
import urllib.error
import urllib.request

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

HERE = os.path.dirname(os.path.abspath(__file__))
BASE_URL = os.environ.get("DIFY_BASE_URL", "http://localhost/v1").rstrip("/")
APP_KEY = os.environ.get("DIFY_APP_KEY", "").strip()
APP_ID = "627f8555-cba4-4667-9c86-219c5055a56c"

MYSQL = os.environ.get("MYSQL_BIN", r"C:\Program Files\MySQL\MySQL Server 8.0\bin\mysql.exe")
DB_PASSWORD = os.environ.get("DB_PASSWORD", "")


def psql(sql):
    """查 Dify 的 postgres（写临时 SQL 文件后 docker cp 进容器执行，避免引号转义问题）"""
    import tempfile
    tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".sql", encoding="utf-8",
                                      delete=False, newline="\n")
    tmp.write(sql)
    tmp.close()
    win_path = tmp.name
    wsl_path = "/mnt/" + win_path[0].lower() + win_path[2:].replace("\\", "/")
    r = subprocess.run(
        ["wsl", "-e", "bash", "-c",
         f"docker cp '{wsl_path}' docker-db_postgres-1:/tmp/q.sql && "
         "docker exec docker-db_postgres-1 psql -U postgres -d dify -t -A -f /tmp/q.sql"],
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    try:
        os.unlink(tmp.name)
    except Exception:
        pass
    return r.stdout


def mysql_query(sql):
    cmd = [MYSQL, "-h", "127.0.0.1", "-u", "root"]
    if DB_PASSWORD:
        cmd.append(f"-p{DB_PASSWORD}")
    cmd += ["--default-character-set=utf8mb4", "--skip-column-names",
            "-e", f"USE cd; {sql}"]
    r = subprocess.run(cmd, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    if r.returncode != 0:
        raise RuntimeError(r.stderr[:200])
    lines = [x.strip() for x in r.stdout.splitlines() if x.strip()]
    return lines[0] if lines else ""


def norm_num(s):
    if isinstance(s, (int, float)):
        return [round(float(s), 2)]
    if not s:
        return []
    keep = [ln for ln in str(s).split("\n")
            if not re.fullmatch(r'[\s|:\-]+', ln.strip())]
    return [round(float(n), 2) for n in re.findall(r'-?\d+\.?\d*', "\n".join(keep).replace(",", ""))]


# ============================================================
# 20 个测试用例
# (id, 分类, 问题, 标准答案SQL, 期望类型, 期望命中的文档关键词)
# 文档关键词用于召回判定：知识库里的文档名包含这些词就算命中
# ============================================================
CASES = [
    # ---- A. 客户表（4）----
    ("A1", "客户表", "一共有多少客户？",
     "SELECT COUNT(*) AS v FROM customers", "num", ["customers"]),
    ("A2", "客户表", "深圳有多少客户？",
     "SELECT COUNT(*) AS v FROM customers WHERE city='深圳'", "num", ["customers"]),
    ("A3", "客户表", "钻石会员有几个？",
     "SELECT COUNT(*) AS v FROM customers WHERE member_level='钻石'", "num", ["customers"]),
    ("A4", "客户表", "女性客户有多少个？",
     "SELECT COUNT(*) AS v FROM customers WHERE gender='F'", "num", ["customers"]),

    # ---- B. 分类表（4）----
    ("B1", "分类表", "一共有几个一级分类？",
     "SELECT COUNT(*) AS v FROM categories WHERE parent_id IS NULL", "num", ["categories"]),
    ("B2", "分类表", "手机通讯属于哪个一级分类？",
     """SELECT c1.name AS v FROM categories c2
        JOIN categories c1 ON c1.id=c2.parent_id WHERE c2.name='手机通讯'""", "text", ["categories"]),
    ("B3", "分类表", "电脑办公分类下有多少个商品？",
     """SELECT COUNT(*) AS v FROM products p JOIN categories c ON c.id=p.category_id
        WHERE c.name='电脑办公'""", "num", ["categories"]),
    ("B4", "分类表", "女装分类的销售额是多少？",
     """SELECT SUM(oi.subtotal) AS v FROM order_items oi
        JOIN products p ON p.id=oi.product_id JOIN categories c ON c.id=p.category_id
        JOIN orders o ON o.id=oi.order_id WHERE o.status='已完成' AND c.name='女装'""",
     "num", ["categories"]),

    # ---- C. 商品表（4）----
    ("C1", "商品表", "一共有多少个商品？",
     "SELECT COUNT(*) AS v FROM products", "num", ["products"]),
    ("C2", "商品表", "库存最多的商品是哪个？",
     "SELECT name AS v FROM products ORDER BY stock DESC LIMIT 1", "text", ["products"]),
    ("C3", "商品表", "MacBook Air 卖了多少件？",
     """SELECT SUM(oi.quantity) AS v FROM order_items oi
        JOIN products p ON p.id=oi.product_id JOIN orders o ON o.id=oi.order_id
        WHERE o.status='已完成' AND p.name LIKE '%MacBook Air%'""", "num", ["products"]),
    ("C4", "商品表", "哪个商品的毛利率最高？",
     """SELECT p.name AS v FROM products p
        WHERE p.id IN (SELECT DISTINCT product_id FROM order_items)
        ORDER BY (p.price-p.cost)/p.price DESC LIMIT 1""", "text", ["products"]),

    # ---- D. 订单表（4）----
    ("D1", "订单表", "一共有多少笔订单？",
     "SELECT COUNT(*) AS v FROM orders", "num", ["orders"]),
    ("D2", "订单表", "已完成状态的订单有多少笔？",
     "SELECT COUNT(*) AS v FROM orders WHERE status='已完成'", "num", ["orders"]),
    ("D3", "订单表", "4月份有多少个订单？",
     """SELECT COUNT(*) AS v FROM orders
        WHERE order_date>='2024-04-01' AND order_date<'2024-05-01'""", "num", ["orders"]),
    ("D4", "订单表", "6月份的销售额是多少？",
     """SELECT SUM(pay_amount) AS v FROM orders WHERE status='已完成'
        AND order_date>='2024-06-01' AND order_date<'2024-07-01'""", "num", ["orders"]),

    # ---- E. 业务规则（4）----
    ("E1", "业务规则", "消费最高的客户是谁？",
     """SELECT c.name AS v FROM customers c JOIN orders o ON o.customer_id=c.id
        WHERE o.status='已完成' GROUP BY c.id,c.name
        ORDER BY SUM(o.pay_amount) DESC LIMIT 1""", "text", ["customers", "orders"]),
    ("E2", "业务规则", "数码电器类商品的销售额是多少？",
     """SELECT SUM(oi.subtotal) AS v FROM order_items oi
        JOIN products p ON p.id=oi.product_id JOIN categories c ON c.id=p.category_id
        JOIN orders o ON o.id=oi.order_id WHERE o.status='已完成'
        AND p.category_id IN (SELECT id FROM categories WHERE name='数码电器'
            UNION SELECT c2.id FROM categories c2 JOIN categories c1 ON c2.parent_id=c1.id
            WHERE c1.name='数码电器')""", "num", ["categories", "order_items"]),
    ("E3", "业务规则", "深圳客户的消费总额是多少？",
     """SELECT SUM(o.pay_amount) AS v FROM customers c JOIN orders o ON o.customer_id=c.id
        WHERE c.city='深圳' AND o.status='已完成'""", "num", ["customers", "orders"]),
    ("E4", "业务规则", "哪些商品从来没被卖出去过？",
     """SELECT p.name AS v FROM products p
        WHERE p.id NOT IN (SELECT DISTINCT product_id FROM order_items) LIMIT 1""",
     "text", ["products", "order_items"]),
]


def call_workflow(question, timeout=400):
    payload = json.dumps({
        "inputs": {"text": question},
        "response_mode": "blocking",
        "user": "recall-test",
    }, ensure_ascii=False).encode("utf-8")

    req = urllib.request.Request(
        f"{BASE_URL}/workflows/run", data=payload,
        headers={"Authorization": f"Bearer {APP_KEY}",
                 "Content-Type": "application/json; charset=utf-8"},
        method="POST")
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = json.loads(resp.read().decode())
    except Exception as e:
        return {"ok": False, "err": str(e), "ms": int((time.perf_counter()-t0)*1000)}
    ms = int((time.perf_counter() - t0) * 1000)
    d = body.get("data", {})
    o = d.get("outputs", {}) or {}
    return {
        "ok": d.get("status") in ("succeeded", "partial-succeeded"),
        "status": d.get("status"),
        "data": (o.get("data") or "").strip(),
        "ans": (o.get("ans") or "").strip(),
        "err_text": (o.get("err_text") or "").strip(),
        "ms": ms,
        "err": d.get("error"),
    }


def get_recall_info(question):
    """从 DB 读取最近一次该问题的 RAG 检索结果

    返回 [{"document_name":..., "content":..., "score":...}, ...]
    注意：知识库是父子分段，检索命中子段但返回父段内容，
          所以判定要基于 content，不能只靠文档名。
    """
    safe_q = question.replace("'", "''")
    sql = f"""
    WITH q AS (
      SELECT r.id
      FROM workflow_runs r
      JOIN workflows w ON w.id = r.workflow_id
      JOIN workflow_node_executions n ON n.workflow_run_id = r.id AND n.title = 'User Input'
      WHERE w.app_id = '{APP_ID}'
        AND n.outputs::jsonb->>'text' = '{safe_q}'
      ORDER BY r.created_at DESC LIMIT 1
    )
    SELECT COALESCE(n.outputs::text, '')
    FROM q LEFT JOIN workflow_node_executions n
      ON n.workflow_run_id = q.id AND n.title = 'Knowledge Retrieval';
    """
    out = psql(sql).strip()
    if not out:
        return None

    try:
        d = json.loads(out)
    except Exception:
        return None

    records = d.get("result", []) or []
    docs = []
    for rec in records:
        meta = rec.get("metadata", {}) or {}
        docs.append({
            "document_name": meta.get("document_name", ""),
            "content": rec.get("content", "") or "",
            "score": rec.get("score"),
        })
    return docs


def judge_recall(docs, keywords):
    """判断召回是否命中

    在返回的分段内容里找「【表名】：xxx」，xxx 命中期望表名即算召回成功。
    返回 (是否命中, 排名, 命中的表名, 该段文档名)
    """
    if not docs:
        return False, None, None, None

    for i, d in enumerate(docs, 1):
        content = (d.get("content") or "").lower()
        for kw in keywords:
            if f"【表名】：{kw}" in content:
                return True, i, kw, d.get("document_name", "")
    return False, None, None, None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="../results/app1_recall_20.json")
    args = ap.parse_args()

    if not APP_KEY:
        print("缺少 DIFY_APP_KEY")
        return 1

    print("=" * 108)
    print("「数据库查询1」召回率测试（父子分段模式，top_k=6）")
    print("=" * 108)
    print(f"{'ID':<4} {'分类':<8} {'问题':<26} {'召回':<6} {'排名':<4} {'答案':<5} {'期望/实际'}")
    print("-" * 108)

    results = []
    recall_hit = 0
    answer_ok = 0
    mrr_sum = 0.0
    times = []

    for cid, cat, question, gt_sql, expect_type, keywords in CASES:
        # 标准答案
        gt = ""
        if gt_sql:
            try:
                gt = mysql_query(gt_sql)
            except Exception as e:
                print(f"{cid:<4} 标准答案失败: {e}")
                continue

        # 调用工作流
        r = call_workflow(question)
        times.append(r["ms"])

        # 召回信息（从 DB 读）
        docs = get_recall_info(question)
        hit, rank, hit_table, hit_doc = judge_recall(docs or [], keywords)
        if hit:
            recall_hit += 1
            mrr_sum += 1.0 / rank

        # 答案判定
        ok = False
        detail = ""
        if not r["ok"]:
            detail = "调用失败"
        elif expect_type == "num":
            gt_nums = norm_num(gt)
            got = norm_num(r["data"]) or norm_num(r["ans"])
            if gt_nums and got:
                ok = any(abs(g - x) < max(0.01, abs(g)*0.005)
                         for g in gt_nums[:2] for x in got)
                detail = f"{gt_nums[0]} / {got[0]}" if got else ""
            else:
                detail = f"{gt} / 空"
        else:
            ok = bool(gt) and gt in (r["data"] + r["ans"] + r["err_text"])
            detail = f"{gt} / {'命中' if ok else '未命中'}"

        if ok:
            answer_ok += 1

        print(f"{cid:<4} {cat:<8} {question[:24]:<26} "
              f"{'✅' if hit else '❌':<6} {str(rank or '-'):<4} "
              f"{'✅' if ok else '❌':<5} {detail[:26]}")

        results.append({
            "id": cid, "category": cat, "question": question,
            "expected": gt, "recall_hit": hit, "recall_rank": rank,
            "recall_table": hit_table, "recall_doc": hit_doc,
            "recall_docs": [{"doc": d.get("document_name"),
                             "len": len(d.get("content") or ""),
                             "score": d.get("score")} for d in (docs or [])],
            "answer_correct": ok, "detail": detail,
            "ms": r["ms"], "status": r.get("status"),
            "data": r["data"][:300], "ans": r["ans"][:300],
        })

    n = len(results)
    print()
    print("=" * 108)
    print("【结果汇总】")
    print("=" * 108)
    print(f"  样本数          : {n}")
    print(f"  RAG 召回率      : {recall_hit}/{n} = {recall_hit/n*100:.1f}%")
    print(f"  MRR             : {mrr_sum/n:.4f}")
    print(f"  端到端答案准确率 : {answer_ok}/{n} = {answer_ok/n*100:.1f}%")
    if times:
        ts = sorted(times)
        print(f"  耗时            : 平均 {sum(times)/len(times)/1000:.1f}s | "
              f"P50 {ts[len(ts)//2]/1000:.1f}s | 最大 {max(times)/1000:.1f}s")
    print("=" * 108)

    # 按分类
    from collections import defaultdict
    by_cat = defaultdict(lambda: [0, 0, 0])  # 召回命中, 答案正确, 总数
    for x in results:
        by_cat[x["category"]][2] += 1
        if x["recall_hit"]:
            by_cat[x["category"]][0] += 1
        if x["answer_correct"]:
            by_cat[x["category"]][1] += 1

    print("\n【按分类】")
    print(f"  {'分类':<10} {'召回率':<16} {'答案准确率'}")
    for c, (rh, ak, tot) in sorted(by_cat.items()):
        print(f"  {c:<10} {rh}/{tot} ({rh/tot*100:>5.1f}%)      {ak}/{tot} ({ak/tot*100:>5.1f}%)")

    # 召回失败详情
    recall_fails = [x for x in results if not x["recall_hit"]]
    if recall_fails:
        print(f"\n【召回失败 {len(recall_fails)} 个】")
        for x in recall_fails:
            print(f"\n  [{x['id']}] {x['question']}")
            print(f"    期望含关键词的文档")
            print(f"    实际召回:")
            for d in (x["recall_docs"] or [])[:6]:
                print(f"      - {d['doc_name']} (score={d.get('score')})")

    out = os.path.normpath(os.path.join(HERE, args.out))
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump({
            "total": n,
            "recall_hit": recall_hit,
            "recall_rate": recall_hit/n if n else 0,
            "mrr": mrr_sum/n if n else 0,
            "answer_correct": answer_ok,
            "answer_rate": answer_ok/n if n else 0,
            "by_category": {k: {"recall": v[0], "answer": v[1], "total": v[2]}
                            for k, v in by_cat.items()},
            "results": results,
        }, f, ensure_ascii=False, indent=2)
    print(f"\n结果已保存: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
