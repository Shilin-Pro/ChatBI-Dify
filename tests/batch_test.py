#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ChatBI 工作流批量测试

用一组预设问题调用 Dify 工作流，把返回的 data（SQL 结果）和标准答案比对。

前提：
    - 标准答案从这里取：直接跑 SQL 得到
    - 由 fetch_ground_truth.py 生成 data/ground_truth.json

用法:
    set DIFY_APP_KEY=app-xxxx
    python batch_test.py
    python batch_test.py --only sync      # 只测某一组
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
MYSQL = os.environ.get(
    "MYSQL_BIN",
    r"C:\Program Files\MySQL\MySQL Server 8.0\bin\mysql.exe")
DB_HOST = os.environ.get("DB_HOST", "127.0.0.1")
DB_PORT = os.environ.get("DB_PORT", "3306")
DB_NAME = os.environ.get("DB_NAME", "cd")
DB_USER = os.environ.get("DB_USER", "root")
DB_PASSWORD = os.environ.get("DB_PASSWORD", "")
GT_FILE = os.path.join(HERE, "..", "data", "ground_truth.json")


# ------------------------------------------------------------
# 测试用例
# ------------------------------------------------------------
# 每组: (id, 分类, 问题, 对应的标准答案 SQL, 提取哪个字段, 期望行为)
# 期望行为:
#   "value"  -> 比对具体数值
#   "refuse" -> 期望模型说"找不到/没有"
CASES = [
    # --- 基础单表 ---
    ("S1", "单表", "深圳有多少客户？",
     "SELECT COUNT(*) AS v FROM customers WHERE city='深圳'", "v", "value"),
    ("S2", "单表", "佛山有多少客户？",
     "SELECT COUNT(*) AS v FROM customers WHERE city='佛山'", "v", "value"),
    ("S3", "单表", "一共有多少客户？",
     "SELECT COUNT(*) AS v FROM customers", "v", "value"),

    # --- 销售额口径（关键）---
    ("A1", "销售额", "电脑办公分类的销售额是多少？",
     """SELECT SUM(oi.subtotal) AS v FROM order_items oi
        JOIN products p ON p.id=oi.product_id
        JOIN categories c ON c.id=p.category_id
        JOIN orders o ON o.id=oi.order_id
        WHERE o.status='已完成' AND c.name='电脑办公'""", "v", "value"),
    ("A2", "销售额", "手机通讯分类的销售额是多少？",
     """SELECT SUM(oi.subtotal) AS v FROM order_items oi
        JOIN products p ON p.id=oi.product_id
        JOIN categories c ON c.id=p.category_id
        JOIN orders o ON o.id=oi.order_id
        WHERE o.status='已完成' AND c.name='手机通讯'""", "v", "value"),
    ("A3", "销售额", "卖得最好的商品是哪个？卖了多少件？",
     """SELECT p.name AS v FROM order_items oi
        JOIN products p ON p.id=oi.product_id
        JOIN orders o ON o.id=oi.order_id
        WHERE o.status='已完成'
        GROUP BY p.id,p.name ORDER BY SUM(oi.quantity) DESC LIMIT 1""", "v", "value"),

    # --- 时间与状态过滤 ---
    ("T1", "时间", "3月份有多少个订单？",
     """SELECT COUNT(*) AS v FROM orders
        WHERE status='已完成' AND order_date>='2024-03-01' AND order_date<'2024-04-01'""", "v", "value"),
    ("T2", "时间", "6月份的销售额是多少？",
     """SELECT SUM(pay_amount) AS v FROM orders
        WHERE status='已完成' AND order_date>='2024-06-01' AND order_date<'2024-07-01'""", "v", "value"),
    ("T3", "时间", "上半年（1-6月）总共成交了多少单？",
     """SELECT COUNT(*) AS v FROM orders WHERE status='已完成'""", "v", "value"),

    # --- 多表关联 ---
    ("J1", "多表", "消费最高的客户是谁？",
     """SELECT c.name AS v FROM customers c
        JOIN orders o ON o.customer_id=c.id
        WHERE o.status='已完成'
        GROUP BY c.id,c.name ORDER BY SUM(o.pay_amount) DESC LIMIT 1""", "v", "value"),
    ("J2", "多表", "深圳客户的累计消费总额是多少？",
     """SELECT SUM(o.pay_amount) AS v FROM customers c
        JOIN orders o ON o.customer_id=c.id
        WHERE c.city='深圳' AND o.status='已完成'""", "v", "value"),
    ("J3", "多表", "华为 Mate 60 Pro 一共卖了多少件？",
     """SELECT SUM(oi.quantity) AS v FROM order_items oi
        JOIN products p ON p.id=oi.product_id
        JOIN orders o ON o.id=oi.order_id
        WHERE o.status='已完成' AND p.name LIKE '%Mate 60%'""", "v", "value"),

    # --- 异常兜底 ---
    ("E1", "兜底", "客户的身份证号是多少？", None, None, "refuse"),
    ("E2", "兜底", "火星有多少客户？", None, None, "refuse"),
]

REFUSE_WORDS = ["找不到", "没有", "无法", "不存在", "抱歉", "未查询到", "暂无"]
MIN_NUMS = 2  # 至少比对这个数量才算通过


# ------------------------------------------------------------
# 工具函数
# ------------------------------------------------------------
def norm_num(s):
    """从字符串里提取所有数字"""
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
    """直连 MySQL 执行标准答案 SQL（连接信息从环境变量读取）"""
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


def ask(question, timeout=300):
    """调工作流"""
    payload = json.dumps({
        "inputs": {"text": question},
        "response_mode": "blocking",
        "user": "batch-test",
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
    status = d.get("status")
    # 正常分支输出 ans/data；错误分支输出 err_text
    return {
        "ok": status == "succeeded",
        "partial": status == "partial-succeeded",
        "data": o.get("data", "") or "",
        "ans": o.get("ans", "") or "",
        "err_text": o.get("err_text", "") or "",
        "all_outputs": o,
        "ms": ms,
        "err": d.get("error"),
    }


# ------------------------------------------------------------
# 主流程
# ------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default=None, help="只测某分类")
    ap.add_argument("--out", default="../results/batch_test.json")
    args = ap.parse_args()

    if not APP_KEY:
        print("缺少 DIFY_APP_KEY")
        return 1

    cases = [c for c in CASES if not args.only or c[1] == args.only]

    print("=" * 78)
    print(f"ChatBI 批量测试（{len(cases)} 个问题）")
    print("=" * 78)
    print(f"{'ID':<4} {'分类':<6} {'结果':<6} {'问题':<28} {'期望':<16} {'实际'}")
    print("-" * 78)

    results = []
    passed = 0

    for cid, cat, question, gt_sql, field, expect in cases:
        # 1. 标准答案
        gt = ""
        if gt_sql:
            try:
                gt = mysql_query(gt_sql)
            except Exception as e:
                print(f"{cid:<4} 标准答案获取失败: {e}")
                continue

        # 2. 调用工作流
        r = ask(question)

        # 3. 判定
        ok = False
        actual_desc = ""
        # 汇总所有可能的输出文本（正常分支 ans/data + 错误分支 err_text）
        all_text = (r.get("ans", "") + " " + r.get("data", "") + " "
                    + r.get("err_text", ""))

        if expect == "refuse":
            # 期望拒答：任何一个输出里出现拒绝词即算通过
            ok = any(w in all_text for w in REFUSE_WORDS)
            actual_desc = "已拒答" if ok else "未拒答"
        elif r["ok"] or r.get("partial"):
            gt_nums = norm_num(gt)
            got_nums = norm_num(r.get("data", ""))

            # 判定 1：数值命中
            if gt_nums and got_nums:
                if any(abs(g - x) < 0.01 for g in gt_nums[:3] for x in got_nums):
                    ok = True
                    actual_desc = "数值命中"
            # 判定 2：文字命中（商品名、客户名等）
            if not ok and isinstance(gt, str) and gt and not gt_nums:
                if gt in all_text:
                    ok = True
                    actual_desc = "文字命中"
            if not ok:
                actual_desc = actual_desc or "不符"
        else:
            actual_desc = f"调用失败: {r['err']}"

        mark = "✅" if ok else "❌"
        if ok:
            passed += 1

        print(f"{cid:<4} {cat:<6} {mark:<6} {question[:26]:<28} {str(gt)[:14]:<16} {actual_desc}")

        results.append({
            "id": cid, "category": cat, "question": question,
            "expected": gt, "pass": ok, "ms": r["ms"],
            "data": r["data"][:500], "ans": r["ans"][:500],
            "err": r.get("err"),
        })

    # 汇总
    total = len(results)
    print()
    print("=" * 78)
    print(f"通过 {passed}/{total} = {passed/total*100:.1f}%" if total else "无结果")
    print(f"平均耗时 {sum(r['ms'] for r in results)//total if total else 0}ms")
    print("=" * 78)

    # 按分类统计
    from collections import defaultdict
    by_cat = defaultdict(lambda: [0, 0])
    for r in results:
        by_cat[r["category"]][1] += 1
        if r["pass"]:
            by_cat[r["category"]][0] += 1
    print("\n按分类:")
    for cat, (p, t) in sorted(by_cat.items()):
        print(f"  {cat:<8} {p}/{t}")

    # 失败详情
    fails = [r for r in results if not r["pass"]]
    if fails:
        print(f"\n未通过 {len(fails)} 个:")
        for r in fails:
            print(f"\n  [{r['id']}] {r['question']}")
            print(f"    期望: {r['expected']}")
            print(f"    返回数据: {r['data'][:150]}")
            if r['ans']:
                print(f"    回答: {r['ans'][:100]}")

    out_path = os.path.normpath(os.path.join(HERE, args.out))
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump({
            "total": total, "passed": passed,
            "pass_rate": passed / total if total else 0,
            "results": results,
        }, f, ensure_ascii=False, indent=2)
    print(f"\n结果已保存: {out_path}")

    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
