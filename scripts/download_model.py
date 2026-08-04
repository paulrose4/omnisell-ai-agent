from __future__ import annotations

import argparse
import subprocess

DEFAULT_MODELS = ["qwen2.5:3b", "llava:7b"]


def run_ollama_pull(model_name: str) -> None:
    print(f"Pulling Ollama model: {model_name}")
    subprocess.run(["ollama", "pull", model_name], check=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="Download local Ollama models used by OmniSell AI.")
    parser.add_argument("models", nargs="*", default=DEFAULT_MODELS, help="Ollama model names to pull.")
    args = parser.parse_args()

    for model_name in args.models:
        run_ollama_pull(model_name)

    print("Model download complete.")


if __name__ == "__main__":
    main()
