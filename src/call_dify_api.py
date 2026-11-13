#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ChatBI Dify 工作流调用示例

用法:
    set DIFY_APP_KEY=app-xxxxxxxx
    set DIFY_BASE_URL=http://localhost/v1
    python call_dify_api.py

前置条件:
    1. Dify 中已创建「数据库查询」工作流应用
    2. 应用已开启 API 访问，拿到 app- 开头的 key
    3. 工作流已发布（不只是草稿）
"""
import json
import os
import sys
import time
import urllib.error
import urllib.request

BASE_URL = os.environ.get("DIFY_BASE_URL", "http://localhost/v1").rstrip("/")
APP_KEY = os.environ.get("DIFY_APP_KEY", "").strip()
USER_ID = os.environ.get("DIFY_USER_ID", "chatbi-demo")


def ask_chatbi(question: str, timeout: int = 300) -> dict:
    """向 ChatBI 工作流提问，返回结构化结果

    返回:
        {
            "success": bool,
            "answer": str,      # LLM 生成的分析报告
            "data": str,        # SQL 查询原始结果（markdown 表格）
            "elapsed_ms": int,
            "error": str | None,
        }
    """
    payload = json.dumps({
        "inputs": {"text": question},
        "response_mode": "blocking",
        "user": USER_ID,
    }).encode("utf-8")

    req = urllib.request.Request(
        f"{BASE_URL}/workflows/run",
        data=payload,
        headers={
            "Authorization": f"Bearer {APP_KEY}",
            "Content-Type": "application/json",
        },
        method="POST",
    )

    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return {
            "success": False,
            "answer": "",
            "data": "",
            "elapsed_ms": int((time.perf_counter() - t0) * 1000),
            "error": f"HTTP {e.code}: {e.read().decode('utf-8', errors='replace')[:300]}",
        }
    except Exception as e:
        return {
            "success": False,
            "answer": "",
            "data": "",
            "elapsed_ms": int((time.perf_counter() - t0) * 1000),
            "error": str(e),
        }

    elapsed = int((time.perf_counter() - t0) * 1000)
    data = body.get("data", {})
    outputs = data.get("outputs", {})

    return {
        "success": data.get("status") == "succeeded",
        "answer": outputs.get("ans", ""),
        "data": outputs.get("data", ""),
        "elapsed_ms": elapsed,
        "error": data.get("error"),
    }


def print_result(question: str, result: dict):
    print("=" * 70)
    print(f"❓ {question}")
    print("=" * 70)

    if not result["success"]:
        print(f"❌ 执行失败：{result['error']}")
        print()
        return

    print(f"⏱  耗时 {result['elapsed_ms']}ms\n")

    if result["data"]:
        print("📊 查询结果：")
        print(result["data"])
        print()

    if result["answer"]:
        print("🤖 AI 分析：")
        print(result["answer"])
        print()


def main():
    if not APP_KEY:
        print("!" * 70)
        print("缺少 DIFY_APP_KEY 环境变量")
        print("!" * 70)
        print("\n获取方式：")
        print("  Dify → 打开「数据库查询」应用 → 左侧「API 访问」→ 创建 API 密钥")
        print("\n设置：")
        print('  PowerShell:  $env:DIFY_APP_KEY="app-xxxxxxxx"')
        print('  CMD:         set DIFY_APP_KEY=app-xxxxxxxx')
        print('  Linux/macOS: export DIFY_APP_KEY=app-xxxxxxxx')
        return 1

    questions = [
        "深圳有多少客户？",
        "各分类的销售额是多少？",
        "3月份卖得最好的商品是什么，卖了多少件？",
        "消费最高的前3名女性客户分别买了什么？",
        "佛山有多少客户？",
    ]

    # 支持从命令行传入自定义问题
    if len(sys.argv) > 1:
        questions = [" ".join(sys.argv[1:])]

    results = []
    for q in questions:
        r = ask_chatbi(q)
        print_result(q, r)
        results.append({"question": q, **r})

    # 汇总
    ok = sum(1 for r in results if r["success"])
    print("=" * 70)
    print(f"完成 {ok}/{len(results)} 个问题")
    if results:
        avg = sum(r["elapsed_ms"] for r in results) // len(results)
        print(f"平均耗时 {avg}ms")
    print("=" * 70)

    return 0 if ok == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
