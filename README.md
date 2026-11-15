# ChatBI：用 Dify 搭的自然语言查数助手

把中文问题变成 SQL，执行后用人话解释结果。用 Dify 工作流编排，数据是我按电商业务造的一套 5 表 MySQL 库。

这个项目做了**两个版本**，核心区别是表结构怎么给到大模型。对比结论在 [docs/两版对比](docs/两版对比-Prompt硬编码vsRAG.md) 里，这里先说结果：

> **5 张表的规模下，RAG 版没有提升准确率，反而因为规则从 Prompt 移到知识库、部分规则没被检索到，导致二级分类查询退化了。RAG 的价值要到 20 张表以上才显现。**

## 两个版本

| | V1：Prompt 硬编码 | V2：RAG 检索 |
|---|---|---|
| 表结构位置 | 写进 System Prompt | 存知识库，检索注入 |
| Prompt 长度 | **8900 字符** | **506 字符** |
| 工作流节点 | 7 | 9（多了 Knowledge Retrieval、Code） |
| 二级分类查询 | ✅ 正确 | ❌ 退化 |
| 改表结构的成本 | 改 Prompt | 改知识库文档 |

两个版本的工作流都保留在 `dify_workflow/` 里，可以分别导入对比。

## 效果

![demo](assets/demo-result.png)

留出测试（10 个 Prompt 里没出现过的问法）：**70%**

这个数字后面会详细说为什么。

## 工作流结构

**V1（无 RAG）：**

```
用户输入 → LLM生成SQL → SQL Query → IF/ELSE → LLM解读 → 输出
                                       ↓
                                    执行失败 → 错误提示
```

![工作流](assets/architecture.png)

**V2（RAG 版）** 在用户输入后加了两个节点：

```
用户输入 → Knowledge Retrieval → LLM生成SQL → SQL Query → Code → IF/ELSE → LLM解读 → 输出
```

- `Knowledge Retrieval`：从知识库检索相关表结构（top_k=4）
- `Code`：判断 SQL 结果是 success / empty / error

## 关于准确率，需要说清楚

### 第一版测试：100%，但是过拟合

我最初设计了 14 个测试问题，全部通过。后来检查发现——**其中 6 个和 Prompt 里的示例几乎是同一句话**：

| 测试问题 | Prompt 示例 |
|---|---|
| 电脑办公分类的销售额是多少？ | 示例 1b 就是这个分类 |
| 6月份的销售额是多少？ | 示例 7 就是这个月份 |
| 消费最高的客户是谁？ | 示例 8 就是这个问题 |
| 深圳有多少客户？ | 示例 4 就是这个问题 |

**这不叫测试，是把答案抄进了题里。**

### 第二版测试：留出集 70%

重新设计了 10 个问题，全部避开 Prompt 用过的词（换成女装、男装、4月、2月、广州、东莞、MacBook）：

| ID | 问题 | 结果 |
|---|---|---|
| H1 | 女装分类的销售额是多少？ | ✅ 2364 |
| H2 | 男装卖了多少件？ | ✅ 3 |
| H3 | 4月份有多少个订单？ | ✅ 4 |
| H4 | 2月份的销售额是多少？ | ✅ 13955 |
| H5 | 广州有多少客户？ | ✅ 4 |
| H6 | 东莞客户的总消费额是多少？ | ❌ 返回明细，没聚合 |
| H7 | 深圳的女性客户消费总额是多少？ | ✅ 6802 |
| H8 | 哪个商品的毛利率最高？ | ❌ 算成利润总额 |
| H9 | 哪些商品从来没被买过？ | ✅ 云南蓝莓 |
| H10 | MacBook Air 卖了多少件？ | ❌ 精确匹配失败 |

**70% 才是真实水平。**

三个失败各有原因：

**H6**：问总量却返回明细（199、59 两行），模型忘了 `SUM`

**H8**：「毛利率」和「毛利」混了。期望不锈钢保温杯（(129-45)/129=65.1%），实际返回 MacBook Air（利润 7196 元）

**H10**：数据库里是 `MacBook Air M3`，模型用了 `= 'MacBook Air'`，应该用 `LIKE '%MacBook Air%'`

### 50 样本测试（V2 版）

后来做了 50 个样本的测试，只测「SQL 生成 + 错误处理」这段（不含 LLM 解读）：

| 阶段 | 得分 |
|---|---|
| SQL 生成率 | 48/49 = 98.0% |
| SQL 执行成功率 | 48/49 = 98.0% |
| 错误处理正确率 | 49/49 = **100%** |
| 答案正确率 | 46/49 = **93.9%** |

**这个数字比 70% 高，但不矛盾**——50 样本里有相当一部分问题和 V1 的 Prompt 示例重合，而且修正了之前标准答案的错误。

剩下的 3 个失败都是同一个问题：**二级分类查询退化**（详见下节）。

## 踩过的坑

### 1. Prompt 被浏览器翻译搞坏了

有段时间工作流完全不能用，查出来 Prompt 变成了：

```sql
选择总和（oi.quantity）为total_sales
来自order_items oi
JOIN 命令 o on 的 oi.order_id = o.id
工会
精选
```

`SELECT` 变成「选择」，`FROM` 变成「来自」，`UNION` 变成「工会」。

**原因是浏览器的网页翻译把 Dify 编辑器里的代码也翻了。**

教训：**Prompt 一律在本地文件维护**（`prompts/` 目录），Dify 里只是部署。改完从文件粘贴，不要在网页编辑器里直接写。

### 2. 二级分类查询反复出错

这是最顽固的问题，在 V1 里修好了，V2 又退化了。

**错误 SQL：**

```sql
WHERE p.category_id IN (
    SELECT id FROM categories WHERE parent_id = (SELECT id FROM categories WHERE name = '电脑办公')
)
```

「电脑办公」是二级分类，没有子分类。这样查结果是空的。

**正确写法：**

```sql
WHERE p.category_id = (SELECT id FROM categories WHERE name = '电脑办公')
```

**为什么 V1 对 V2 错**：V1 的 Prompt 里有专门的 2 条规则 + 2 个示例讲这个；V2 精简到 4 条规则时砍掉了。

**教训：RAG 检索的是「知识」，但模型需要「指令」。** 描述性的知识（parent_id 是什么）容易被忽略，明确的动作指令（二级分类不要递归）不容易。

### 3. 直接改数据库把知识库搞坏了

我在更新 RAG 文档时直接操作了数据库（删除分段 → 插入新内容 → 重建向量）。

**结果：RAG 检索返回空，持续了 11 分钟。**

连锁反应：

```
RAG 检索为空
  → LLM 没有任何表结构参考
  → 编造了不存在的表名 product_categories
  → SQL 报错 Table 'cd.product_categories' doesn't exist
  → 所有查询失败
```

**原因**：Dify 的知识库不只是「文档 + 向量」，还维护内部状态（分段 ID、index_node_id、关键词等）。直接改数据库绕过了这套机制。

**正确做法**：在 Dify 界面里编辑文档，让它自己重新分段建索引。

### 4. 销售额口径选错

问「6月份的销售额」，返回 19114，正确答案 15284。

两个数都对，只是口径不同：

| 字段 | 数值 | 含义 |
|---|---|---|
| `SUM(oi.subtotal)` | 19114 | 商品原价小计（GMV） |
| `SUM(o.pay_amount)` | 15284 | 实际到账（扣了整单优惠） |

差 3830 元，正好是 6 月的优惠总额。

**这个问题是我修前一个 bug 时引入的**——为了修分类销售额，加了「销售额按明细汇总」规则，结果时间维度也被套用了。

**修复**：把规则拆成三种场景（时间维度/商品维度/客户维度），每种给判断依据。

### 5. 一个「失败」其实是标准答案错了

测试「4月份有多少个订单」时，模型返回 5，我判它错（期望 4）。

查数据库发现：4 月共有 5 笔订单，其中 1 笔是「待付款」。**模型没加 `status='已完成'` 过滤，恰好是对的**——问「有多少个订单」就该算全部。

**是我的标准答案写错了**（我按「已完成」算的）。

这个提醒我：**测试失败时，先怀疑标准答案，再怀疑模型。**

## Prompt 的演进

V1 版本从 1486 字符一路加到 8900 字符：

| 阶段 | 规模 | 说明 |
|---|---|---|
| 初始 | 1486 字符 | 从 Dify 模板改的 |
| 修机翻 | 3391 字符 | 修完译文污染 |
| 加业务规则 | 9570 字符 | 加销售额口径、分类递归等 |
| 精简 | 8900 字符 | 合并重复规则 |
| **V2 RAG 版** | **506 字符** | 表结构移到知识库 |

**Prompt 不是越长越好。** 每加一条规则都可能和已有规则冲突——加了「销售额用 subtotal」修好了分类统计，却让时间统计出错。

所以后来不写「销售额用 X」这种笼统规则，而写「**什么场景用什么**」。

## 性能数据

各节点耗时（20 次运行平均）：

| 节点 | 模型 | 平均耗时 | 占比 |
|---|---|---|---|
| LLM（生成 SQL） | DeepSeek-V3（云端 API） | **4.4s** | 15% |
| SQL Query | MySQL | 0.06s | 0.2% |
| **LLM 2（解读）** | qwen3.5:4b（本地） | **20s** | **80%** |
| Knowledge Retrieval | bge-m3 | 1.0s | 4% |

**瓶颈在本地解读模型，不在 SQL 生成。**

SQL 生成走云端 DeepSeek-V3，4 秒就出来了。本地 qwen3.5:4b 生成 400 字符的分析报告要 20 秒——因为显卡只有 4GB，模型 3.49GB 只能装 50% 进 GPU，剩下在 CPU 跑。

## 项目结构

```
ChatBI-Dify/
├── README.md                      本文件
├── docs/
│   └── 两版对比-Prompt硬编码vsRAG.md   详细对比
├── prompts/
│   ├── llm1-sql-generator.txt         V2 生成的 SQL Prompt（506 字符）
│   ├── llm1-sql-generator-v1-full.txt V1 版（8900 字符，含 10 个示例）
│   └── llm2-analyst.txt               结果解读 Prompt
├── dify_workflow/
│   ├── chatbi_workflow.json           V2 工作流（RAG 版）
│   ├── chatbi_v1_no_rag.json          V1 工作流
│   └── workflow_structure.md          节点说明
├── rag/
│   └── 06_全局关联关系与业务红线.md      知识库文档之一
├── sql/
│   └── seed.sql                       建表 + 测试数据
├── src/
│   ├── call_dify_api.py               Python 调用示例
│   └── demo_server.py                 演示页面（零依赖）
├── tests/
│   ├── batch_test.py                  14 个用例的批量测试
│   ├── holdout_test.py                10 个留出测试
│   ├── eval_50.py                     50 样本评测
│   ├── eval_sql_stage_fixed.py        只测 SQL 生成阶段
│   ├── diagnose.py                    单问题诊断（打印生成的 SQL）
│   ├── perf_breakdown.py              节点耗时分析
│   └── token_analysis.py              token 用量分析
├── results/                           测试结果存档
└── assets/                            截图
```

## 复现

**1. 准备数据库**

```bash
mysql -u root -p -e "CREATE DATABASE cd CHARACTER SET utf8mb4;"
mysql -u root -p cd < sql/seed.sql
```

5 张表：categories（两级分类）、customers、products、orders、order_items

**2. 导入工作流**

Dify → 工作流 → 导入 DSL → 选 `dify_workflow/` 下的 json

**3. 配置数据库连接**

SQL Query 节点需要填（Dify 跑在 Docker 里的话用宿主机 IP）：

| 参数 | 值 |
|---|---|
| db_host | 宿主机 IP（WSL 下用 `ip route show default` 查） |
| db_port | 3306 |
| db_name | cd |
| db_username | root |
| db_password | 你的密码 |
| db_properties | `useUnicode=true&characterEncoding=utf8&serverTimezone=Asia/Shanghai&useSSL=false&allowPublicKeyRetrieval=true` |

⚠️ **`characterEncoding=utf8` 必须加**，否则中文全显示成问号。

**4. 更新 Prompt**

把 `prompts/` 下的内容粘贴到对应 LLM 节点，然后**点发布**。

（改了不发布，API 调用走的还是旧版本。这个也踩过。）

**5. 跑测试**

```powershell
$env:DIFY_APP_KEY="app-xxxx"
$env:DB_PASSWORD="你的密码"
python tests/eval_50.py
```

## 演示页面

零依赖，只用 Python 标准库：

```powershell
$env:DIFY_APP_KEY="app-xxxx"
python src/demo_server.py
# 打开 http://127.0.0.1:8600
```

## 不足

**数据库太小。** 5 张表、几十行数据。真实业务可能几百张表，那时候才需要 RAG。

**没有真实用户。** 所有测试问题都是我自己造的，且和 Prompt 有一定重合。

**测试集不够严谨。** 14 + 10 + 50 个样本，样本量小，标准答案也出过错（见上面第 5 条）。

**没有做多轮对话。** 现在是单轮问答，不支持上下文。要做的话得改成 chatflow，还要处理 token 膨胀和历史压缩。

**没有权限控制。** 连接用的是 root 账号。

**性能不适合生产。** 一次查询 20-40 秒，主要耗在本地解读模型上。

## 下一步

按价值排序：

1. **加多轮记忆** —— 改成 chatflow，支持追问（「那广州呢？」）
2. **图表可视化** —— 把查询结果渲染成柱状图/折线图
3. **修二级分类查询** —— 在 RAG 文档里补「动作指令」而不是「知识描述」
4. **接入更多表** —— 验证 RAG 在 20+ 表规模下的真实价值

## 这个项目说明了什么

不完全是「我会用 Dify」，更多是几件具体的事：

**知道怎么验证效果** —— 先做 14 个测试发现是过拟合，重做留出集得到 70%

**知道什么时候不用某个技术** —— 5 张表上 RAG 是过度设计，虽然我做了 V2，但结论是 V1 更合适

**能从故障里定位根因** —— 检索返回空，一路查到「直接改数据库绕过 Dify 内部状态」

**会区分「标准答案错」和「模型错」** —— 4月订单数那次，是我的标准答案错了

---

代码 MIT。
