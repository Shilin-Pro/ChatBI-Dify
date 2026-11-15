#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
用修正后的标准答案，重新计算【SQL生成 + 错误处理】阶段的得分。
数据来源：_sql_stage.json（已导出的 Dify 历史执行记录）
"""
import json
import os
import re
import sys
import io
import subprocess

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

HERE = os.path.dirname(os.path.abspath(__file__))
RAW = r"C:\Users\Lenovo\OneDrive\文档\deepseek-harness\default-workspace\_sql_stage.json"

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


# 从 eval_50.py 加载修正后的用例
import importlib.util
spec = importlib.util.spec_from_file_location("e50", os.path.join(HERE, "eval_50.py"))
e50 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(e50)

# 建立 问题 -> (类型, 标准答案) 映射
expected = {}
for cid, cat, q, sql, typ in e50.CASES:
    if sql is None:
        expected[q] = (typ, None)
    else:
        try:
            expected[q] = (typ, mysql_query(sql))
        except Exception as e:
            expected[q] = (typ, f"__ERR__{e}")


def norm_num(s):
    if isinstance(s, (int, float)):
        return [round(float(s), 2)]
    if not s:
        return []
    keep = [ln for ln in str(s).split("\n")
            if not re.fullmatch(r'[\s|:\-]+', ln.strip())]
    txt = "\n".join(keep)
    return [round(float(n), 2) for n in re.findall(r'-?\d+\.?\d*', txt.replace(",", ""))]


def main():
    data = json.load(open(RAW, encoding='utf-8'))
    matched = {k: v for k, v in data.items() if k in expected}

    print("=" * 106)
    print("ChatBI 评测｜仅 SQL 生成 + 错误处理（标准答案已修正）")
    print("=" * 106)
    print(f"历史记录 {len(data)} 条，匹配测试集 {len(matched)} 条\n")

    print(f"{'问题':<28} {'SQL':<4} {'执行':<4} {'错误处理':<10} {'答案':<4} {'期望':<12} 说明")
    print("-" * 106)

    stat = {"n": 0, "sql_ok": 0, "exec_ok": 0, "err_ok": 0, "correct": 0}
    rows = []

    for q, v in matched.items():
        typ, gt = expected[q]
        stat["n"] += 1

        sql = (v.get("sql") or "").strip()
        result = (v.get("result") or "").strip()
        error = (v.get("error") or "").strip()
        code_status = (v.get("code_status") or "").strip()

        # 1. SQL 生成
        sql_ok = sql.upper().lstrip().startswith("SELECT")
        if sql_ok:
            stat["sql_ok"] += 1

        # 2. 执行成功
        exec_ok = bool(result) and ("失败" not in result) and not error
        if exec_ok:
            stat["exec_ok"] += 1

        # 3. 错误处理
        if typ == "refuse":
            err_ok = (not sql_ok) or code_status == "error" or "失败" in result
            err_label = "✅拒答" if err_ok else "❌未拒答"
        else:
            if code_status == "error":
                err_ok = False
                err_label = "❌误判错"
            elif code_status == "empty":
                err_ok = False
                err_label = "⚠️判空"
            else:
                err_ok = True
                err_label = "✅正常"
        if err_ok:
            stat["err_ok"] += 1

        # 4. 答案
        correct = False
        note = ""
        if typ == "refuse":
            correct = err_ok
            note = "拒答判定"
        elif exec_ok:
            if typ == "num":
                got = norm_num(result)
                gt_nums = norm_num(gt)
                if gt_nums:
                    correct = any(abs(g - x) < max(0.01, abs(g) * 0.005)
                                  for g in gt_nums[:2] for x in got)
                    note = f"期望{gt_nums[:2]}"
                else:
                    note = "标准答案空"
            else:
                correct = gt in result
                note = f"期望「{gt}」"
        if correct:
            stat["correct"] += 1

        print(f"{q[:26]:<28} {'✅' if sql_ok else '❌':<4} {'✅' if exec_ok else '❌':<4} "
              f"{err_label:<10} {'✅' if correct else '❌':<4} {str(gt)[:10]:<12} {note}")

        rows.append({
            "question": q, "sql": sql, "result": result[:300],
            "error": error[:200], "code_status": code_status,
            "correct": correct, "expected": gt, "type": typ, "note": note,
        })

    n = stat["n"]
    print()
    print("=" * 106)
    print("【分阶段得分（修正标准答案后）】")
    print("=" * 106)
    print(f"  SQL 生成率      : {stat['sql_ok']}/{n} = {stat['sql_ok']/n*100:.1f}%")
    print(f"  SQL 执行成功率  : {stat['exec_ok']}/{n} = {stat['exec_ok']/n*100:.1f}%")
    print(f"  错误处理正确率  : {stat['err_ok']}/{n} = {stat['err_ok']/n*100:.1f}%")
    print(f"  答案正确率      : {stat['correct']}/{n} = {stat['correct']/n*100:.1f}%")
    print("=" * 106)

    fails = [r for r in rows if not r["correct"]]
    if fails:
        print(f"\n【未通过 {len(fails)} 个】")
        for r in fails:
            print(f"\n❌ {r['question']}")
            print(f"   期望: {r['expected']}  类型: {r['type']}")
            print(f"   SQL: {r['sql'][:260]}")
            if r["result"]:
                print(f"   结果: {r['result'][:140]}")
            if r["error"]:
                print(f"   错误: {r['error'][:140]}")

    out = os.path.join(HERE, "..", "results", "eval_sql_stage_fixed.json")
    with open(os.path.normpath(out), "w", encoding="utf-8") as f:
        json.dump({"stats": stat, "rows": rows, "failed": len(fails)}, f,
                  ensure_ascii=False, indent=2)
    print(f"\n已保存: {os.path.normpath(out)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
