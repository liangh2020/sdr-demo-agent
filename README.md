# SDR Demo Agent

5 分钟启动一个会自动找客户、写外联邮件的开源 SDR Agent，内置按需购买验证 leads 的能力。

它跑通完整闭环：**discover**（按意向找候选）→ **buy**（x402 微支付购买）
→ **outreach**（生成个性化外联邮件）→ **report**（转化回传驱动返利与复购）。
回传不是终点——成交返 credit、坏单退 credit，飞轮转起来才是这个 demo 真正要演的东西。

## 诚实声明（先说清楚）

- **当前仅 base-sepolia 测试网，主网未开。** 默认 `--pay mock` 是本地模拟支付，零真钱。
- `--pay testnet` 展示真实 x402 402 流程需走 `--mode mcp`（MCP server 的 x402 链路），且需要你自己的钱包做 EIP-3009 签名——demo 不持有私钥。
- 外联默认 dry-run 只打印不发送；`--live` 需自备 SMTP，且单次运行最多真发 20 封。
- 示例价格（$0.80 / $1.50 / $2.50 三档）是 demo 定价，不是正式报价。

```
                    ┌─────────────┐
                    │ SDR Demo    │
                    │ Agent       │
                    └──────┬──────┘
                           │ 本地直调 / MCP stdio
              ┌────────────▼────────────┐
              │  Leads Plaza 数据池     │
              │  search_leads           │  免费浏览摘要
              │  buy_lead (x402)        │  微支付拿完整 lead
              │  report_conversion      │  回传 → 返利/赔付 → 复购
              └─────────────────────────┘
```

## 5 分钟跑通

```bash
git clone https://github.com/liangh2020/sdr-demo-agent.git
cd sdr-demo-agent
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python agent.py          # 零配置，全 mock，一条命令出结果
```

默认：n8n 英文招聘意向池 + `--pay mock` + dry-run + `--outcome converted`。

## 飞轮演示（推荐先看这个）

```bash
python agent.py --demo-flywheel
```

一轮演完：试单买 3 条 → 回传 1 条 converted → 7% credit 返还入账并打印余额 →
用余额抵扣再买 1 条。敢把"回传驱动复购"演出来，而不是只演一次买断。

```bash
python agent.py --budget 5            # 预算 $5 内按规则批量买（展示采购决策）
python agent.py --outcome bad_lead --reason "帖子已删除"   # 坏单赔付：credit 退回余额
python agent.py --source v2ex         # 切回 V2EX 中文池（经本地 MCP server）
python agent.py --mode mcp            # 走真实 MCP stdio 协议
python agent.py --live --to prospect@company.com   # 真发邮件（需 SMTP_*，≤20 封/次）
```

## 选品规则（"Agent"二字的最低门槛）

默认不买"qa 最高"——按规则选：**qa > 80 且 72 小时内发布且单价在预算内**，
按 qa 降序取。`--lead-id` 可手动指定单条；`--budget` 设总预算，超预算的自动跳过并说明原因。

## 合规承诺

1. **contact 永不含帖内个人联系方式。** 出售的 contact 只有帖链与公司级公开信息；
   帖内的微信/QQ/个人邮箱/手机号永不进入商品（采集端 L1 直接拦截）。
2. **大厂不上架。** 蚂蚁/阿里/腾讯/字节/百度/京东/美团/拼多多/华为/小米/网易/滴滴
   等超出 SMB 意向 ICP 的公司在 discover 环节直接过滤（会打印过滤条数）。
3. **--live 真发邮件时**，请遵守目标地区的反垃圾法规（如 CAN-SPAM / GDPR），
   确保收件人可退订；fork 出去群发造成的后果由发送方承担。

## 数据源

- 默认 `n8n`：n8n 社区 Jobs 板英文招聘意向池（pilot 29 条已上架，质检上架率 25%）。
  帖子是发帖人自愿公开的求购信息；我们卖的是"验证过的意向 + 直达链接 + 质检分"，
  不是联系人名单——买家 agent 自己去帖子页联系。
- `--source v2ex`：经本地 MCP server 读 V2EX 中文池（需 MCP server 在同级目录或
  `LEADS_MCP_SERVER_DIR` 指向它）。

## 如何指向你自己的 leads API

`agent.py` 里所有调用都经过 `LeadsClient` 抽象（`search_leads` / `buy_lead` /
`report_conversion` 三个方法）。想接自己的数据源：

1. 继承 `LeadsClient` 实现这三个方法（参考 `N8NFileClient`，约 60 行）；
2. 在 `main()` 的 `make_client` 里换成你的实现。

字段约定：`search_leads` 返回 `{"leads": [{id, title, qa_score, age_hours,
price_usd, source_url}]}`；`buy_lead` 返回完整 lead（含 `contact`）；
`report_conversion` 接受 `converted | no_response | bad_lead`。

## 示例运行（2026-10-18 实测，`python agent.py` 默认参数）

```
[source=n8n mode=local] SDR Demo Agent

============================================================
  STEP: 1/4 DISCOVER — 按 ICP 找候选 leads
============================================================
找到 5 条在售摘要（免费浏览，不含 contact）：
  [1] ld-n8n-2026-10-18-421323 | qa=89.5 | $2.50 | 2h
      [HIRING] n8n + Looker Studio builder for marketing reporting
  [2] ld-n8n-2026-10-18-3c2993 | qa=84.5 | $1.50 | 2h
      Technical Account Manager / Customer Success Manager w/heavy
  [3] ld-n8n-2026-10-18-4483b1 | qa=84.5 | $1.50 | 2h
      Same-day n8n workflow rescue - $50 fixed for one bounded bug
  [4] ld-n8n-2026-10-18-58ff63 | qa=84.5 | $1.50 | 2h
      N8n Builder
  [5] ld-n8n-2026-10-18-c1b2f0 | qa=84.5 | $1.50 | 2h
      AI Automation Engineer open to freelance and remote opportun

选品规则命中 1 条（qa>80 且 72h 内），预计总价 $2.50

============================================================
  STEP: 2/4 BUY — x402 购买
============================================================
支付成功（MOCK）：tx=MOCK-TX-62044E5ECE3A，价格 $2.5
拿到完整 lead（含 contact）：{"type": "post", "value": "https://community.n8n.io/t/...", "note": "合规：仅帖链与公司级公开信息；永不把帖内个人联系方式（微信/QQ/个人邮箱/手机号）作为 contact 出售"}
⚠ 本地联调占位，非真实结算

============================================================
  STEP: OUTREACH — 生成个性化外联邮件
============================================================
Subject: Re: [HIRING] n8n + Looker Studio builder for marketing…

Hi,

Saw your public post about this:
"[HIRING] n8n + Looker Studio builder for marketing reporting (Meta Ads, Google Ads, GA4) | Paid test…"
…

I'm Marco, working on AI automation services. I saw you mentioned budget — we've helped similar teams cut delivery cost to ~half.

Open to a 15-min chat about your setup? No pitch if it's not a fit.

Marco

[dry-run] 未发送。加 --live 并配置 SMTP_* 环境变量可真发。

============================================================
  STEP: 4/4 REPORT — 转化回传
============================================================

-- ld-n8n-2026-10-18-421323 → converted
{
  "status": "recorded",
  "lead_id": "ld-n8n-2026-10-18-4213239b",
  "outcome": "converted",
  "reward": "成交回传：已返还 $0.18 credit（成交价的 7%），当前余额 $0.18，下次购买可抵扣。"
}

成交回传触发 7% credit 返还——这就是驱动复购的数据飞轮。

============================================================
  DONE：discover → buy → outreach → report 全链跑通
============================================================
```

## License

MIT — Built on [Leads Plaza](https://github.com/liangh2020/leads-plaza-mcp-server).
