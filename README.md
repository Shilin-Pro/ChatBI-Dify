# 用 Dify 搭的 ChatBI：自然语言查数据库

一个能把中文问题变成 SQL、执行后用人话解释结果的查数工具。用 Dify 工作流编排，本地跑 qwen3.5:4b，数据是我按电商业务造的一套 5 表 MySQL 数据。

写这个项目的起因很简单：业务同事每次要数据都得找人写 SQL，我就想试试大模型能不能直接搞定。做下来发现，**难的不是让它生成 SQL，而是让它生成对的 SQL**。

## 效果

14 个测试问题，全过。

| 类型 | 例子 | 结果 |
|---|---|---|
| 单表查询 | 深圳有多少客户？ | 12 ✅ |
| 分类统计 | 电脑办公分类的销售额是多少？ | 44087 ✅ |
| 时间维度 | 6月份的销售额是多少？ | 15284 ✅ |
| 多表关联 | 消费最高的客户是谁？ | 林峰 ✅ |
| 异常兜底 | 客户的身份证号是多少？ | 正确拒答 ✅ |

![demo](assets/demo-result.png)

## 工作流结构

```
用户输入 → LLM生成SQL → SQL Query → IF/ELSE判断 → LLM解读 → 输出
                                        ↓
                                    执行失败 → 错误提示输出
```

![工作流](assets/architecture.png)

7 个节点，其中 IF/ELSE 是错误兜底：SQL 执行失败时走另一条路，给用户友好提示，而不是抛异常。

## 遇到的核心问题

### 1. 分类查询把一级二级搞混

问「电脑办公分类的销售额」，返回 76581，而正确答案是 44087。

查日志发现生成的 SQL 是：

```sql
WHERE o.status = '已完成'
  AND p.category_id IN (
      SELECT id FROM categories WHERE name = '电脑办公'
      UNION
      SELECT c2.id FROM categories c2
      JOIN categories c1 ON c2.parent_id = c1.id
      WHERE c1.name = '数码电器'        ← 写成了父级分类
  )
```

「电脑办公」是二级分类，「数码电器」是它的一级分类。LLM 把两处都套成了父级，等于查了整个数码电器（76581）。

**原因是我给的 Prompt 示例只有「查一级分类」的写法**，LLM 形成了固定模式，遇到二级分类时机械套用。

修复方式是在 Prompt 里明确区分两种情况：

```sql
-- 查一级分类（要包含子分类）
WHERE p.category_id IN (
    SELECT id FROM categories WHERE name = '数码电器'
    UNION
    SELECT c2.id FROM categories c2
    JOIN categories c1 ON c2.parent_id = c1.id
    WHERE c1.name = '数码电器'
)

-- 查二级分类（没有下级，直接等值匹配）
WHERE p.category_id = (SELECT id FROM categories WHERE name = '电脑办公')
```

### 2. 销售额口径选错

问「6月份的销售额」，返回 19114，正确答案 15284。

两个数其实都对，只是口径不同：

| 字段 | 数值 | 含义 |
|---|---|---|
| `SUM(oi.subtotal)` | 19114 | 商品原价小计之和（GMV） |
| `SUM(o.pay_amount)` | 15284 | 实际到账金额（扣了整单优惠） |

差 3830 元，正好是 6 月份订单的优惠总额。

**这个问题是我修前一个 bug 时引入的**：为了修好分类销售额，我加了「销售额按明细汇总」的规则。结果时间维度的销售额也被套用了这个规则。

修复方式是把规则拆成三种场景，每种给判断依据：

| 场景 | 判断特征 | 用哪个字段 |
|---|---|---|
| 按时间/整体 | 问题里没有「哪个商品/分类」 | `pay_amount`，不 JOIN 明细表 |
| 按商品/分类 | 有分类名、商品名 | `oi.subtotal`，必须 JOIN |
| 按客户 | 客户消费额 | `pay_amount` |

### 3. 「最高」类问题返回一堆结果

问「消费最高的客户是谁」，返回了前 5 名。

原因是 Prompt 里写着「明细查询加 LIMIT 100」，LLM 就用了 `LIMIT 100`。但问题是单数，应该只返回一个。

修复：区分「单个」和「排名」：

```
问"最高的那一个" → LIMIT 1
问"前 3 名"     → LIMIT 3
```

### 4. Prompt 被浏览器翻译搞坏了

有段时间工作流完全不能用，检查发现 Prompt 变成了这样：

```sql
选择总和（oi.quantity）为total_sales
来自order_items oi
JOIN 命令 o on 的 oi.order_id = o.id
工会
精选
```

`SELECT` → `选择`，`FROM` → `来自`，`JOIN` → `在`，`UNION` → `工会`。

**原因是浏览器的网页翻译功能把 Dify 编辑器里的代码也翻译了。**

教训：**Prompt 一律在本地文件里维护**（`prompts/` 目录），Dify 里只是「部署」。改完从文件复制粘贴，不要在网页编辑器里直接写。

## Prompt 的迭代过程

| 阶段 | 规模 | 测试通过率 |
|---|---|---|
| 初始版 | 1486 字符 | 无法正常用 |
| 修复机翻 | 3391 字符 | — |
| 补充业务规则 | 9570 字符 | 71.4% |
| 精修场景判断 | 12814 字符 | **100%** |

**这里有个反直觉的点**：Prompt 不是越长越好，而是每加一条规则都可能和已有规则冲突。

比如加了「销售额用 subtotal」修好了分类统计，却让时间统计出错。所以后来我不写「销售额用 X」这种笼统规则，而是写「**什么场景用什么**」。

## 测试是怎么做的

`tests/batch_test.py` 里定义 14 个问题，每个都配标准答案 SQL：

```python
("A1", "销售额", "电脑办公分类的销售额是多少？",
 """SELECT SUM(oi.subtotal) AS v FROM order_items oi
    JOIN products p ON p.id=oi.product_id
    JOIN categories c ON c.id=p.category_id
    JOIN orders o ON o.id=oi.order_id
    WHERE o.status='已完成' AND c.name='电脑办公'""", "v", "value"),
```

运行时会：
1. 直连 MySQL 跑标准答案 SQL
2. 调 Dify 工作流
3. 比对返回值和标准答案

```bash
python tests/batch_test.py
```

输出：

```
ID   分类     结果     问题                          期望          实际
S1   单表     ✅      深圳有多少客户？                12           数值命中
A1   销售额    ✅      电脑办公分类的销售额是多少？          44087.00     数值命中
E1   兜底     ✅      客户的身份证号是多少？                         已拒答
```

## 演示页面

零依赖实现，只用 Python 标准库（不用装 streamlit，避免环境问题）：

```powershell
$env:DIFY_APP_KEY="app-xxxxxxxx"
python src/demo_server.py
# 打开 http://127.0.0.1:8600
```

页面会把大模型的分析报告渲染成 Markdown，表格转成 HTML 展示。

## 数据库

5 张表，电商业务：

| 表 | 说明 | 行数 |
|---|---|---|
| categories | 商品分类（两级） | 10 |
| customers | 客户 | 20 |
| products | 商品 | 20 |
| orders | 订单 | 30 |
| order_items | 订单明细 | 39 |

导入：

```bash
mysql -u root -p cd < sql/seed.sql
```

数据是刻意设计的，留了几个「坑」：

- **订单状态有 6 种**（待付款/已付款/已发货/已完成/已取消/已退款），统计营收不过滤就会算错
- **分类是两级的**，查一级要递归，查二级不用
- **三个金额字段**（total_amount / discount / pay_amount），容易混
- **商品有 cost 字段**，可以算毛利

## 项目结构

```
ChatBI-Dify/
├── prompts/
│   ├── llm1-sql-generator.txt    SQL 生成 Prompt（核心）
│   └── llm2-analyst.txt          结果解读 Prompt
├── dify_workflow/
│   ├── chatbi_workflow.json      工作流导出（已脱敏）
│   └── workflow_structure.md     节点说明
├── sql/
│   └── seed.sql                  建表 + 测试数据
├── src/
│   ├── call_dify_api.py          Python 调用示例
│   └── demo_server.py            演示页面（零依赖）
├── tests/
│   ├── batch_test.py             批量测试（14 个用例）
│   └── diagnose.py               单问题诊断（打印生成的 SQL）
├── results/
│   └── batch_test.json           测试结果存档
└── assets/                       截图
```

## 复现步骤

**1. 准备数据库**

```bash
mysql -u root -p -e "CREATE DATABASE cd CHARACTER SET utf8mb4;"
mysql -u root -p cd < sql/seed.sql
```

**2. 导入工作流**

Dify → 工作流 → 导入 DSL → 选 `dify_workflow/chatbi_workflow.json`

**3. 改配置**

工作流里的 SQL Query 节点需要填数据库连接：

| 参数 | 值 |
|---|---|
| db_host | 你的 MySQL 地址（Docker 里跑 Dify 的话用宿主机 IP） |
| db_port | 3306 |
| db_name | cd |
| db_username | root |
| db_password | 你的密码 |
| db_properties | `useUnicode=true&characterEncoding=utf8&serverTimezone=Asia/Shanghai&useSSL=false&allowPublicKeyRetrieval=true` |

**注意 `characterEncoding=utf8`**：不加的话中文会全部显示成问号。我踩过这个坑。

**4. 更新 Prompt**

把 `prompts/` 下两个文件的内容分别粘贴到 LLM 和 LLM 2 节点，然后**点发布**。

（改了不发布，API 调用走的还是旧版本。这个也踩过。）

**5. 跑测试**

```powershell
$env:DIFY_APP_KEY="app-xxxx"
$env:DIFY_BASE_URL="http://localhost/v1"
python tests/batch_test.py
```

## 不足

**数据库太小。** 5 张表、几十行数据，和真实业务差得远。真实场景可能有几百张表，Prompt 里塞不下表结构，得用 RAG 检索 Schema。我现在没做。

**没有权限控制。** 连接用的是 root 账号。生产环境应该建只读账号，而且工作流层面也该有用户隔离。

**没有并发测试。** 单机本地跑，一次请求要 40-80 秒（qwen3.5:4b 在 CPU 上推理）。这个延迟没法上生产。

**SQL 安全只靠 Prompt 约束。** 现在是在 Prompt 里写「只允许 SELECT」，理论上模型不听话就能写别的。更稳的做法是数据库层面给只读权限。

**测试集还是自己造的。** 14 个问题覆盖了几种典型类型，但样本量太小，说不上有统计意义。

## 大概说明了什么

这个项目最花时间的不是搭工作流，是**反复调试 Prompt**。

中间有一段时间我一直以为是插件的问题——同样的 SQL 直接在数据库跑是对的，但工作流返回的数是错的。后来查日志才发现是 LLM 生成的 SQL 本身写错了，只是错得很隐蔽（分类名写成了父级），肉眼不容易看出来。

所以后来我做了一个 `diagnose.py`，能把每次生成的 SQL 打出来。**调试 LLM 应用，能看到它到底生成了什么，比什么都重要。**

---

代码 MIT。
