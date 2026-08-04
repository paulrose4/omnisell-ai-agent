from __future__ import annotations

import argparse
import sys
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from agents.tools.vision_tool import analyze_customer_image


def main() -> None:
    load_dotenv(PROJECT_ROOT / ".env")

    parser = argparse.ArgumentParser(description="Test OmniSell multimodal image analysis with Ollama vision models.")
    parser.add_argument("image_path", help="Path to a local image file.")
    parser.add_argument("--question", default="请分析这张电商图片，并提取客服处理需要的信息。")
    parser.add_argument("--target-language", default="zh")
    parser.add_argument("--task-hint", default="电商图片识别")
    args = parser.parse_args()

    result = analyze_customer_image(
        question=args.question,
        image_path=args.image_path,
        target_language=args.target_language,
        task_hint=args.task_hint,
    )

    print(f"Vision model: {result['vision_model']}")
    print(f"Vision base URL: {result['vision_base_url']}")
    print("\nAnalysis:")
    print(result["analysis"])


if __name__ == "__main__":
    main()
