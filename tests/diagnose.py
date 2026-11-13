#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
单问题诊断：打印工作流生成的 SQL、返回数据、执行耗时

用法:
    python diagnose.py "电脑办公分类的销售额是多少？"
"""
import json
import os
import sys
import io
import time
import urllib.request

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

BASE = os.environ.get("DIFY_BASE_URL", "http://localhost/v1").rstrip("/")
KEY = os.environ.get("DIFY_APP_KEY", "").strip()


def run(question):
    payload = json.dumps({
        "inputs": {"text": question},
        "response_mode": "blocking",
        "user": "diag",
    }).encode()
    req = urllib.request.Request(
        f"{BASE}/workflows/run", data=payload,
        headers={"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"},
        method="POST")
    t0 = time.perf_counter()
    with urllib.request.urlopen(req, timeout=400) as r:
        body = json.loads(r.read().decode())
    return (time.perf_counter() - t0) * 1000, body


def main():
    if len(sys.argv) < 2:
        print("用法: python diagnose.py <问题>")
        return 1
    q = " ".join(sys.argv[1:])

    print("=" * 74)
    print(f"问题: {q}")
    print("=" * 74)

    ms, body = run(q)
    d = body.get("data", {})

    print(f"\n状态: {d.get('status')}")
    print(f"耗时: {ms:.0f}ms")

    if d.get("error"):
        print(f"错误: {d['error']}")

    # 关键：遍历所有节点的执行过程，找到 LLM 生成的 SQL
    print("\n" + "-" * 74)
    print("节点执行轨迹（找生成的 SQL）")
    print("-" * 74)

    for node in d.get("workflow_run", {}).get("nodes", []) if isinstance(d.get("workflow_run"), dict) else []:
        pass

    # 更可靠：从 outputs 反推 + 检查是否有中间输出
    outputs = d.get("outputs", {}) or {}
    print(f"\n输出字段: {list(outputs.keys())}")
    for k, v in outputs.items():
        print(f"\n--- {k} ---")
        print(str(v)[:1000])

    # 尝试从 body 里找 SQL 痕迹
    raw = json.dumps(body, ensure_ascii=False)
    import re
    sqls = re.findall(r'SELECT[^"\\]{20,400}', raw, re.IGNORECASE)
    if sqls:
        print("\n" + "-" * 74)
        print("捕获到的 SQL 片段")
        print("-" * 74)
        for s in set(sqls):
            print(f"\n{s}")
    else:
        print("\n(未在响应中找到 SQL 文本)")

    return 0


if __name__ == "__main__":
    sys.exit(main())
