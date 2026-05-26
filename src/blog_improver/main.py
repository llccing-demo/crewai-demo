#!/usr/bin/env python
"""
Blog Improver — main entry point.

Usage:
    # Run Crew 1: suggest 5 new article topics based on blog analysis
    uv run python -m blog_improver.main suggest

    # Run Crew 2: SEO + quality review for a specific post
    uv run python -m blog_improver.main review --url https://rowanliu.com/posts/angular-signals-zoneless/

    # Run Crew 3: fetch, translate, and verify URL content coverage
    uv run python -m blog_improver.main translate --url https://example.com/article
"""

import sys
import argparse
from pathlib import Path

# Allow running from repo root
sys.path.insert(0, str(Path(__file__).parent.parent))

from blog_improver.crew import BlogTopicStrategistCrew, SEOReviewerCrew, UrlTranslationCrew
from blog_improver.llm import format_llm_runtime_info, validate_llm_connection


def run_suggest() -> None:
    """Run Crew 1 — Blog Topic Strategist."""
    print("\n🚀 启动 Crew 1：博客选题策略师\n" + "=" * 50)
    print(format_llm_runtime_info())
    validate_llm_connection()
    result = BlogTopicStrategistCrew().crew().kickoff()
    print("\n✅ 完成！选题建议已保存至 suggestions.md\n")
    print(result)


def run_review(article_url: str) -> None:
    """Run Crew 2 — SEO & Quality Reviewer for a given post URL."""
    print(f"\n🔍 启动 Crew 2：文章 SEO & 质量审查\n目标文章：{article_url}\n" + "=" * 50)
    print(format_llm_runtime_info())
    validate_llm_connection()
    result = SEOReviewerCrew().crew().kickoff(inputs={"article_url": article_url})
    print("\n✅ 完成！审查报告已保存至 review.md\n")
    print(result)


def run_translate(source_url: str, target_language: str) -> None:
    """Run Crew 3 — URL fetch, translation, and completeness verification."""
    print(
        f"\n🌐 启动 Crew 3：URL 抓取 + 翻译 + 完整性校验\n"
        f"目标 URL：{source_url}\n目标语言：{target_language}\n"
        + "=" * 50
    )
    print(format_llm_runtime_info())
    validate_llm_connection()
    result = UrlTranslationCrew().crew().kickoff(
        inputs={
            "source_url": source_url,
            "target_language": target_language,
        }
    )
    print("\n✅ 完成！抓取、翻译和校验结果已生成。\n")
    print(result)


def main() -> None:
    parser = argparse.ArgumentParser(description="Blog Improver — CrewAI Demo")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # suggest sub-command
    subparsers.add_parser("suggest", help="分析博客并生成 5 个新文章选题建议")

    # review sub-command
    review_parser = subparsers.add_parser("review", help="对指定文章进行 SEO 和质量审查")
    review_parser.add_argument(
        "--url",
        default="https://rowanliu.com/posts/angular-signals-zoneless/",
        help="要审查的博客文章 URL（默认：Angular Signals 文章）",
    )

    # translate sub-command
    translate_parser = subparsers.add_parser(
        "translate",
        help="抓取 URL 内容，翻译成中文，并校验是否有遗漏",
    )
    translate_parser.add_argument(
        "--url",
        default="https://rowanliu.com/posts/angular-signals-zoneless/",
        help="要抓取和翻译的 URL",
    )
    translate_parser.add_argument(
        "--target-language",
        default="Simplified Chinese",
        help="目标语言（默认：Simplified Chinese）",
    )

    args = parser.parse_args()

    if args.command == "suggest":
        run_suggest()
    elif args.command == "review":
        run_review(args.url)
    elif args.command == "translate":
        run_translate(args.url, args.target_language)


if __name__ == "__main__":
    main()
