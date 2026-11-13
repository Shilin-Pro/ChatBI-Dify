#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""分析 ChatBI 工作流各节点耗时分布"""
import json
import os
import sys
import io
import subprocess

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

APP_ID = "9bb25f01-8771-40f5-819b-0a9122264600"


def psql(sql):
    r = subprocess.run(
        ["wsl", "-e", "bash", "-c",
         f'docker exec docker-db_postgres-1 psql -U postgres -d dify -t -A -c "{sql}"'],
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    return r.stdout.strip()


def main():
    print("=" * 78)
    print("ChatBI 工作流节点耗时分析（最近 15 次运行）")
    print("=" * 78)

    # 取最近运行
    raw = psql(f"""
        SELECT r.id || '|' || r.created_at || '|' || COALESCE(r.elapsed_time, 0)
        FROM workflow_runs r
        JOIN workflows w ON w.id = r.workflow_id
        WHERE w.app_id = '{APP_ID}'
        ORDER BY r.created_at DESC LIMIT 15;
    """)

    if not raw:
        print("  无数据")
        return 1

    runs = []
    for line in raw.splitlines():
        parts = line.split("|")
        if len(parts) >= 3:
            runs.append({"id": parts[0], "created": parts[1], "elapsed": float(parts[2])})

    print(f"  采集到 {len(runs)} 次运行\n")

    # 对每次运行取节点耗时
    nb = {"LLM": [], "SQL Query": [], "LLM 2": [], "IF/ELSE 2": [], "Output": []}

    for run in runs:
        node_raw = psql(f"""
            SELECT n.title || '|' || COALESCE(n.elapsed_time, 0)
            FROM workflow_node_executions n
            WHERE n.workflow_run_id = '{run['id']}'
            ORDER BY n.created_at;
        """)
        for line in node_raw.splitlines():
            if "|" not in line:
                continue
            title, t = line.rsplit("|", 1)
            try:
                nb.setdefault(title, []).append(float(t))
            except ValueError:
                pass

    print("-" * 78)
    print(f"{'节点':<16} {'次数':>6} {'平均(s)':>10} {'最快(s)':>10} {'最慢(s)':>10}")
    print("-" * 78)

    import statistics
    total_avg = 0
    for title in ["LLM", "SQL Query", "IF/ELSE 2", "LLM 2", "Output"]:
        vals = nb.get(title, [])
        if not vals:
            continue
        avg = statistics.mean(vals)
        total_avg += avg
        print(f"{title:<16} {len(vals):>6} {avg:>10.2f} {min(vals):>10.2f} {max(vals):>10.2f}")

    print("-" * 78)
    print(f"{'合计':<16} {'':>6} {total_avg:>10.2f}")

    # 占比
    print(f"\n各节点占总耗时比例:")
    for title in ["LLM", "SQL Query", "IF/ELSE 2", "LLM 2", "Output"]:
        vals = nb.get(title, [])
        if not vals or total_avg == 0:
            continue
        avg = statistics.mean(vals)
        pct = avg / total_avg * 100
        bar = "█" * int(pct / 2)
        print(f"  {title:<14} {pct:>5.1f}%  {bar}")

    # 总结
    llm1 = statistics.mean(nb.get("LLM", [0]))
    llm2 = statistics.mean(nb.get("LLM 2", [0]))
    sql = statistics.mean(nb.get("SQL Query", [0]))
    print(f"\n结论:")
    print(f"  两个 LLM 合计 {llm1+llm2:.1f}s，占 {(llm1+llm2)/total_avg*100:.1f}%")
    print(f"  SQL 执行 {sql:.3f}s，占 {sql/total_avg*100:.2f}%")

    return 0


if __name__ == "__main__":
    sys.exit(main())
