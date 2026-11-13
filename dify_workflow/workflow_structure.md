# 工作流结构说明

## 节点

| 节点 | 类型 | 说明 |
|---|---|---|
| User Input | `start` | 用户输入 |
| LLM | `llm` | LLM 调用 |
| SQL Query | `tool` | 外部工具 |
| LLM 2 | `llm` | LLM 调用 |
| Output | `end` | 输出 |
| IF/ELSE 2 | `if-else` | 条件分支 |
| Output 3 | `end` | 输出 |

## 连线

- User Input → LLM
- LLM → SQL Query
- SQL Query → IF/ELSE 2
- LLM 2 → Output
- IF/ELSE 2 → LLM 2
- IF/ELSE 2 → Output 3
