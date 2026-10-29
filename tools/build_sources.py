"""Write data/sources.json: which data routes the site is built from, their
coverage and the credential-free status of the latest connection checks.

Inputs are the private evidence manifest, the provider money-flow directory,
the published attribution metadata and, when present, the sanitized status
written by tools/check_data_sources.py. Credentials never enter the output;
only the names of the secrets and where they are stored are described.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_real_study as study  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
REPOSITORY = "ccarafa/market-drop-attribution-demo"
WORKFLOW = "data-sources-check.yml"


def manifest_coverage(manifest, kind):
    days = sorted(k.split("/")[1] for k in manifest if k.startswith(kind + "/"))
    rows = sum(int(v.get("saved") or 0) for k, v in manifest.items() if k.startswith(kind + "/"))
    complete = all(v.get("complete") for k, v in manifest.items() if k.startswith(kind + "/"))
    fetched = max((v.get("fetched_at") or "" for k, v in manifest.items() if k.startswith(kind + "/")), default=None)
    return {"days": len(days), "first_day": days[0] if days else None, "last_day": days[-1] if days else None, "rows": rows, "complete": complete, "fetched_at": fetched}


def moneyflow_coverage(directory):
    files = sorted(Path(directory).glob("*.json")) if directory and Path(directory).is_dir() else []
    days, rows = [], 0
    for path in files:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("complete"):
            days.append(path.stem)
            rows += len(payload.get("rows") or [])
    stamp = max((datetime.fromtimestamp(p.stat().st_mtime, timezone.utc).isoformat() for p in files), default=None)
    return {"days": len(days), "first_day": days[0] if days else None, "last_day": days[-1] if days else None, "rows": rows, "complete": bool(days), "fetched_at": stamp}


def build(evidence_dir, moneyflow_dir, status_path):
    manifest_path = Path(evidence_dir) / "manifest.json" if evidence_dir else None
    manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path and manifest_path.is_file() else {}
    index = json.loads((ROOT / "data/attribution/index.json").read_text(encoding="utf-8"))
    replay = json.loads((ROOT / "data/replay_evidence.json").read_text(encoding="utf-8"))
    status = json.loads(Path(status_path).read_text(encoding="utf-8")) if status_path and Path(status_path).is_file() else None
    announcements = manifest_coverage(manifest, "announcements")
    news = manifest_coverage(manifest, "news")
    moneyflow = moneyflow_coverage(moneyflow_dir)
    sources = [
        {"id": "announcements", "name": "公告搜索", "api": "search/announcement", "route": "AI-Ready 网关 gw.datayes.com/aladdin_proxy/aladdin_adventure",
         "auth": "Authorization: Bearer（凭证 DATAYES_API_TOKEN）", "billing": "按返回条数计点（约 1,320 点/条），占用月度点数", "limit": "10 次/秒，2,000 次/日",
         "fields": "公告标题、分类、发布时间、股票代码、申万一级行业", "use": "本公司公告、同行业公告、行业归属（归因层 ①②）", "coverage": announcements, "status": "connected"},
        {"id": "news", "name": "公众号文章搜索", "api": "web/news", "route": "AI-Ready 网关 gw.datayes.com/aladdin_proxy/aladdin_adventure",
         "auth": "Authorization: Bearer（凭证 DATAYES_API_TOKEN）", "billing": "免费", "limit": "10 次/秒，2,000 次/日",
         "fields": "文章标题、来源、发布时间、关联股票代码、原文链接", "use": "本股文章、行业与监管报道、宏观政策背景（归因层 ③④）", "coverage": news, "status": "connected"},
        {"id": "moneyflow", "name": "个股资金流向", "api": "market/getMktEquMfNew.json", "route": "数据API商城 api.datayes.com/data/v1",
         "auth": "Authorization: Bearer（凭证 DATAYES_API_TOKEN）", "billing": "商城试用计次，不占用 AI-Ready 月度点数", "limit": "按交易日整市场一次调用（pagesize 10000）",
         "fields": "小单、中单、大单、超大单净流入，成交额，成交笔数", "use": "主力与超大单资金流向特征与展示（归因层 ⑥）", "coverage": moneyflow, "status": "connected"},
        {"id": "daily", "name": "A股日行情", "api": "market/getMktEqud.json", "route": "AI-Ready 网关 gw.datayes.com/aladdin_proxy/data_api",
         "auth": "Authorization: Bearer（凭证 DATAYES_API_TOKEN）", "billing": "50,000 点/行", "limit": "10 次/秒，2,000 次/日",
         "fields": "开高低收、成交量、成交额、涨跌幅", "use": "仅用于连接检查；站点价格来自本地 2026 年 7 月快照档案", "coverage": None, "status": "verified_not_used"},
        {"id": "s3", "name": "S3 文件服务", "api": "bigdata-s3-data.datayes.com", "route": "对象存储（公告、研报正文文件）",
         "auth": "s3-data-auth（DATAYES_S3_TOKEN）", "billing": "受带宽与并发规范约束", "limit": "并发 ≤50，QPS ≤200，大批量安排在 15:30 前或 20:00 后",
         "fields": "公告正文、研报 HTML 等文件", "use": "已验证可取件；站点不展示正文，未使用", "coverage": None, "status": "verified_not_used"},
        {"id": "mcp", "name": "MCP 网关", "api": "dataapi-mcp-server.datayes.com", "route": "37 个子服务器，接口目录与领域搜索",
         "auth": "X-API-KEY（与 S3 Token 相同）", "billing": "目录与搜索免费；取数需另行授权", "limit": "—",
         "fields": "接口定义、参数说明", "use": "接口发现与参数核对；业务取数未授权", "coverage": None, "status": "verified_not_used"},
        {"id": "local", "name": "本地快照档案", "api": "reports/202607_analysis（私有）", "route": "项目自有 2026 年 7 月逐笔快照汇总",
         "auth": "无（本地文件）", "billing": "—", "limit": "—",
         "fields": "末次价格、参考前收、成交量、价差、盘口失衡", "use": "事件定义、市场与行业中位数、成交盘口指标", "coverage": {"days": 23, "first_day": "20260701", "last_day": "20260731", "rows": 119652, "complete": True, "fetched_at": None}, "status": "connected"},
    ]
    available = [
        {"name": "政策法规详情 getPolRegDetailSrc", "reason": "当前套餐返回 403，需要开通后才能作为政策层证据"},
        {"name": "实时快讯 getFlashNews", "reason": "目录中标记锁定，未开放调用"},
        {"name": "沪深实时快照 getSHSZTickRTSnapshot", "reason": "当前账号网关返回 404；盘中实时监测需要该接口或自建行情源"},
        {"name": "逐笔盘口汇总（项目 EC2）", "reason": "主动买卖、跌停封单等五分钟级指标的服务器作业已就绪，等待实例可连接后运行"},
    ]
    document = {
        "schema_version": 1, "generated_at": datetime.now(timezone.utc).isoformat(), "repository": REPOSITORY,
        "links": {
            "secrets": f"https://github.com/{REPOSITORY}/settings/secrets/actions",
            "workflow": f"https://github.com/{REPOSITORY}/actions/workflows/{WORKFLOW}",
            "badge": f"https://github.com/{REPOSITORY}/actions/workflows/{WORKFLOW}/badge.svg",
            "runs": f"https://github.com/{REPOSITORY}/actions",
            "guide": "https://d.datayes.com/guide", "console": "https://mall.datayes.com/dashboard?lang=zh",
        },
        "credentials": [
            {"name": "DATAYES_API_TOKEN", "label": "通联 API 凭证", "obtained": "mall.datayes.com → 我的数据 → 我的凭证",
             "used_by": ["公告搜索", "公众号文章搜索", "个股资金流向", "A股日行情连接检查"],
             "stored_in": ["GitHub 仓库 Secret（供连接检查工作流使用）", "本地构建环境的忽略目录 .runtime/datayes/api.env"]},
            {"name": "DATAYES_S3_TOKEN", "label": "通联 S3 Token", "obtained": "通联客服发放",
             "used_by": ["S3 文件服务", "MCP 网关 X-API-KEY"],
             "stored_in": ["GitHub 仓库 Secret", "本地构建环境的忽略目录 .runtime/datayes/s3.env"]},
        ],
        "rotation": [
            "点击“在 GitHub 更换 Token”，以仓库所有者身份登录后更新对应 Secret 的值；页面本身不保存也不读取密钥。",
            "点击“运行连接检查”，手动触发工作流；它用新 Secret 对每条路线各发一次请求，只保留脱敏状态，结果显示在下方徽章和运行记录中。",
            "本地重建数据时，把同一 Token 更新到 .runtime/datayes/api.env（或 s3.env），再运行构建脚本并发布。",
        ],
        "build": {"attribution_builder_sha256": index["metadata"].get("builder_sha256"), "evidence_counts": index["metadata"].get("evidence_counts"),
                  "moneyflow_rows": index["metadata"].get("moneyflow_rows"), "replay_pack_version": replay["metadata"].get("version")},
        "last_check": status, "sources": sources, "available": available,
    }
    return study.write_json(ROOT / "data" / "sources.json", document)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence", type=Path)
    parser.add_argument("--moneyflow", type=Path)
    parser.add_argument("--status", type=Path, default=ROOT / ".runtime" / "data-sources-status.json")
    args = parser.parse_args()
    print(json.dumps(build(args.evidence, args.moneyflow, args.status), ensure_ascii=False))


if __name__ == "__main__":
    main()
