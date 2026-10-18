#!/usr/bin/env python3
"""SDR Demo Agent — 5 分钟启动一个会自动找客户、写外联邮件的开源 SDR Agent，
内置按需购买验证 leads 的能力。

跑通完整闭环：discover（找 leads）→ buy（x402 购买）
→ outreach（个性化外联）→ report（转化回传）。

默认零配置全 mock，一条命令跑完：
    python agent.py

飞轮演示（回传驱动复购）：
    python agent.py --demo-flywheel

数据源：默认 n8n 英文招聘意向池（29 条已上架）；
--source v2ex 可切回 MCP server 的 V2EX 中文池。
"""
import argparse
import asyncio
import json
import os
import re
import smtplib
import sys
import uuid
from datetime import datetime, timezone
from email.message import EmailMessage

AGENT_DIR = os.path.dirname(os.path.abspath(__file__))
# Leads Plaza MCP server 位置（--source v2ex / --mode mcp 时用）。
# 也可以：export LEADS_MCP_SERVER_DIR=/path/to/leads-plaza-mcp-server
MCP_SERVER_DIR = os.environ.get(
    "LEADS_MCP_SERVER_DIR",
    os.path.join(os.path.dirname(AGENT_DIR), "mcp_server"),
)

LEADS_MCP_REPO = "https://github.com/liangh2020/leads-plaza-mcp-server"
N8N_POOL = os.path.normpath(os.path.join(
    AGENT_DIR, "..", "data", "qa",
    "scored_n8n_pilot_2026-10-18.jsonl",
))

# --live 护栏：单次运行最多真发 20 封（见 README 反垃圾声明）
MAX_LIVE_PER_DAY = 20


# ================= 合规：大厂 blocklist =================

# 超出 ICP（SMB 招聘/外包意向）的大厂，永不上架。discover 环节过滤。
BLOCKLIST = [
    "蚂蚁集团", "ant group", "antgroup",
    "阿里", "alibaba",
    "腾讯", "tencent",
    "字节", "bytedance", "tiktok",
    "百度", "baidu",
    "京东", "jd.com", "jingdong",
    "美团", "meituan",
    "拼多多", "pinduoduo",
    "华为", "huawei",
    "小米", "xiaomi",
    "网易", "netease",
    "滴滴", "didi",
]


def is_blocklisted(title: str, summary: str = "") -> bool:
    text = f"{title} {summary}".lower()
    return any(b.lower() in text for b in BLOCKLIST)


# ================= Leads 客户端抽象 =================

class LeadsClient:
    """对 leads 数据源的调用封装。想接自己的 leads API：继承此类，
    实现 search_leads / buy_lead / report_conversion 即可（见 README）。"""

    def search_leads(self, category="", limit=5) -> dict:
        raise NotImplementedError

    def buy_lead(self, lead_id: str, mock: bool = True) -> dict:
        raise NotImplementedError

    def report_conversion(self, lead_id: str, outcome: str, reason: str = "") -> dict:
        raise NotImplementedError


def _price_tier(qa_score: float) -> float:
    """demo 定价（明码标价，见 README）：按质检分三档。"""
    if qa_score >= 85:
        return 2.50
    if qa_score >= 75:
        return 1.50
    return 0.80


def _age_hours(captured_at: str) -> int:
    try:
        cap = datetime.fromisoformat(captured_at)
    except Exception:
        return 9999
    if cap.tzinfo is None:
        cap = cap.replace(tzinfo=timezone.utc)
    return max(0, int((datetime.now(timezone.utc) - cap).total_seconds() // 3600))


class N8NFileClient(LeadsClient):
    """默认数据源：n8n 英文招聘意向池（pilot 29 条已上架）。
    mock 记账在本 demo 内完成（余额/订单/返利/赔付）。"""

    def __init__(self, pool_path: str = N8N_POOL):
        self.balance = 0.0   # credit 余额（USD）
        self.orders = []     # mock 订单
        self.leads = []
        with open(pool_path, encoding="utf-8") as f:
            for line in f:
                try:
                    r = json.loads(line)
                except Exception:
                    continue
                if not r.get("_listed"):
                    continue
                self.leads.append({
                    "id": r.get("_id"),
                    "title": r.get("title", ""),
                    "source_url": r.get("source_url", ""),
                    "summary": r.get("summary", ""),
                    "qa_score": r.get("_qa", 0),
                    "age_hours": _age_hours(r.get("captured_at", "")),
                    "price_usd": _price_tier(r.get("_qa", 0)),
                    "category": "hiring_intent",
                })

    def search_leads(self, category="", limit=5) -> dict:
        blocked = 0
        out = []
        for l in sorted(self.leads, key=lambda x: x["qa_score"], reverse=True):
            if is_blocklisted(l["title"], l["summary"]):
                blocked += 1
                continue
            out.append({k: l[k] for k in
                        ("id", "title", "source_url", "qa_score",
                         "age_hours", "price_usd", "category")})
            if len(out) >= max(1, limit):
                break
        return {"total": len(out), "blocked_bigtech": blocked,
                "note": "摘要免费浏览；contact 等完整字段需 buy_lead 支付后获取",
                "leads": out}

    def buy_lead(self, lead_id: str, mock: bool = True) -> dict:
        lead = next((l for l in self.leads if l["id"] == lead_id), None)
        if not lead:
            return {"status": "not_found", "lead_id": lead_id}
        if not mock:
            return {"status": "needs_mcp",
                    "message": "真实 x402 402 流程请用 --mode mcp（走 MCP server 的 x402 链路）"}
        tx = f"MOCK-TX-{uuid.uuid4().hex[:12].upper()}"
        self.orders.append({"lead_id": lead_id, "price_usd": lead["price_usd"],
                            "tx": tx})
        full = dict(lead)
        # ---- 合规承诺（见 README"合规承诺"）----
        # contact 永不含帖内个人联系方式（微信/QQ/个人邮箱/手机号）；
        # 只含帖链与公司级公开信息。买家 agent 自行去帖子页联系。
        full["contact"] = {
            "type": "post",
            "value": lead["source_url"],
            "note": "合规：仅帖链与公司级公开信息；"
                    "永不把帖内个人联系方式（微信/QQ/个人邮箱/手机号）"
                    "作为 contact 出售",
        }
        full["payment"] = {"mode": "MOCK", "tx_hash": tx,
                           "warning": "本地联调占位，非真实结算"}
        return {"status": "paid_mock", "lead": full}

    def report_conversion(self, lead_id: str, outcome: str, reason: str = "") -> dict:
        order = next((o for o in self.orders if o["lead_id"] == lead_id), None)
        price = order["price_usd"] if order else 0.0
        if outcome == "converted":
            reward = round(price * 0.07, 2)
            self.balance = round(self.balance + reward, 2)
            return {"status": "recorded", "lead_id": lead_id,
                    "outcome": outcome,
                    "reward": f"成交回传：已返还 ${reward:.2f} credit"
                              f"（成交价的 7%），当前余额 ${self.balance:.2f}，"
                              f"下次购买可抵扣。"}
        if outcome == "bad_lead":
            self.balance = round(self.balance + price, 2)
            return {"status": "recorded", "lead_id": lead_id,
                    "outcome": outcome, "reason": reason,
                    "refund": f"坏单赔付：${price:.2f} credit 已退回余额，"
                              f"当前余额 ${self.balance:.2f}。"}
        return {"status": "recorded", "lead_id": lead_id, "outcome": outcome}


class LocalClient(LeadsClient):
    """--source v2ex：直调本地 MCP server 的业务逻辑（V2EX 中文池）。"""

    def __init__(self, server_dir: str = MCP_SERVER_DIR):
        if server_dir not in sys.path:
            sys.path.insert(0, server_dir)
        try:
            import server as _srv
        except ImportError as e:
            raise SystemExit(
                f"找不到 MCP server（{server_dir}）：{e}\n"
                f"  1) 把本仓库 clone 到 leads-plaza/ 下与 mcp_server 同级，或\n"
                f"  2) export LEADS_MCP_SERVER_DIR=/path/to/leads-plaza-mcp-server，或\n"
                f"  3) 用 --mode mcp 走真实 MCP stdio 协议（见 README）。"
            )
        _srv.init_db()
        self._srv = _srv

    def search_leads(self, category="", limit=5) -> dict:
        return self._srv.search_leads_impl(category=category, limit=limit)

    def buy_lead(self, lead_id: str, mock: bool = True) -> dict:
        return self._srv.buy_lead_impl(lead_id, mock_confirm=mock)

    def report_conversion(self, lead_id: str, outcome: str, reason: str = "") -> dict:
        return self._srv.report_conversion_impl(lead_id, outcome, reason)


class MCPStdioClient(LeadsClient):
    """--mode mcp：走真实 MCP stdio 协议调远端 MCP server。
    需要 pip install -r requirements.txt（mcp 包）。"""

    def __init__(self, server_dir: str = MCP_SERVER_DIR):
        self.server_dir = server_dir
        try:
            from mcp import ClientSession, StdioServerParameters  # noqa
            from mcp.client.stdio import stdio_client  # noqa
        except ImportError:
            raise SystemExit("mcp 包未安装：pip install -r requirements.txt")

    def _call(self, tool: str, args: dict):
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        async def _run():
            params = StdioServerParameters(
                command=sys.executable,
                args=["server.py"],
                cwd=self.server_dir,
                env={**os.environ},
            )
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    r = await session.call_tool(tool, args)
                    return json.loads(r.content[0].text)

        return asyncio.run(_run())

    def search_leads(self, category="", limit=5) -> dict:
        return self._call("search_leads", {"category": category, "limit": limit})

    def buy_lead(self, lead_id: str, mock: bool = True) -> dict:
        return self._call("buy_lead", {"lead_id": lead_id, "mock_confirm": mock})

    def report_conversion(self, lead_id: str, outcome: str, reason: str = "") -> dict:
        return self._call("report_conversion",
                          {"lead_id": lead_id, "outcome": outcome, "reason": reason})


# ================= 选品规则 =================

def select_leads(leads: list, budget: float | None = None,
                 limit: int = 5) -> tuple[list, float]:
    """示例选品规则（"Agent"二字的最低门槛，见 README）：
    qa>80 且 age_hours<72 且单价在预算内；按 qa 降序取。
    返回 (选中的 leads, 预计总价)。"""
    picked, spent = [], 0.0
    for l in sorted(leads, key=lambda x: x.get("qa_score", 0), reverse=True):
        if not (l.get("qa_score", 0) > 80 and l.get("age_hours", 9999) < 72):
            continue
        price = l.get("price_usd") or 0
        if budget is not None and spent + price > budget:
            print(f"  跳过 {l['id'][:24]}：${price:.2f} 超出剩余预算 "
                  f"${budget - spent:.2f}")
            continue
        picked.append(l)
        spent += price
        if len(picked) >= max(1, limit):
            break
    return picked, round(spent, 2)


# ================= 外联邮件 =================

def _clean_snippet(text: str, n: int = 150) -> str:
    t = re.sub(r"\s+", " ", text or "").strip()
    return t[:n] + ("…" if len(t) > n else "")


def _has_cjk(text: str) -> bool:
    return bool(re.search(r"[\u4e00-\u9fff]", text or ""))


def _angle_zh(text: str) -> str:
    if re.search(r"预算|报价|多少钱|价格|费用", text):
        return "看到你提到了预算，我们之前帮类似需求把交付成本压到原来的一半左右。"
    if re.search(r"急|asap|尽快|今天|明天|urgent", text, re.I):
        return "看到时间比较紧，我们可以先快速对齐需求，48 小时内出初步方案。"
    return "不知道你这边是否已在对接供应商，我们可以先免费帮你梳理一版需求清单。"


def _angle_en(text: str) -> str:
    if re.search(r"budget|\$\s?[\d,]+|per hour|fixed", text, re.I):
        return ("I saw you mentioned budget — we've helped similar teams "
                "cut delivery cost to ~half.")
    if re.search(r"urgent|asap|same[ -]?day", text, re.I):
        return ("Timing looks tight — we can align on requirements fast "
                "and share a draft plan within 48h.")
    return ("Not sure if you're already talking to vendors — happy to put "
            "together a free requirements checklist first.")


def build_outreach_email(lead: dict, sender: str, product: str) -> tuple[str, str]:
    """基于 lead 数据生成个性化外联邮件。返回 (subject, body)。
    按 lead 语言整封统一（英文 lead 全英文、中文 lead 全中文）；
    90% 讲对方的问题/场景，10% 留 CTA（去推广味）。"""
    title = lead.get("title", "")
    summary = lead.get("summary", "") or ""
    source = lead.get("source_url", "")
    text = f"{title} {summary}"

    if _has_cjk(title):
        subject = f"Re: {_clean_snippet(title, 30)}"
        body = (
            f"你好，\n\n"
            f"看到你在公开渠道留下这个需求：\n"
            f"「{_clean_snippet(title, 80)}」\n"
        )
        if summary and summary.strip() != title.strip():
            body += f"{_clean_snippet(summary)}\n"
        body += (
            f"（来源：{source}）\n\n"
            f"我是 {sender}（{product}）。{_angle_zh(text)}\n\n"
            f"方便约 15 分钟聊下你的具体场景吗？合适就深入，不合适就当交个朋友。\n\n"
            f"{sender}"
        )
    else:
        subject = f"Re: {_clean_snippet(title, 50)}"
        body = (
            f"Hi,\n\n"
            f"Saw your public post about this:\n"
            f"\"{_clean_snippet(title, 100)}\"\n"
        )
        if summary and summary.strip() != title.strip():
            body += f"{_clean_snippet(summary)}\n"
        body += (
            f"(source: {source})\n\n"
            f"I'm {sender}, working on {product}. {_angle_en(text)}\n\n"
            f"Open to a 15-min chat about your setup? No pitch if it's not a fit.\n\n"
            f"{sender}"
        )
    return subject, body


def send_email_smtp(to_addr: str, subject: str, body: str) -> None:
    host = os.environ.get("SMTP_HOST")
    if not host:
        raise SystemExit(
            "live 模式需要 SMTP 配置：export SMTP_HOST / SMTP_PORT(默认587) / "
            "SMTP_USER / SMTP_PASS / SMTP_FROM 后再跑 --live"
        )
    port = int(os.environ.get("SMTP_PORT", "587"))
    user = os.environ.get("SMTP_USER", "")
    password = os.environ.get("SMTP_PASS", "")
    from_addr = os.environ.get("SMTP_FROM", user)
    msg = EmailMessage()
    msg["From"] = from_addr
    msg["To"] = to_addr
    msg["Subject"] = subject
    msg.set_content(body)
    with smtplib.SMTP(host, port) as s:
        s.starttls()
        if user:
            s.login(user, password)
        s.send_message(msg)


# ================= 主流程 =================

def step(title: str):
    print(f"\n{'=' * 60}\n  STEP: {title}\n{'=' * 60}")


def make_client(args) -> LeadsClient:
    if args.mode == "mcp":
        return MCPStdioClient()
    if args.source == "v2ex":
        return LocalClient()
    return N8NFileClient()


def do_buy(client: LeadsClient, lead: dict, mock: bool = True) -> dict | None:
    bought = client.buy_lead(lead["id"], mock=mock)
    if bought.get("status") == "needs_mcp":
        print(bought["message"])
        return None
    if bought.get("status") != "paid_mock":
        print("购买失败：", json.dumps(bought, ensure_ascii=False)[:500])
        return None
    full = bought["lead"]
    pay = full.get("payment", {})
    print(f"支付成功（MOCK）：tx={pay.get('tx_hash')}，价格 ${full.get('price_usd')}")
    print(f"拿到完整 lead（含 contact）："
          f"{json.dumps(full.get('contact'), ensure_ascii=False)}")
    print(f"⚠ {pay.get('warning', '')}")
    return full


def do_outreach(full: dict, args, live_count: int = 0) -> int:
    step("OUTREACH — 生成个性化外联邮件")
    subject, body = build_outreach_email(full, args.sender, args.product)
    print(f"Subject: {subject}\n\n{body}")
    contact = full.get("contact") or {}
    to_addr = args.to or (contact.get("value")
                          if "@" in str(contact.get("value", "")) else "")
    if args.live:
        if live_count >= MAX_LIVE_PER_DAY:
            print(f"\n--live 护栏：单次运行已达上限 {MAX_LIVE_PER_DAY} 封，停止。")
            return live_count
        if not to_addr:
            print("\n--live 但 lead 里没有邮箱地址，用 --to 指定收件人。")
            return live_count
        print("\n[合规提醒] 真实发送请遵守目标地区反垃圾法规（如 CAN-SPAM / GDPR），"
              "并确保收件人可退订。")
        send_email_smtp(to_addr, subject, body)
        print(f"\n已发送 → {to_addr}")
        return live_count + 1
    print("\n[dry-run] 未发送。加 --live 并配置 SMTP_* 环境变量可真发。")
    return live_count


def do_report(client: LeadsClient, lead_id: str, outcome: str,
              reason: str = "") -> dict:
    rep = client.report_conversion(lead_id, outcome, reason)
    print(json.dumps(rep, ensure_ascii=False, indent=2))
    if outcome == "converted" and "reward" in rep:
        print("\n成交回传触发 7% credit 返还——这就是驱动复购的数据飞轮。")
    elif outcome == "bad_lead" and "refund" in rep:
        print("\n坏单敢赔：credit 已退回余额——信任比一单的钱值钱。")
    return rep


def run_flywheel(client: LeadsClient, args) -> int:
    """--demo-flywheel：试单 → 回传 → 复购，一圈演完。"""
    step("FLYWHEEL 1/2 — 试单：买 3 条")
    res = client.search_leads(limit=10)
    picked, est = select_leads(res.get("leads", []), limit=3)
    if len(picked) < 3:
        print(f"规则内只有 {len(picked)} 条符合（qa>80 且 72h 内），"
              "飞轮演示需要 ≥3 条。")
        return 1
    print(f"选中 3 条，预计总价 ${est:.2f}：")
    for l in picked:
        print(f"  {l['id'][:24]} | qa={l['qa_score']} | "
              f"${l['price_usd']:.2f} | {l['age_hours']}h | {l['title'][:50]}")
    bought = [do_buy(client, l) for l in picked]
    bought = [b for b in bought if b]
    if not bought:
        return 1

    step("FLYWHEEL 2/2 — 回传 1 条 converted → 返利入账")
    first = bought[0]
    do_report(client, first["id"], "converted")
    balance = getattr(client, "balance", 0.0)
    print(f"\n当前 credit 余额：${balance:.2f}")

    step("FLYWHEEL 3/2 — 复购：用余额抵扣再买 1 条")
    res2 = client.search_leads(limit=10)
    bought_ids = {b["id"] for b in bought}
    rest = [l for l in res2.get("leads", []) if l["id"] not in bought_ids]
    picked2, _ = select_leads(rest, limit=1)
    if not picked2:
        print("没有更多符合规则的 lead，复购演示到此为止。")
        return 0
    nxt = picked2[0]
    price = nxt.get("price_usd") or 0
    offset = min(balance, price)
    real_pay = round(price - offset, 2)
    print(f"目标：{nxt['id'][:24]} | qa={nxt['qa_score']} | 原价 ${price:.2f}")
    print(f"余额抵扣 ${offset:.2f} → 实付 ${real_pay:.2f}")
    full2 = do_buy(client, nxt)
    if full2:
        if hasattr(client, "balance"):
            client.balance = round(client.balance - offset, 2)
        do_outreach(full2, args)
        do_report(client, nxt["id"], "no_response")
    print(f"\n{'=' * 60}\n  飞轮转完一圈：试单 → 回传 → 返利 → 余额抵扣复购\n"
          f"  回传数据驱动复购——这就是 Leads Plaza 的核心飞轮。\n{'=' * 60}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="SDR Demo Agent — 5 分钟自动找客户")
    ap.add_argument("--icp", default=None,
                    help="意向类目（默认：n8n 源用 hiring_intent，v2ex 源用 ai_automation_intent）")
    ap.add_argument("--limit", type=int, default=5, help="discover 取几条")
    ap.add_argument("--source", choices=["n8n", "v2ex"], default="n8n",
                    help="n8n=英文招聘意向池（默认）；v2ex=MCP server 的 V2EX 中文池")
    ap.add_argument("--mode", choices=["local", "mcp"], default="local",
                    help="local=本地数据源（默认）；mcp=走真实 MCP stdio 协议")
    ap.add_argument("--lead-id", default="", help="指定购买的 lead id（默认按规则选）")
    ap.add_argument("--pay", choices=["mock", "testnet"], default="mock",
                    help="mock=本地模拟支付（默认）；testnet=真实 x402 流程（需 --mode mcp）")
    ap.add_argument("--budget", type=float, default=None,
                    help="批量预算（USD）：预算内按规则买前 N 条，如 --budget 5")
    ap.add_argument("--demo-flywheel", action="store_true",
                    help="演示完整飞轮：试单 3 条 → 回传 converted → 返利 → 余额抵扣复购")
    ap.add_argument("--live", action="store_true",
                    help="真的发送外联邮件（需 SMTP_* 环境变量；默认 dry-run 只打印）")
    ap.add_argument("--to", default="", help="外联收件人（默认从 lead contact 猜，猜不到则只打印）")
    ap.add_argument("--outcome", choices=["converted", "no_response", "bad_lead"],
                    default="converted", help="回传结果（默认 converted，演示 7%% credit 返还）")
    ap.add_argument("--reason", default="", help="回传备注（bad_lead 时建议填写原因）")
    ap.add_argument("--sender", default="Marco", help="外联邮件署名")
    ap.add_argument("--product", default="AI automation services",
                    help="一句话产品描述（fork 后改成你自己的）")
    args = ap.parse_args()

    client = make_client(args)
    eff_source = "mcp-server" if args.mode == "mcp" else args.source
    icp = args.icp or ("hiring_intent" if eff_source == "n8n" else "ai_automation_intent")
    print(f"[source={eff_source} mode={args.mode}] SDR Demo Agent")

    if args.demo_flywheel:
        if args.source != "n8n" or args.mode != "local":
            print("--demo-flywheel 目前只支持默认本地 n8n 池（mock 记账）。")
            return 1
        return run_flywheel(client, args)

    # ---- 1. discover ----
    step("1/4 DISCOVER — 按 ICP 找候选 leads")
    res = client.search_leads(category=icp, limit=args.limit)
    leads = res.get("leads", [])
    blocked = res.get("blocked_bigtech", 0)
    print(f"找到 {res.get('total', 0)} 条在售摘要（免费浏览，不含 contact）"
          f"{f'；已过滤大厂 {blocked} 条' if blocked else ''}：")
    for i, l in enumerate(leads, 1):
        age = l.get("age_hours", "?")
        print(f"  [{i}] {l['id'][:24]} | qa={l['qa_score']} "
              f"| ${l.get('price_usd', 0):.2f} | {age}h")
        print(f"      {l['title'][:60]}")
    if not leads:
        print("没有符合的 lead，换个 --icp 试试。")
        return 1

    # ---- 选品（规则，见 README）----
    if args.lead_id:
        picked = [l for l in leads if l["id"] == args.lead_id][:1]
        est = picked[0].get("price_usd", 0) if picked else 0
    else:
        n = args.limit if args.budget is not None else 1
        picked, est = select_leads(leads, budget=args.budget, limit=n)
    if not picked:
        print("规则内（qa>80 且 72h 内且预算内）没有符合的 lead。")
        if any(l.get("age_hours") is None for l in leads):
            print("注：该数据源部分 lead 缺少 age 数据，时效过滤将其排除；"
                  "默认 n8n 池自带 age，可直接跑。")
        return 1
    print(f"\n选品规则命中 {len(picked)} 条（qa>80 且 72h 内"
          f"{f'且总价 ≤${args.budget:.2f}' if args.budget else ''}），"
          f"预计总价 ${est:.2f}")

    # ---- 2. buy ----
    step("2/4 BUY — x402 购买" + (f"（批量 {len(picked)} 条）" if len(picked) > 1 else ""))
    if args.pay == "testnet":
        resp = client.buy_lead(picked[0]["id"], mock=False)
        if resp.get("status") == "needs_mcp":
            print(resp["message"])
            return 0
        print(json.dumps(resp, ensure_ascii=False, indent=2)[:1500])
        print("\n真实 x402 流程：用钱包按 paymentRequirements 做 EIP-3009 签名，"
              "带 X-PAYMENT header 重试 buy_lead → facilitator verify/settle → 拿完整 lead。"
              "\ndemo 不持有私钥，到此止步。切回 --pay mock 可跑完闭环。")
        return 0
    bought_fulls = []
    for l in picked:
        full = do_buy(client, l)
        if full:
            bought_fulls.append(full)
    if not bought_fulls:
        return 1

    # ---- 3. outreach ----
    live_count = 0
    for full in bought_fulls:
        live_count = do_outreach(full, args, live_count)

    # ---- 4. report ----
    step("4/4 REPORT — 转化回传")
    for i, full in enumerate(bought_fulls):
        oc = args.outcome if i == 0 or len(bought_fulls) == 1 else "no_response"
        print(f"\n-- {full['id'][:24]} → {oc}")
        do_report(client, full["id"], oc, args.reason if oc == "bad_lead" else "")

    print(f"\n{'=' * 60}\n  DONE：discover → buy → outreach → report 全链跑通\n{'=' * 60}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
