"""Terminal-only entry for writing system-scope documents into the AI knowledge base.

The HTTP upload API only accepts user-scope files, so system documents
(official standards, internal reports, ...) must be ingested from the terminal.

Usage:
    # List existing system documents
    python kb_ingest_system.py list

    # Ingest one document (title is required)
    python kb_ingest_system.py 空气质量标准.pdf --title "环境空气质量指数技术规定" \
        --category air_quality_standard --authority official --topics AQHI,标准

Run with the backend virtualenv python, e.g.:
    /Users/chenweiyang/.workbuddy/binaries/python/envs/default/bin/python kb_ingest_system.py ...
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ai_service.knowledge.config import KnowledgeConfig
from ai_service.knowledge.manager import KnowledgeBaseManager
from ai_service.knowledge.models import (
    AUTHORITY_WHITELIST,
    CATEGORY_WHITELIST,
    KnowledgePrincipal,
)

# 终端本机身份：平台维护者，允许写 system 范围
MAINTAINER = KnowledgePrincipal(
    principal_id="platform",
    roles=("knowledge_admin",),
    allowed_scopes=("system", "user"),
    local_development=True,
)


def cmd_list(manager: KnowledgeBaseManager) -> int:
    docs = manager.list_documents(principal=MAINTAINER, scope="system")
    if not docs:
        print("系统知识库目前为空。")
        return 0
    print(f"系统知识库共 {len(docs)} 篇文档：")
    for doc in docs:
        print(f"  [{doc.status}] {doc.title}  (id={doc.document_id}, chunks={doc.chunk_count})")
    return 0


def cmd_add(manager: KnowledgeBaseManager, args: argparse.Namespace) -> int:
    path = Path(args.file).expanduser().resolve()
    if not path.is_file():
        print(f"找不到文件: {path}")
        return 1

    # 元数据校验前置，失败直接给出可选值
    if args.category not in CATEGORY_WHITELIST:
        print(f"category 不合法，可选: {', '.join(sorted(CATEGORY_WHITELIST))}")
        return 1
    if args.authority not in AUTHORITY_WHITELIST:
        print(f"authority 不合法，可选: {', '.join(sorted(AUTHORITY_WHITELIST))}")
        return 1

    metadata = {
        "title": args.title,
        "source": args.source,
        "author": args.author,
        "year": args.year,
        "category": args.category,
        "topics": [t for t in args.topics.split(",") if t.strip()],
        "scope": "system",
        "authority_level": args.authority,
    }

    print(f"写入系统知识库: {path.name}")
    print("(首次运行会下载嵌入模型，可能需要几分钟)")
    document = manager.ingest_bytes(
        path.read_bytes(), path.name, metadata, principal=MAINTAINER
    )
    print(f"  标题:   {document.title}")
    print(f"  状态:   {document.status}")
    print(f"  分块数: {document.chunk_count}")
    print(f"  文档ID: {document.document_id}")
    if document.status == "requires_ocr":
        print("  ⚠️ 该文档疑似扫描版 PDF，需要 OCR 后再写入。")
        return 1
    if document.status == "failed":
        print(f"  ❌ 索引失败: {document.error_message}")
        return 1
    print("✅ 已写入系统知识库（scope=system），所有会话均可检索。")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="向系统知识库写入/查看文档")
    parser.add_argument("file", nargs="?", help="要写入的文档 (pdf/docx/txt/md)，或使用 list 查看现状")
    parser.add_argument("--title", help="文献题名（引用必需）")
    parser.add_argument("--category", default="other", help=f"分类，默认 other，可选: {', '.join(sorted(CATEGORY_WHITELIST))}")
    parser.add_argument("--authority", default="internal", help=f"权威级别，默认 internal，可选: {', '.join(sorted(AUTHORITY_WHITELIST))}")
    parser.add_argument("--source", default=None, help="来源（如发文机构/链接）")
    parser.add_argument("--author", default=None, help="作者")
    parser.add_argument("--year", type=int, default=None, help="年份")
    parser.add_argument("--topics", default="", help="主题标签，逗号分隔")
    args = parser.parse_args()

    config = KnowledgeConfig.from_env(ROOT)
    print(f"知识库位置: {config.storage_path}")
    manager = KnowledgeBaseManager(config)

    if args.file == "list" or (args.file is None and not args.title):
        return cmd_list(manager)
    if not args.file or not args.title:
        print("写入文档需要同时给出文件和 --title；只看现状请用: kb_ingest_system.py list")
        return 1
    return cmd_add(manager, args)


if __name__ == "__main__":
    sys.exit(main())
