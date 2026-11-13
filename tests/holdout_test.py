#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
留出测试集（Holdout Test）

设计原则：
1. 所有问题都不在 Prompt 示例里出现
2. 覆盖原测试集没测过的场景：新分类、新月份、新城市、组合条件、计算类、否定类
3. 用来检验真实泛化能力，而不是"记住示例"

用法:
    set DIFY_APP_KEY=app-xxxx
    python holdout_test.py
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

MYSQL = os.environ.get("MYSQL_BIN", r"C:\Program Files\MySQL\MySQL Server 8.0\bin\mysql.exe")
DB_HOST = os.environ.get("DB_HOST", "127.0.0.1")
DB_PORT = os.environ.get("DB_PORT", "3306")
DB_NAME = os.environ.get("DB_NAME", "cd")
DB_USER = os.environ.get("DB_USER", "root")
DB_PASSWORD = os.environ.get("DB_PASSWORD", "")


# ------------------------------------------------------------
# 留出测试用例
# ------------------------------------------------------------
# 设计说明：
# - H1/H2: 新分类（女装、男装），Prompt 示例只用过数码电器/电脑办公
# - H3/H4: 新月份（4月、2月），示例只用过 3月/6月
# - H5/H6: 新城市（广州、东莞），示例只用过深圳/佛山
# - H7: 组合条件（城市+性别），示例没有这种组合
# - H8: 计算类（毛利率），示例没用过 cost
# - H9: 否定类（从没买过的商品），需要 LEFT JOIN
# - H10: 新商品（MacBook Air），示例用过华为Mate60
CASES = [
    ("H1", "新分类", "女装分类的销售额是多少？",
     """SELECT SUM(oi.subtotal) AS v FROM order_items oi
        JOIN products p ON p.id=oi.product_id
        JOIN categories c ON c.id=p.category_id
        JOIN orders o ON o.id=oi.order_id
        WHERE o.status='已完成' AND c.name='女装'""", "v"),

    ("H2", "新分类", "男装卖了多少件？",
     """SELECT SUM(oi.quantity) AS v FROM order_items oi
        JOIN products p ON p.id=oi.product_id
        JOIN categories c ON c.id=p.category_id
        JOIN orders o ON o.id=oi.order_id
        WHERE o.status='已完成' AND c.name='男装'""", "v"),

    ("H3", "新月份", "4月份有多少个订单？",
     """SELECT COUNT(*) AS v FROM orders
        WHERE status='已完成' AND order_date>='2024-04-01' AND order_date<'2024-05-01'""", "v"),

    ("H4", "新月份", "2月份的销售额是多少？",
     """SELECT SUM(pay_amount) AS v FROM orders
        WHERE status='已完成' AND order_date>='2024-02-01' AND order_date<'2024-03-01'""", "v"),

    ("H5", "新城市", "广州有多少客户？",
     """SELECT COUNT(*) AS v FROM customers WHERE city='广州'""", "v"),

    ("H6", "新城市", "东莞客户的总消费额是多少？",
     """SELECT SUM(o.pay_amount) AS v FROM customers c
        JOIN orders o ON o.customer_id=c.id
        WHERE c.city='东莞' AND o.status='已完成'""", "v"),

    ("H7", "组合条件", "深圳的女性客户消费总额是多少？",
     """SELECT SUM(o.pay_amount) AS v FROM customers c
        JOIN orders o ON o.customer_id=c.id
        WHERE c.city='深圳' AND c.gender='F' AND o.status='已完成'""", "v"),

    ("H8", "计算类", "哪个商品的毛利率最高？",
     """SELECT p.name AS v FROM products p
        WHERE p.id IN (SELECT DISTINCT product_id FROM order_items)
        ORDER BY (p.price - p.cost) / p.price DESC LIMIT 1""", "v"),

    ("H9", "否定类", "哪些商品从来没被买过？",
     """SELECT p.name AS v FROM products p
        WHERE p.id NOT IN (SELECT DISTINCT product_id FROM order_items)
        LIMIT 1""", "v"),

    ("H10", "新商品", "MacBook Air 卖了多少件？",
     """SELECT SUM(oi.quantity) AS v FROM order_items oi
        JOIN products p ON p.id=oi.product_id
        JOIN orders o ON o.id=oi.order_id
        WHERE o.status='已完成' AND p.name LIKE '%MacBook Air%'""", "v"),
]


def norm_num(s):
    if isinstance(s, (int, float)):
        return [round(float(s), 2)]
    nums = re.findall(r'-?\d+\.?\d*', str(s).replace(",", ""))
    out = []
    for n in nums:
        try:
            out.append(round(float(n), 2))
        except ValueError:
            pass
    return out


def mysql_query(sql):
    cmd = [MYSQL, "-h", DB_HOST, "-P", DB_PORT, "-u", DB_USER]
    if DB_PASSWORD:
        cmd.append(f"-p{DB_PASSWORD}")
    cmd += ["--default-character-set=utf8mb4", "--connect-timeout=10",
            "--skip-column-names", "-e", f"USE {DB_NAME}; {sql}"]
    r = subprocess.run(cmd, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    if r.returncode != 0:
        raise RuntimeError(r.stderr[:300])
    lines = [x.strip() for x in r.stdout.splitlines() if x.strip()]
    return lines[0] if lines else ""


def ask(question, timeout=400):
    payload = json.dumps({
        "inputs": {"text": question},
        "response_mode": "blocking",
        "user": "holdout-test",
    }).encode()
    req = urllib.request.Request(
        f"{BASE_URL}/workflows/run", data=payload,
        headers={"Authorization": f"Bearer {APP_KEY}", "Content-Type": "application/json"},
        method="POST")
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = json.loads(resp.read().decode())
    except Exception as e:
        return {"ok": False, "err": str(e), "ms": int((time.perf_counter() - t0) * 1000)}
    ms = int((time.perf_counter() - t0) * 1000)
    d = body.get("data", {})
    o = d.get("outputs", {}) or {}
    return {
        "ok": d.get("status") in ("succeeded", "partial-succeeded"),
        "data": o.get("data", "") or "",
        "ans": o.get("ans", "") or "",
        "err_text": o.get("err_text", "") or "",
        "ms": ms,
        "err": d.get("error"),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="../results/holdout_test.json")
    args = ap.parse_args()

    if not APP_KEY:
        print("缺少 DIFY_APP_KEY")
        return 1

    print("=" * 80)
    print("留出测试（Holdout Test）—— 所有问题均未在 Prompt 示例中出现")
    print("=" * 80)
    print(f"{'ID':<5} {'类型':<8} {'结果':<6} {'问题':<24} {'期望':<14} {'实际'}")
    print("-" * 80)

    results = []
    passed = 0

    for cid, cat, question, gt_sql, field in CASES:
        try:
            gt = mysql_query(gt_sql)
        except Exception as e:
            print(f"{cid:<5} 标准答案失败: {e}")
            continue

        r = ask(question)

        ok = False
        desc = ""
        all_text = (r.get("data", "") + " " + r.get("ans", "") + " "
                    + r.get("err_text", ""))

        if not r["ok"]:
            desc = f"调用失败"
        else:
            gt_nums = norm_num(gt)
            got_nums = norm_num(r.get("data", "") or r.get("ans", ""))
            if gt_nums and got_nums:
                if any(abs(g - x) < 0.01 for g in gt_nums[:3] for x in got_nums):
                    ok = True
                    desc = "数值命中"
            if not ok and isinstance(gt, str) and gt and not gt_nums:
                if gt in all_text:
                    ok = True
                    desc = "文字命中"
            if not ok:
                desc = "不符"

        if ok:
            passed += 1

        print(f"{cid:<5} {cat:<8} {'✅' if ok else '❌':<6} {question[:22]:<24} "
              f"{str(gt)[:12]:<14} {desc}")

        results.append({
            "id": cid, "category": cat, "question": question,
            "expected": gt, "pass": ok, "ms": r["ms"],
            "data": (r.get("data") or "")[:400],
            "ans": (r.get("ans") or "")[:400],
            "err_text": r.get("err_text", ""),
        })

    total = len(results)
    print()
    print("=" * 80)
    print(f"留出测试通过率: {passed}/{total} = {passed/total*100:.1f}%")
    print(f"平均耗时: {sum(r['ms'] for r in results)//total if total else 0}ms")
    print("=" * 80)

    fails = [r for r in results if not r["pass"]]
    if fails:
        print(f"\n未通过 {len(fails)} 个：")
        for r in fails:
            print(f"\n  [{r['id']}] {r['question']}")
            print(f"    期望: {r['expected']}")
            print(f"    返回: {(r['data'] or r['err_text'])[:200]}")

    out = os.path.normpath(os.path.join(HERE, args.out))
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump({"total": total, "passed": passed,
                   "pass_rate": passed/total if total else 0,
                   "results": results}, f, ensure_ascii=False, indent=2)
    print(f"\n结果已保存: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
