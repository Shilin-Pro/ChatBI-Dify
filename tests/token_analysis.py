#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""分析 LLM 节点的 token 数和输出长度"""
import json
import subprocess
import sys
import io

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

APP_ID = "9bb25f01-8771-40f5-819b-0a9122264600"


def psql(sql):
    r = subprocess.run(
        ["wsl", "-e", "bash", "-c",
         f'docker exec docker-db_postgres-1 psql -U postgres -d dify -t -A -c "{sql}"'],
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    return r.stdout.strip()


print("=" * 76)
print("LLM 节点 token 用量分析（最近 10 次）")
print("=" * 76)
print(f"{'节点':<8} {'耗时(s)':>8} {'prompt':>8} {'completion':>12} {'输出长度':>10}")
print("-" * 76)

for node_title in ["LLM", "LLM 2"]:
    raw = psql(f"""
        SELECT n.outputs::text
        FROM workflow_runs r
        JOIN workflows w ON w.id = r.workflow_id
        JOIN workflow_node_executions n ON n.workflow_run_id = r.id
        WHERE w.app_id = '{APP_ID}' AND n.title = '{node_title}'
          AND n.outputs IS NOT NULL
        ORDER BY r.created_at DESC LIMIT 10;
    """)
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            d = json.loads(line)
        except Exception:
            continue
        usage = d.get("usage", {}) or {}
        pt = usage.get("prompt_tokens", 0)
        ct = usage.get("completion_tokens", 0)
        text = d.get("text", "") or d.get("reasoning_content", "")
        # 从别的字段拿耗时不好拿，这里跳过
        print(f"{node_title:<8} {'':>8} {pt:>8} {ct:>12} {len(text):>10}")
