# SDR Demo Agent — Leads Plaza

一个能跑通 **discover → buy → outreach → report** 全链的开源示例 Agent。

这是 Leads Plaza 的分销实验：与其等 agent 来买 leads，不如批量制造"会买 leads 的 agent"。这个 demo 跑通一次只要 5 分钟，README 里硬编码了我们的端点——**每个 fork/clone 都是我们的分销渠道**。

```
                    ┌─────────────┐
                    │ SDR Demo    │
                    │ Agent       │
                    └──────┬──────┘
                           │ MCP / 本地调用
              ┌────────────▼────────────┐
              │  Leads Plaza MCP Server │
              │  search_leads           │  免费浏览摘要
              │  buy_lead (x402)        │  微支付拿完整 lead
              │  report_conversion      │  回传 → 动态定价
              └─────────────────────────┘
```

## 5 分钟跑通

```bash
git clone https://github.com/liangh2020/sdr-demo-agent.git   # 仓库名待定
cd sdr-demo-agent
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python agent.py          # 零配置，全 mock，一条命令出结果
```

默认：`--mode local`（直调本地 MCP server 逻辑）+ `--pay mock`（模拟支付，零真钱）
+ dry-run（只打印邮件不发送）+ `--outcome converted`。

## 默认端点（硬编码）

| 用途 | 默认值 | 改法 |
|---|---|---|
| Leads MCP server | `../mcp_server`（与 [leads-plaza-mcp-server](https://github.com/liangh2020/leads-plaza-mcp-server) 同级）或 `LEADS_MCP_SERVER_DIR` 环境变量 | `--mode mcp` 走真实 MCP stdio 协议 |
| MCP server 仓库 | https://github.com/liangh2020/leads-plaza-mcp-server | 见下 |

## 如何指向你自己的 leads API

`agent.py` 里所有调用都经过 `LeadsClient` 抽象（`search_leads` / `buy_lead` /
`report_conversion` 三个方法）。想接自己的数据源：

1. 继承 `LeadsClient` 实现这三个方法（参考 `LocalClient`，约 20 行）；
2. 在 `main()` 里把 `LocalClient()` 换成你的实现。

字段约定：`search_leads` 返回 `{"leads": [{id, title, qa_score, price_usd, ...}]}`；
`buy_lead` 返回完整 lead（含 `contact`）；`report_conversion` 接受
`converted | no_response | bad_lead`。

## 诚实声明

- **当前仅 base-sepolia 测试网，主网未开。** `--pay mock` 是本地模拟支付，不产生真实结算。
- `--pay testnet` 展示真实 x402 402 流程（`paymentRequirements`），需要钱包做 EIP-3009
  签名后带 `X-PAYMENT` 重试——demo 不持有私钥，到此止步。配
  `LEADS_NETWORK=base-sepolia` + `LEADS_PAY_TO=<收款地址>` 可看测试网参数。
- 外联默认 dry-run；`--live` 需自备 SMTP（`SMTP_HOST/PORT/USER/PASS/FROM`）。
- 不要把没验证的功能写进你 fork 的 README。

## 常用姿势

```bash
python agent.py --icp funding_hiring_signal --limit 3   # 换意向类目
python agent.py --mode mcp                              # 走真实 MCP stdio 协议
python agent.py --pay testnet                           # 看真实 x402 402 流程
python agent.py --outcome bad_lead --reason "联系方式失效"  # 坏单 SLA 路径
python agent.py --live --to prospect@company.com        # 真发邮件（需 SMTP_*）
python agent.py --sender "Marco" --product "AI SDR 外包服务"  # 换署名和产品
```

## 示例运行（2026-10-18 实测，`python agent.py` 默认参数）

```
[mode=local] SDR Demo Agent → Leads Plaza (https://github.com/liangh2020/leads-plaza-mcp-server)

============================================================
  STEP: 1/4 DISCOVER — 按 ICP 找候选 leads
============================================================
找到 4 条在售摘要（免费浏览，不含 contact）：
  [1] ld-2026-10-13-42d9d6e1 | qa=84.5 | $0.27 | 新鲜度=0.283
      招聘： AI 质量评估工程师
  [2] ld-2026-10-13-b4e8655c | qa=77.0 | $0.27 | 新鲜度=0.283
      [上海｜ AI 创业团队招聘] Agent / AI Infra / LLM Data / AI 产品经理｜ AI 社交
  [3] ld-2026-10-13-726569c7 | qa=77.0 | $0.27 | 新鲜度=0.283
      [上海招聘] Agent/全栈开发
  [4] ld-2026-10-13-96e531c7 | qa=77.0 | $0.27 | 新鲜度=0.283
      [蚂蚁集团招聘] agent 研发工程师（上海，杭州）

选中购买：ld-2026-10-13-42d9d6e1（qa 最高；可用 --lead-id 指定）

============================================================
  STEP: 2/4 BUY — x402 购买
============================================================
支付成功（MOCK）：tx=MOCK-TX-17725A38110A，价格 $0.27
拿到完整 lead（含 contact）：{"type": "post", "value": "https://www.v2ex.com/t/1248322"}
⚠ 本地联调占位，非真实结算

============================================================
  STEP: 3/4 OUTREACH — 生成个性化外联邮件
============================================================
Subject: Re: 招聘： AI 质量评估工程师

你好，

看到你在公开渠道留下这个需求：
「招聘： AI 质量评估工程师」
岗位名称：AI 质量评估工程师 薪资：20-35k·13 薪 ｜ 常州·新北区 ｜ 3-5 年 ｜ 统招本科及以上 岗位职责 负责公司 AI 平台的质量保障，从 0 到 1 搭建质量与评测体系——你是 owner ，不是执行者； 构建面向大模型/Agent 系统的评测体系：评测数据集、golden ……
（来源：https://www.v2ex.com/t/1248322）

我是 Alex，在做 an AI automation SDR service。不知道你这边是否已在对接供应商，我们可以先免费帮你梳理一版需求清单。

方便约 15 分钟聊下你的具体场景吗？合适就深入，不合适就当交个朋友。

Alex

[dry-run] 未发送。加 --live 并配置 SMTP_* 环境变量可真发。

============================================================
  STEP: 4/4 REPORT — 转化回传
============================================================
{
  "status": "recorded",
  "lead_id": "ld-2026-10-13-42d9d6e1",
  "outcome": "converted",
  "reward": "感谢回传！已返还 0.02 USD credit（成交价的 7%），下次购买可抵扣。"
}

成交回传触发 7% credit 返还——这就是驱动动态定价的数据飞轮。

============================================================
  DONE：discover → buy → outreach → report 全链跑通
  把这套循环交给真正的 SDR agent，就是 Leads Plaza 的分销网络。
============================================================
```

## License

MIT
