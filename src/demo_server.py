#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ChatBI 演示页面（零依赖，只用标准库）

用法:
    set DIFY_APP_KEY=app-xxxxxxxx
    python demo_server.py
    浏览器打开 http://127.0.0.1:8600
"""
import json
import os
import sys
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

BASE_URL = os.environ.get("DIFY_BASE_URL", "http://localhost/v1").rstrip("/")
APP_KEY = os.environ.get("DIFY_APP_KEY", "").strip()
PORT = int(os.environ.get("DEMO_PORT", "8600"))

EXAMPLES = [
    "深圳有多少客户？",
    "电脑办公分类的销售额是多少？",
    "6月份的销售额是多少？",
    "消费最高的客户是谁？",
    "卖得最好的商品是什么？",
    "3月份有多少个订单？",
    "华为 Mate 60 Pro 一共卖了多少件？",
    "客户的身份证号是多少？",
]


def ask(question, retries=2):
    """调用 Dify 工作流

    retries: 失败重试次数。LLM 偶发输错（走兜底分支）时自动重试。
    """
    last = None
    for attempt in range(retries + 1):
        result = _ask_once(question)
        # 成功，或者不是"误走兜底"的情况，直接返回
        if result.get("status") == "succeeded" and result.get("data"):
            if attempt > 0:
                result["retried"] = attempt
            return result
        last = result
        # 拒答类问题不该重试（那是正确答案）
        if result.get("err_text") and "找不到" in result["err_text"]:
            # 可能是真拒答，也可能是误判，重试一次看看
            if attempt < retries:
                time.sleep(1)
                continue
        if attempt < retries:
            time.sleep(1)
    return last


def _ask_once(question):
    """单次调用"""
    payload = json.dumps({
        "inputs": {"text": question},
        "response_mode": "blocking",
        "user": "demo",
    }).encode()

    req = urllib.request.Request(
        f"{BASE_URL}/workflows/run", data=payload,
        headers={"Authorization": f"Bearer {APP_KEY}", "Content-Type": "application/json"},
        method="POST")

    t0 = time.perf_counter()
    with urllib.request.urlopen(req, timeout=400) as resp:
        body = json.loads(resp.read().decode())
    ms = int((time.perf_counter() - t0) * 1000)

    d = body.get("data", {})
    o = d.get("outputs", {}) or {}

    # Dify 工作流的输出字段名可能随版本变化，这里兼容多种命名
    # V1/V2: ans / data / err_text
    # V3:    ans1（正常解读）/ ans2（SQL错误）/ ans3（找不到实体）
    answer = o.get("ans1") or o.get("ans") or ""
    err_msg = o.get("ans2") or o.get("ans3") or o.get("err_text") or ""
    data = o.get("data") or ""

    return {
        "status": d.get("status"),
        "answer": answer,
        "data": data,
        "err_text": err_msg,
        "raw_outputs": o,
        "elapsed_ms": ms,
        "error": d.get("error"),
    }


HTML = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>ChatBI - 自然语言查数</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    font-family: -apple-system, "Segoe UI", "Microsoft YaHei", sans-serif;
    background: #f7f8fa; color: #1f2328; padding: 24px;
    line-height: 1.6;
  }
  .wrap { max-width: 900px; margin: 0 auto; }
  header { margin-bottom: 24px; }
  h1 { font-size: 24px; font-weight: 600; margin-bottom: 6px; }
  .sub { color: #656d76; font-size: 14px; }
  .card {
    background: #fff; border: 1px solid #d0d7de; border-radius: 10px;
    padding: 20px; margin-bottom: 16px;
  }
  label { font-size: 13px; font-weight: 600; color: #57606a; display: block; margin-bottom: 8px; }
  .input-row { display: flex; gap: 10px; }
  input[type=text] {
    flex: 1; padding: 11px 14px; font-size: 15px; border: 1px solid #d0d7de;
    border-radius: 8px; outline: none; font-family: inherit;
  }
  input[type=text]:focus { border-color: #0969da; box-shadow: 0 0 0 3px rgba(9,105,218,.1); }
  button {
    padding: 11px 24px; font-size: 14px; font-weight: 600; color: #fff;
    background: #1f883d; border: none; border-radius: 8px; cursor: pointer;
    font-family: inherit; white-space: nowrap;
  }
  button:hover { background: #1a7f37; }
  button:disabled { background: #94d3a2; cursor: default; }
  .examples { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 14px; }
  .chip {
    padding: 6px 12px; font-size: 13px; background: #f6f8fa; color: #0969da;
    border: 1px solid #d0d7de; border-radius: 16px; cursor: pointer;
    font-family: inherit;
  }
  .chip:hover { background: #eaeef2; }
  .meta { font-size: 12px; color: #656d76; margin-bottom: 14px; }
  .meta span { margin-right: 16px; }
  .badge {
    display: inline-block; padding: 2px 8px; border-radius: 10px;
    font-size: 11px; font-weight: 600;
  }
  .ok { background: #dafbe1; color: #1a7f37; }
  .warn { background: #fff8c5; color: #9a6700; }
  .fail { background: #ffebe9; color: #cf222e; }

  .section-title {
    font-size: 14px; font-weight: 600; color: #57606a;
    margin: 18px 0 10px; padding-bottom: 6px; border-bottom: 1px solid #d0d7de;
  }
  .table-wrap { overflow-x: auto; margin: 12px 0; }
  table {
    border-collapse: collapse; font-size: 13.5px;
    font-family: "SF Mono", Consolas, monospace;
    min-width: 280px;
  }
  th, td { padding: 8px 14px; text-align: left; border: 1px solid #d0d7de; }
  th { background: #f6f8fa; font-weight: 600; }
  td { background: #fff; }

  .answer { font-size: 14px; }
  .answer h3 { font-size: 15px; margin: 16px 0 8px; color: #1f2328; }
  .answer h3:first-child { margin-top: 0; }
  .answer ul, .answer ol { margin: 8px 0 8px 22px; }
  .answer li { margin: 4px 0; }
  .answer strong { color: #cf222e; font-weight: 600; }

  .err-box {
    background: #ffebe9; border-left: 3px solid #cf222e;
    padding: 12px 16px; border-radius: 6px; font-size: 14px; color: #a40e26;
  }
  .loading { text-align: center; padding: 40px; color: #656d76; }
  .spinner {
    display: inline-block; width: 24px; height: 24px;
    border: 3px solid #d0d7de; border-top-color: #0969da;
    border-radius: 50%; animation: spin .8s linear infinite;
  }
  @keyframes spin { to { transform: rotate(360deg); } }
  .empty { color: #656d76; font-size: 14px; padding: 20px; text-align: center; }
</style>
</head>
<body>
<div class="wrap">
  <header>
    <h1>ChatBI</h1>
    <div class="sub">用自然语言查数据库，自动生成 SQL 并解读结果</div>
  </header>

  <div class="card">
    <label>请输入你的问题</label>
    <div class="input-row">
      <input type="text" id="q" placeholder="例如：6月份哪个品类卖得最好？" autocomplete="off">
      <button id="go">查询</button>
    </div>
    <div class="examples" id="examples"></div>
  </div>

  <div id="out"></div>
</div>

<script>
const EXAMPLES = __EXAMPLES__;

const ex = document.getElementById('examples');
EXAMPLES.forEach(t => {
  const b = document.createElement('button');
  b.className = 'chip';
  b.textContent = t;
  b.onclick = () => { document.getElementById('q').value = t; run(); };
  ex.appendChild(b);
});

const out = document.getElementById('out');
const btn = document.getElementById('go');
const inp = document.getElementById('q');

function esc(s) {
  return String(s).replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
}

// 把 markdown 表格转成 HTML table
function mdTable(md) {
  const lines = md.trim().split('\n').filter(l => l.trim());
  if (lines.length < 2) return null;
  const rows = lines.filter(l => !/^\s*\|?[\s:|-]+\|?\s*$/.test(l));
  if (!rows.length) return null;
  let html = '<div class="table-wrap"><table>';
  rows.forEach((line, i) => {
    const cells = line.replace(/^\||\|$/g, '').split('|').map(c => c.trim());
    const tag = i === 0 ? 'th' : 'td';
    html += '<tr>' + cells.map(c => `<${tag}>${esc(c)}</${tag}>`).join('') + '</tr>';
  });
  html += '</table></div>';
  return html;
}

// 简单 markdown 渲染
function mdRender(md) {
  if (!md) return '';
  let html = esc(md);

  // 代码块
  html = html.replace(/```[\s\S]*?```/g, m => {
    const code = m.replace(/```\w*\n?/g, '').replace(/```$/, '');
    return `<pre style="background:#f6f8fa;padding:12px;border-radius:6px;overflow-x:auto;font-size:13px">${code}</pre>`;
  });

  // 标题
  html = html.replace(/^### (.+)$/gm, '<h3>$1</h3>');
  html = html.replace(/^## (.+)$/gm, '<h3>$1</h3>');
  html = html.replace(/^# (.+)$/gm, '<h3>$1</h3>');

  // 粗体
  html = html.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');

  // 列表
  html = html.replace(/^[•\-*] (.+)$/gm, '<li>$1</li>');
  html = html.replace(/^\d+\. (.+)$/gm, '<li>$1</li>');
  html = html.replace(/(<li>[\s\S]*?<\/li>)(?!\s*<li>)/g, '<ul>$1</ul>');

  // 表格（转成 HTML 后不再处理）
  const tableBlocks = [];
  html = html.replace(/((?:\|.*\|\s*\n)+)/g, m => {
    const idx = tableBlocks.length;
    tableBlocks.push(mdTable(m) || m);
    return `\x00TABLE${idx}\x00`;
  });

  // 段落
  html = html.split('\n\n').map(p => {
    if (/^\s*<(h3|ul|ol|pre|div)/.test(p.trim())) return p;
    if (!p.trim()) return '';
    return `<p>${p}</p>`;
  }).join('');

  // 换行
  html = html.replace(/\n/g, '<br>');

  // 恢复表格（注意 <br> 会破坏表格，先恢复）
  html = html.replace(/\x00TABLE(\d+)\x00/g, (m, i) => tableBlocks[i]);
  html = html.replace(/<br>(<\/table>)/g, '$1');

  return html;
}

async function run() {
  const q = inp.value.trim();
  if (!q) return;

  btn.disabled = true;
  out.innerHTML = '<div class="card loading"><div class="spinner"></div><div style="margin-top:12px">正在生成 SQL 并执行查询...</div></div>';

  try {
    const r = await fetch('/api/query', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({question: q})
    });
    const d = await r.json();
    render(q, d);
  } catch (e) {
    out.innerHTML = `<div class="card"><div class="err-box">请求失败：${esc(e.message)}</div></div>`;
  } finally {
    btn.disabled = false;
  }
}

function render(q, d) {
  if (d.error) {
    out.innerHTML = `<div class="card"><div class="err-box">${esc(d.error)}</div></div>`;
    return;
  }

  const statusMap = {
    'succeeded': ['ok', '成功'],
    'partial-succeeded': ['warn', '部分成功（走了兜底分支）'],
    'failed': ['fail', '失败'],
  };
  const [cls, label] = statusMap[d.status] || ['fail', d.status];

  let html = '<div class="card">';
  html += `<div class="meta">
    <span>状态 <span class="badge ${cls}">${label}</span></span>
    <span>耗时 ${d.elapsed_ms} ms</span>
  </div>`;

  // 错误兜底分支
  if (d.err_text) {
    html += '<div class="section-title">系统提示</div>';
    html += `<div class="err-box">${esc(d.err_text)}</div>`;
    html += '</div>';
    out.innerHTML = html;
    return;
  }

  // 分析结果（LLM 输出里已含查询结果表格和标题）
  if (d.answer) {
    html += `<div class="answer">${mdRender(d.answer)}</div>`;
  } else if (d.data) {
    // 没有分析文本时，才单独显示数据
    html += '<div class="section-title">🗃️ SQL 查询结果</div>';
    const t = mdTable(d.data);
    html += t || `<pre style="font-size:13px;overflow-x:auto">${esc(d.data)}</pre>`;
  }

  html += '</div>';
  out.innerHTML = html;
}

btn.onclick = run;
inp.addEventListener('keydown', e => { if (e.key === 'Enter') run(); });
inp.focus();

// 支持通过 URL 参数直接查询，方便分享和截图
// 例如 http://127.0.0.1:8600/?q=6月份的销售额是多少？
const params = new URLSearchParams(location.search);
const presetQ = params.get('q');
if (presetQ) {
  inp.value = presetQ;
  run();
}
</script>
</body>
</html>
"""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        return

    def do_GET(self):
        # 忽略查询参数，/ 和 /index.html 都返回页面
        path = self.path.split("?")[0]
        if path in ("/", "/index.html"):
            html = HTML.replace("__EXAMPLES__", json.dumps(EXAMPLES, ensure_ascii=False))
            body = html.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_error(404)

    def do_POST(self):
        if self.path != "/api/query":
            self.send_error(404)
            return

        length = int(self.headers.get("Content-Length", 0))
        try:
            req = json.loads(self.rfile.read(length).decode("utf-8"))
        except Exception as e:
            return self._json({"error": f"请求解析失败: {e}"})

        q = (req.get("question") or "").strip()
        if not q:
            return self._json({"error": "问题不能为空"})
        if not APP_KEY:
            return self._json({"error": "未设置 DIFY_APP_KEY 环境变量"})

        try:
            result = ask(q)
        except Exception as e:
            return self._json({"error": f"调用工作流失败: {e}"})

        self._json(result)

    def _json(self, obj):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main():
    if not APP_KEY:
        print("!" * 64)
        print("未设置 DIFY_APP_KEY")
        print("!" * 64)
        print("  PowerShell:  $env:DIFY_APP_KEY=\"app-xxxx\"")
        print("  CMD:         set DIFY_APP_KEY=app-xxxx")
        print()
        return 1

    print("=" * 64)
    print("ChatBI 演示页面")
    print("=" * 64)
    print(f"  地址: http://127.0.0.1:{PORT}")
    print(f"  Dify: {BASE_URL}")
    print(f"  按 Ctrl+C 停止")
    print("=" * 64)

    server = HTTPServer(("127.0.0.1", PORT), Handler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")
    return 0


if __name__ == "__main__":
    sys.exit(main())
