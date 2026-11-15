#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ReAct 版本评测：重点验证两条纠错回路

1. 实体消歧回路：空数据 → LLM3 提取实体 → LIKE 探测 → LLM4 重写 SQL
2. 错误兜底：SQL 失败 → Code 判断 → IF/ELSE 降级

测试集重点设计"容易触发纠错"的问题：
- 模糊/错误的商品名（如"苹果手机"库里是 iPhone）
- 不存在的客户
- 正常的对照问题
"""
import json
import os
import re
import subprocess
import sys
import io
import time
import urllib.request

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

HERE = os.path.dirname(os.path.abspath(__file__))
BASE_URL = "http://localhost/v1"
APP_KEY = os.environ.get("DIFY_APP_KEY", "").strip()
APP_ID = "627f8555-cba4-4667-9c86-219c5055a56c"

MYSQL = os.environ.get("MYSQL_BIN", r"C:\Program Files\MySQL\MySQL Server 8.0\bin\mysql.exe")
DB_PASSWORD = os.environ.get("DB_PASSWORD", "")


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
# 测试用例
# 格式: (id, 分类, 问题, 标准答案SQL, 期望类型, 是否期望触发纠错)
# ============================================================
CASES = [
    # ---- A. 对照组：正常问题，不应触发纠错 ----
    ("N1", "正常", "深圳有多少客户？",
     "SELECT COUNT(*) AS v FROM customers WHERE city='深圳'", "num", False),
    ("N2", "正常", "一共有多少笔订单？",
     "SELECT COUNT(*) AS v FROM orders", "num", False),
    ("N3", "正常", "6月份的销售额是多少？",
     """SELECT SUM(pay_amount) AS v FROM orders WHERE status='已完成'
        AND order_date>='2024-06-01' AND order_date<'2024-07-01'""", "num", False),
    ("N4", "正常", "消费最高的客户是谁？",
     """SELECT c.name AS v FROM customers c JOIN orders o ON o.customer_id=c.id
        WHERE o.status='已完成' GROUP BY c.id,c.name
        ORDER BY SUM(o.pay_amount) DESC LIMIT 1""", "text", False),
    ("N5", "正常", "数码电器类商品的销售额是多少？",
     """SELECT SUM(oi.subtotal) AS v FROM order_items oi
        JOIN products p ON p.id=oi.product_id JOIN categories c ON c.id=p.category_id
        JOIN orders o ON o.id=oi.order_id WHERE o.status='已完成'
        AND p.category_id IN (SELECT id FROM categories WHERE name='数码电器'
            UNION SELECT c2.id FROM categories c2 JOIN categories c1 ON c2.parent_id=c1.id
            WHERE c1.name='数码电器')""", "num", False),

    # ---- B. 模糊商品名：应触发实体消歧 ----
    ("F1", "模糊实体", "MacBook 卖了多少件？",
     """SELECT SUM(oi.quantity) AS v FROM order_items oi
        JOIN products p ON p.id=oi.product_id JOIN orders o ON o.id=oi.order_id
        WHERE o.status='已完成' AND p.name LIKE '%MacBook%'""", "num", True),
    ("F2", "模糊实体", "iPhone 卖了多少件？",
     """SELECT SUM(oi.quantity) AS v FROM order_items oi
        JOIN products p ON p.id=oi.product_id JOIN orders o ON o.id=oi.order_id
        WHERE o.status='已完成' AND p.name LIKE '%iPhone%'""", "num", True),
    ("F3", "模糊实体", "罗技鼠标卖了多少件？",
     """SELECT SUM(oi.quantity) AS v FROM order_items oi
        JOIN products p ON p.id=oi.product_id JOIN orders o ON o.id=oi.order_id
        WHERE o.status='已完成' AND p.name LIKE '%罗技%鼠标%'""", "num", True),
    ("F4", "模糊实体", "华为手机卖了多少件？",
     """SELECT SUM(oi.quantity) AS v FROM order_items oi
        JOIN products p ON p.id=oi.product_id JOIN orders o ON o.id=oi.order_id
        WHERE o.status='已完成' AND p.name LIKE '%华为%'""", "num", True),
    ("F5", "模糊实体", "林峰花了多少钱？",
     """SELECT SUM(o.pay_amount) AS v FROM customers c JOIN orders o ON o.customer_id=c.id
        WHERE c.name='林峰' AND o.status='已完成'""", "num", True),

    # ---- C. 不存在的实体：应优雅返回空，不崩溃 ----
    ("E1", "不存在", "张三丰买了多少东西？", None, "graceful", False),
    ("E2", "不存在", "外星人手机卖了多少件？", None, "graceful", False),

    # ---- D. 字段不存在：应拒答 ----
    ("E3", "异常", "客户的身份证号是多少？", None, "refuse", False),

    # ---- E. 业务规则 ----
    ("B1", "业务规则", "哪个分类的销售额最高？",
     """SELECT c.name AS v FROM order_items oi
        JOIN products p ON p.id=oi.product_id JOIN categories c ON c.id=p.category_id
        JOIN orders o ON o.id=oi.order_id WHERE o.status='已完成'
        AND c.parent_id IS NOT NULL
        GROUP BY c.id,c.name ORDER BY SUM(oi.subtotal) DESC LIMIT 1""", "text", False),
    ("B2", "业务规则", "女装分类的销售额是多少？",
     """SELECT SUM(oi.subtotal) AS v FROM order_items oi
        JOIN products p ON p.id=oi.product_id JOIN categories c ON c.id=p.category_id
        JOIN orders o ON o.id=oi.order_id WHERE o.status='已完成' AND c.name='女装'""",
     "num", False),
    ("B3", "业务规则", "电脑办公分类的销售额是多少？",
     """SELECT SUM(oi.subtotal) AS v FROM order_items oi
        JOIN products p ON p.id=oi.product_id JOIN categories c ON c.id=p.category_id
        JOIN orders o ON o.id=oi.order_id WHERE o.status='已完成' AND c.name='电脑办公'""",
     "num", False),
    ("B4", "业务规则", "男性客户的消费总额是多少？",
     """SELECT SUM(o.pay_amount) AS v FROM customers c JOIN orders o ON o.customer_id=c.id
        WHERE c.gender='M' AND o.status='已完成'""", "num", False),
    ("B5", "业务规则", "有哪些商品从来没被卖出去过？",
     """SELECT p.name AS v FROM products p
        WHERE p.id NOT IN (SELECT DISTINCT product_id FROM order_items) LIMIT 1""",
     "text", False),
]

REFUSE_WORDS = ["找不到", "没有", "无法", "不存在", "抱歉", "未查询到", "暂无",
                "不能", "不支持", "无相关", "未提及", "失败", "查无"]


def call_workflow(question, timeout=500):
    payload = json.dumps({
        "inputs": {"text": question},
        "response_mode": "blocking",
        "user": "react-eval",
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

    # 输出字段名可能变化，全部收集
    # 正常: ans1 (LLM2 解读文本)
    # 找不到: ans3 (Output 2)
    # SQL错误: ans2 (Output 3)
    ans = (o.get("ans1") or o.get("ans") or "")
    err_msg = (o.get("ans2") or o.get("ans3") or o.get("err_text") or "")
    data = o.get("data") or ""

    return {
        "ok": d.get("status") in ("succeeded", "partial-succeeded"),
        "status": d.get("status"),
        "data": data.strip(),
        "ans": ans.strip(),
        "err_text": err_msg.strip(),
        "raw_outputs": o,
        "ms": ms,
        "err": d.get("error"),
    }


def judge(expect_type, gt, r):
    """返回 (是否通过, 说明)"""
    if not r["ok"]:
        return False, "调用失败"

    all_text = (r["data"] + " " + r["ans"] + " " + r["err_text"]).strip()

    if expect_type == "refuse":
        ok = any(w in all_text for w in REFUSE_WORDS)
        return ok, "已拒答" if ok else "未拒答"

    if expect_type == "graceful":
        # 不存在的实体：应该给友好提示，不崩溃（没抛异常、有输出）
        has_output = bool(all_text)
        no_crash = r.get("status") in ("succeeded", "partial-succeeded")
        return has_output and no_crash, "优雅返回" if has_output else "无输出"

    # 数值 / 文字比对
    if expect_type == "num":
        gt_nums = norm_num(gt)
        got = norm_num(r["data"]) or norm_num(r["ans"])
        if not gt_nums:
            return False, "标准答案空"
        if not got:
            return False, "无数据"
        ok = any(abs(g - x) < max(0.01, abs(g)*0.005) for g in gt_nums[:2] for x in got)
        return ok, f"{gt_nums[0]} / {got[0]}"

    if expect_type == "text":
        ok = bool(gt) and gt in all_text
        return ok, f"{gt} / {'命中' if ok else '未命中'}"

    return False, "未知类型"


def main():
    if not APP_KEY:
        print("缺少 DIFY_APP_KEY")
        return 1

    print("=" * 110)
    print("ReAct 版本评测（重点验证两条纠错回路）")
    print("=" * 110)
    print(f"{'ID':<4} {'类型':<8} {'问题':<24} {'结果':<5} {'耗时':<8} {'说明'}")
    print("-" * 110)

    results = []
    passed = 0
    times = []

    for cid, cat, question, gt_sql, expect_type, expect_react in CASES:
        gt = ""
        if gt_sql:
            try:
                gt = mysql_query(gt_sql)
            except Exception as e:
                print(f"{cid:<4} 标准答案失败: {e}")
                continue

        r = call_workflow(question)
        times.append(r["ms"])
        ok, note = judge(expect_type, gt, r)
        if ok:
            passed += 1

        print(f"{cid:<4} {cat:<8} {question[:22]:<24} {'✅' if ok else '❌':<5} "
              f"{r['ms']/1000:>6.1f}s  {note}")

        results.append({
            "id": cid, "category": cat, "question": question,
            "expected": gt, "pass": ok, "note": note,
            "expect_react": expect_react,
            "ms": r["ms"], "status": r.get("status"),
            "data": r["data"][:400], "ans": r["ans"][:400],
            "err_text": r.get("err_text", "")[:200],
        })

    n = len(results)
    print()
    print("=" * 110)
    print(f"通过率: {passed}/{n} = {passed/n*100:.1f}%")
    if times:
        ts = sorted(times)
        print(f"耗时: 平均 {sum(times)/len(times)/1000:.1f}s | "
              f"P50 {ts[len(ts)//2]/1000:.1f}s | 最大 {max(times)/1000:.1f}s")
    print("=" * 110)

    # 按分类
    from collections import defaultdict
    by_cat = defaultdict(lambda: [0, 0])
    for x in results:
        by_cat[x["category"]][1] += 1
        if x["pass"]:
            by_cat[x["category"]][0] += 1
    print("\n【按分类】")
    for c, (p, t) in sorted(by_cat.items()):
        print(f"  {c:<10} {p}/{t} ({p/t*100:>5.1f}%)")

    # 失败详情
    fails = [x for x in results if not x["pass"]]
    if fails:
        print(f"\n【未通过 {len(fails)} 个】")
        for x in fails:
            print(f"\n  [{x['id']}] {x['question']}")
            print(f"      期望: {x['expected']}  说明: {x['note']}")
            if x["data"]:
                print(f"      数据: {x['data'][:150]}")
            if x["err_text"]:
                print(f"      错误: {x['err_text'][:100]}")

    out = os.path.join(HERE, "..", "results", "react_eval.json")
    os.makedirs(os.path.dirname(os.path.normpath(out)), exist_ok=True)
    with open(os.path.normpath(out), "w", encoding="utf-8") as f:
        json.dump({
            "total": n, "passed": passed, "pass_rate": passed/n if n else 0,
            "avg_ms": sum(times)/len(times) if times else 0,
            "by_category": {k: {"pass": v[0], "total": v[1]} for k, v in by_cat.items()},
            "results": results,
        }, f, ensure_ascii=False, indent=2)
    print(f"\n已保存: {os.path.normpath(out)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
