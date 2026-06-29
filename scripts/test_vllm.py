import os
import time
from pathlib import Path

from dotenv import load_dotenv
from openai import APIConnectionError, APIStatusError, OpenAI, OpenAIError


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    load_dotenv(PROJECT_ROOT / ".env")

    base_url = os.getenv("VLLM_BASE_URL", "http://localhost:11434/v1")
    model_name = os.getenv("VLLM_MODEL_NAME", "qwen2.5:3b")

    client = OpenAI(
        base_url=base_url,
        api_key=os.getenv("VLLM_API_KEY", "not-needed"),
        timeout=60.0,
    )

    prompt = "用中文回答：什么是电商智能体？"
    print(f"正在连接 vLLM: {base_url}")
    print(f"模型名称: {model_name}")

    started_at = time.perf_counter()

    try:
        response = client.chat.completions.create(
            model=model_name,
            messages=[
                {"role": "system", "content": "你是 OmniSell AI 的电商自动化助手。"},
                {"role": "user", "content": prompt},
            ],
            temperature=0.2,
            max_tokens=256,
        )
        elapsed = time.perf_counter() - started_at
        content = response.choices[0].message.content or ""

        print("\n模型回复:")
        print(content.strip())
        print(f"\n耗时: {elapsed:.2f} 秒")
    except APIConnectionError as exc:
        print("\n连接失败: 无法连接到 Ollama 服务。")
        print("请确认 Ollama 已启动，且 http://localhost:11434/ 可访问。")
        print(f"错误详情: {exc}")
    except APIStatusError as exc:
        print("\nOllama 返回了错误状态码。")
        print(f"状态码: {exc.status_code}")
        print(f"响应内容: {exc.response.text}")
    except OpenAIError as exc:
        print("\nOpenAI SDK 调用失败。")
        print(f"错误详情: {exc}")
    except Exception as exc:
        print("\n测试请求发生未知错误。")
        print(f"错误详情: {exc}")


if __name__ == "__main__":
    main()
