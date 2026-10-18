#!/usr/bin/env python3
"""SDR Demo Agent — Leads Plaza 黄金路径演示。

跑通完整闭环：discover（找 leads）→ buy（x402 购买）
→ outreach（个性化外联）→ report（转化回传）。

默认零配置全 mock，一条命令跑完：
    python agent.py

这是开源示例：README 里硬编码了 Leads Plaza 的端点，
每个 fork/clone 都是分销渠道。想接自己的 leads API 见 README。
"""
import argparse
import asyncio
import json
import os
import re
import smtplib
import sys
from email.message import EmailMessage

AGENT_DIR = os.path.dirname(os.path.abspath(__file__))
# Leads Plaza MCP server 位置（默认指向我们开源的 MCP server 仓库）。
# 也可以：export LEADS_MCP_SERVER_DIR=/path/to/leads-plaza-mcp-server
MCP_SERVER_DIR = os.environ.get(
    "LEADS_MCP_SERVER_DIR",
    os.path.join(os.path.dirname(AGENT_DIR), "mcp_server"),
)

LEADS_MCP_REPO = "https://github.com/liangh2020/leads-plaza-mcp-server"


# ================= Leads 客户端抽象 =================

class LeadsClient:
    """对 Leads Plaza 的调用封装。想接自己的 leads API：继承此类，
    实现 search_leads / buy_lead / report_conversion 即可（见 README）。"""

    def search_leads(self, category="", limit=5) -> dict:
        raise NotImplementedError

    def buy_lead(self, lead_id: str, mock: bool = True) -> dict:
        raise NotImplementedError

    def report_conversion(self, lead_id: str, outcome: str, reason: str = "") -> dict:
        raise NotImplementedError


class LocalClient(LeadsClient):
    """直调本地 MCP server 的业务逻辑（等价于走 MCP tool）。
    默认模式：零配置，5 分钟跑通。"""

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
    """走真实 MCP stdio 协议调远端 MCP server（演示真实端点）。
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


# ================= 外联邮件 =================

def _clean_snippet(text: str, n: int = 150) -> str:
    t = re.sub(r"\s+", " ", text or "").strip()
    return t[:n] + ("……" if len(t) > n else "")


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
            f"我是 {sender}，在做 {product}。{_angle_zh(text)}\n\n"
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


def _has_cjk(text: str) -> bool:
    return bool(re.search(r"[\u4e00-\u9fff]", text or ""))


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


def main() -> int:
    ap = argparse.ArgumentParser(description="SDR Demo Agent — Leads Plaza 黄金路径")
    ap.add_argument("--icp", default="ai_automation_intent",
                    help="意向类目（默认 ai_automation_intent）")
    ap.add_argument("--limit", type=int, default=5, help="discover 取几条")
    ap.add_argument("--mode", choices=["local", "mcp"], default="local",
                    help="local=直调本地 MCP server 逻辑（默认）；mcp=走真实 MCP stdio 协议")
    ap.add_argument("--lead-id", default="", help="指定购买的 lead id（默认取 qa 最高）")
    ap.add_argument("--pay", choices=["mock", "testnet"], default="mock",
                    help="mock=本地模拟支付（默认）；testnet=展示真实 x402 402 流程（需钱包签名，demo 到此止步）")
    ap.add_argument("--live", action="store_true",
                    help="真的发送外联邮件（需 SMTP_* 环境变量；默认 dry-run 只打印）")
    ap.add_argument("--to", default="", help="外联收件人（默认从 lead contact 猜，猜不到则只打印）")
    ap.add_argument("--outcome", choices=["converted", "no_response", "bad_lead"],
                    default="converted", help="回传结果（默认 converted，演示 7%% credit 返还）")
    ap.add_argument("--reason", default="", help="回传备注（bad_lead 时建议填写原因）")
    ap.add_argument("--sender", default="Alex", help="外联邮件署名")
    ap.add_argument("--product", default="an AI automation SDR service",
                    help="一句话产品描述（fork 后改成你自己的）")
    args = ap.parse_args()

    client = MCPStdioClient() if args.mode == "mcp" else LocalClient()
    print(f"[mode={args.mode}] SDR Demo Agent → Leads Plaza ({LEADS_MCP_REPO})")

    # ---- 1. discover ----
    step("1/4 DISCOVER — 按 ICP 找候选 leads")
    res = client.search_leads(category=args.icp, limit=args.limit)
    leads = res.get("leads", [])
    print(f"找到 {res.get('total', 0)} 条在售摘要（免费浏览，不含 contact）：")
    for i, l in enumerate(leads, 1):
        print(f"  [{i}] {l['id']} | qa={l['qa_score']} "
              f"| ${l['price_usd']} | 新鲜度={l['freshness']}")
        print(f"      {l['title'][:60]}")
    if not leads:
        print("没有符合的 lead，换个 --icp 试试。")
        return 1

    lead_id = args.lead_id or max(leads, key=lambda l: l["qa_score"])["id"]
    print(f"\n选中购买：{lead_id}（qa 最高；可用 --lead-id 指定）")

    # ---- 2. buy ----
    step("2/4 BUY — x402 购买")
    if args.pay == "testnet":
        resp = client.buy_lead(lead_id, mock=False)
        print(json.dumps(resp, ensure_ascii=False, indent=2)[:1500])
        print("\n真实 x402 流程：用钱包按 paymentRequirements 做 EIP-3009 签名，"
              "带 X-PAYMENT header 重试 buy_lead → facilitator verify/settle → 拿完整 lead。"
              "\ndemo 不持有私钥，到此止步。切回 --pay mock 可跑完闭环。")
        return 0
    bought = client.buy_lead(lead_id, mock=True)
    if bought.get("status") != "paid_mock":
        print("购买失败：", json.dumps(bought, ensure_ascii=False)[:500])
        return 1
    lead = bought["lead"]
    pay = lead.get("payment", {})
    print(f"支付成功（MOCK）：tx={pay.get('tx_hash')}，价格 ${lead.get('price_usd')}")
    print(f"拿到完整 lead（含 contact）：{json.dumps(lead.get('contact'), ensure_ascii=False)}")
    print(f"⚠ {pay.get('warning', '')}")

    # ---- 3. outreach ----
    step("3/4 OUTREACH — 生成个性化外联邮件")
    subject, body = build_outreach_email(lead, args.sender, args.product)
    print(f"Subject: {subject}\n\n{body}")
    contact = lead.get("contact") or {}
    to_addr = args.to or (contact.get("value") if "@" in str(contact.get("value", "")) else "")
    if args.live:
        if not to_addr:
            print("\n--live 但 lead 里没有邮箱地址，用 --to 指定收件人。")
            return 1
        send_email_smtp(to_addr, subject, body)
        print(f"\n已发送 → {to_addr}")
    else:
        print("\n[dry-run] 未发送。加 --live 并配置 SMTP_* 环境变量可真发。")

    # ---- 4. report ----
    step("4/4 REPORT — 转化回传")
    rep = client.report_conversion(lead_id, args.outcome, args.reason)
    print(json.dumps(rep, ensure_ascii=False, indent=2))
    if args.outcome == "converted" and "reward" in rep:
        print("\n成交回传触发 7% credit 返还——这就是驱动动态定价的数据飞轮。")
    elif args.outcome == "bad_lead":
        print("\n坏单走 SLA：24h 内自动退 credit 或换一条同类目等值 lead。")

    print(f"\n{'=' * 60}\n  DONE：discover → buy → outreach → report 全链跑通\n"
          f"  把这套循环交给真正的 SDR agent，就是 Leads Plaza 的分销网络。\n{'=' * 60}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
