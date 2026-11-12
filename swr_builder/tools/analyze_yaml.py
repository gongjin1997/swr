"""CLI 工具：分析 ns-yaml 目录，推断资源需求。

使用：
    python -m swr_builder.tools.analyze_yaml /data/swr/yaml/ns-yaml
"""
import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict

import yaml

from ..core.resource_inference import analyze_namespace_yaml


def load_yaml_file(file_path: Path) -> list[Dict[str, Any]]:
    """加载 YAML 文件（支持多文档）"""
    with open(file_path, "r", encoding="utf-8") as f:
        # 使用 safe_load_all 支持多文档 YAML
        documents = list(yaml.safe_load_all(f))

    # 处理 kubectl get all -o yaml 格式（有 items 列表）
    all_objects = []
    for doc in documents:
        if doc is None:
            continue
        if isinstance(doc, dict) and "items" in doc:
            all_objects.extend(doc["items"])
        elif isinstance(doc, list):
            all_objects.extend(doc)
        else:
            all_objects.append(doc)

    return all_objects


def analyze_directory(ns_yaml_dir: Path) -> Dict[str, Any]:
    """分析整个 ns-yaml 目录"""
    results = {}

    for yaml_file in ns_yaml_dir.rglob("*.yml"):
        if yaml_file.name.startswith("."):
            continue

        namespace = yaml_file.parent.name
        print(f"分析 {namespace}/{yaml_file.name}...", file=sys.stderr)

        try:
            objects = load_yaml_file(yaml_file)
            analysis = analyze_namespace_yaml(objects)

            if analysis["deployments"] or analysis["configmaps"]:
                results[namespace] = analysis

        except Exception as e:
            print(f"  错误: {e}", file=sys.stderr)
            continue

    return results


def print_summary(results: Dict[str, Any]) -> None:
    """打印汇总报告"""
    print("\n=== 资源推断汇总 ===\n")

    total_summary = {}

    for namespace, analysis in results.items():
        print(f"命名空间: {namespace}")
        print(f"  Deployments: {len(analysis['deployments'])}")
        print(f"  ConfigMaps: {len(analysis['configmaps'])}")

        if analysis["summary"]:
            print("  推断的资源类型:")
            for resource_type, count in analysis["summary"].items():
                print(f"    - {resource_type}: {count}")
                total_summary[resource_type] = total_summary.get(resource_type, 0) + count
        print()

    print("=== 全局统计 ===")
    print(f"总命名空间数: {len(results)}")
    print("推断的资源类型分布:")
    for resource_type, count in sorted(total_summary.items(), key=lambda x: -x[1]):
        print(f"  - {resource_type}: {count}")


def main():
    parser = argparse.ArgumentParser(description="分析 ns-yaml 目录，推断资源需求")
    parser.add_argument("ns_yaml_dir", type=Path, help="ns-yaml 目录路径")
    parser.add_argument("--output", "-o", type=Path, help="输出 JSON 文件路径")
    parser.add_argument("--verbose", "-v", action="store_true", help="详细输出")

    args = parser.parse_args()

    if not args.ns_yaml_dir.is_dir():
        print(f"错误: {args.ns_yaml_dir} 不是目录", file=sys.stderr)
        sys.exit(1)

    print(f"分析目录: {args.ns_yaml_dir}", file=sys.stderr)
    results = analyze_directory(args.ns_yaml_dir)

    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(results, f, ensure_ascii=False, indent=2)
        print(f"\n结果已保存到: {args.output}", file=sys.stderr)

    if args.verbose:
        print(json.dumps(results, ensure_ascii=False, indent=2))
    else:
        print_summary(results)


if __name__ == "__main__":
    main()
