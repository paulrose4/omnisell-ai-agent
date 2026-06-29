from pathlib import Path

from huggingface_hub import snapshot_download


REPO_ID = "qwen/qwen2.5-3b"
PROJECT_ROOT = Path(__file__).resolve().parents[1]
TARGET_DIR = PROJECT_ROOT / "models" / "qwen2.5-3b"


def main() -> None:
    TARGET_DIR.mkdir(parents=True, exist_ok=True)

    print(f"寮€濮嬩笅杞芥ā鍨? {REPO_ID}")
    print(f"鐩爣鐩綍: {TARGET_DIR}")
    print("huggingface_hub 浼氳嚜鍔ㄦ樉绀烘枃浠朵笅杞借繘搴︺€?)

    snapshot_download(
        repo_id=REPO_ID,
        local_dir=str(TARGET_DIR),
        local_dir_use_symlinks=False,
    )

    print("妯″瀷涓嬭浇瀹屾垚銆?)


if __name__ == "__main__":
    main()
