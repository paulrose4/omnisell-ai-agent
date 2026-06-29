# OmniSell AI 迁移部署指南

本文档用于把当前 `omnisell-ai` 项目迁移到另一台设备运行。当前项目代码已经完成，并且在本机通过了 Python 单元测试；由于当前电脑没有可用 Docker/GPU 环境，容器启动和 vLLM GPU 推理需要在目标机器上验证。

## 1. 当前项目状态

已完成模块：

| 模块 | 状态 | 说明 |
| --- | --- | --- |
| Docker Compose 基础设施 | 已完成 | vLLM、ChromaDB、Mock Platform、API 网关 |
| Mock 电商平台 | 已完成 | FastAPI + OAuth2 授权码流程 + 商品/订单 API |
| RAG 知识库 | 已完成 | FAQ、产品、政策数据；ChromaDB 构建脚本；检索器；翻译 pipeline |
| AutoGen 多智能体 | 已完成 | 运营、客服、风控 Agent + GroupChat 编排 |
| 安全系统 | 已完成 | Docker 沙箱、HITL、熔断器、审计日志 |
| API 网关 | 已完成 | 统一 REST API、API Key、限流、管理接口 |
| 测试 | 已完成 | 本地测试 `15 passed` |

已通过的测试命令：

```powershell
pytest -q .\tests\test_e2e.py .\tests\test_agents.py .\tests\test_safety.py
```

未在当前电脑验证的内容：

- `docker compose up -d`
- vLLM 容器 GPU 推理
- ChromaDB 容器持久化运行
- API 网关容器和 Mock Platform 容器联调

原因：当前电脑没有可用 `docker` 命令。

## 2. 推荐目标机器配置

最低开发配置：

| 项目 | 建议 |
| --- | --- |
| 操作系统 | Ubuntu 22.04/24.04，或 Windows 11 + WSL2 + Docker Desktop |
| GPU | NVIDIA GPU，建议显存 12GB+；7B GPTQ Int4 通常更省显存，但仍建议留余量 |
| 内存 | 32GB+ |
| 磁盘 | 80GB+ 可用空间，模型、镜像、Chroma 数据会占空间 |
| Python | 3.12 推荐；3.11 也通常可用 |
| Docker | Docker Engine 或 Docker Desktop |
| GPU 容器支持 | NVIDIA Driver + NVIDIA Container Toolkit |

如果暂时没有 GPU，也可以先跑：

- Mock Platform
- RAG 数据构建
- API 网关部分接口
- 单元测试

但 `vllm-server` 无法正常启动 GPU 推理。

## 3. 需要安装的软件

### 3.1 Git

用于拷贝或拉取项目。

Windows:

```powershell
git --version
```

Ubuntu:

```bash
sudo apt update
sudo apt install -y git
git --version
```

### 3.2 Python

推荐 Python 3.12。

验证：

```bash
python --version
pip --version
```

Windows 可能需要使用：

```powershell
py -3.12 --version
```

### 3.3 Docker

Windows 推荐 Docker Desktop + WSL2。Docker 官方文档说明 Docker Desktop 可启用 WSL2 backend，并在 Linux containers 模式下工作。参考：[Docker Desktop WSL2 backend](https://docs.docker.com/desktop/features/wsl/)。

Ubuntu 推荐 Docker Engine 或 Docker Desktop。安装后验证：

```bash
docker --version
docker compose version
docker run --rm hello-world
```

### 3.4 NVIDIA 驱动和容器运行时

目标机器需要先安装 NVIDIA 驱动，并确认：

```bash
nvidia-smi
```

然后安装 NVIDIA Container Toolkit。NVIDIA 官方文档说明该工具用于让 Docker 容器访问 NVIDIA GPU。参考：[Installing the NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html)。

验证 Docker 是否能看到 GPU：

```bash
docker run --rm --gpus all nvidia/cuda:12.4.1-base-ubuntu22.04 nvidia-smi
```

如果这一步失败，先不要启动本项目，先修 Docker GPU 环境。

## 4. 需要的 Python 依赖

所有 Python 库已放在：

```text
requirements.txt
```

核心依赖包括：

| 依赖 | 用途 |
| --- | --- |
| `fastapi`, `uvicorn` | API 网关和 Mock Platform |
| `pydantic`, `pydantic-settings` | 数据模型和配置 |
| `httpx`, `openai` | HTTP 调用和 vLLM OpenAI 兼容接口 |
| `chromadb` | 向量数据库客户端 |
| `sentence-transformers` | 多语言向量化 |
| `transformers`, `torch`, `sentencepiece`, `sacremoses` | OPUS-MT 翻译和模型推理依赖 |
| `pyautogen` | AutoGen Agent / GroupChat |
| `docker` | Docker SDK，沙箱执行器使用 |
| `huggingface-hub` | 下载 Qwen 模型 |
| `pytest` | 本地测试 |

安装方式：

```bash
python -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

Windows PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
```

如果目标机器是 CUDA GPU，`torch` 的安装可能需要按 PyTorch 官方 CUDA 版本安装对应 wheel。若普通 `pip install -r requirements.txt` 装到 CPU 版 PyTorch，vLLM 仍由 Docker 容器负责推理，API 本身不一定受影响，但本地跑翻译/embedding 会慢。

## 5. 环境变量配置

复制模板：

```bash
cp .env.example .env
```

Windows PowerShell:

```powershell
Copy-Item .env.example .env
```

推荐 `.env` 内容：

```env
VLLM_BASE_URL=http://localhost:8000/v1
VLLM_MODEL_NAME=Qwen2.5-7B-Instruct-GPTQ-Int4
VLLM_API_KEY=not-needed

CHROMA_HOST=localhost
CHROMA_PORT=8001
CHROMA_COLLECTION=omnisell_customer_kb

MOCK_PLATFORM_URL=http://localhost:8002
OAUTH_CLIENT_ID=omnisell_agent
OAUTH_CLIENT_SECRET=dev_secret_key_12345
OAUTH_REDIRECT_URI=http://localhost:3000/oauth/callback

API_KEY=dev_api_key_12345
RATE_LIMIT_PER_MINUTE=60

SANDBOX_IMAGE=omnisell-sandbox:latest
SANDBOX_TIMEOUT_SECONDS=30
```

Docker Compose 中 API 容器会覆盖部分地址为容器内服务名：

```env
VLLM_BASE_URL=http://vllm-server:8000/v1
CHROMA_HOST=chromadb
CHROMA_PORT=8000
MOCK_PLATFORM_URL=http://mock-platform:8000
```

所以：

- 在宿主机直接运行 Python：使用 `localhost`
- 在 Docker Compose 内运行 API：使用 compose 服务名

## 6. 项目目录说明

```text
omnisell-ai/
├── api/                 # API 网关
├── agents/              # AutoGen 多智能体
├── mock_platform/       # Mock Shopee Open API
├── rag/                 # RAG 知识库
├── sandbox/             # Docker 沙箱执行器
├── safety/              # HITL、熔断、审计
├── scripts/             # 模型下载、vLLM 测试
├── tests/               # 单元和端到端测试
├── docker-compose.yml
├── requirements.txt
├── .env.example
└── README.md
```

## 7. 迁移项目到另一台电脑

方式一：直接复制整个目录。

建议排除这些目录：

```text
.venv/
__pycache__/
.pytest_cache/
chroma_data/
models/
logs/
```

如果模型已经下载好，也可以复制 `models/`，避免重新下载。

方式二：使用 Git。

```bash
git clone <你的仓库地址> omnisell-ai
cd omnisell-ai
```

当前目录不是 Git 仓库时，你可以先在本机初始化：

```bash
git init
git add .
git commit -m "Initial OmniSell AI project"
```

## 8. 下载模型

项目使用：

```text
Qwen/Qwen2.5-7B-Instruct-GPTQ-Int4
```

下载脚本：

```bash
python scripts/download_model.py
```

模型会下载到：

```text
models/Qwen2.5-7B-Instruct-GPTQ-Int4
```

如果 Hugging Face 需要登录：

```bash
huggingface-cli login
```

如果网络无法访问 Hugging Face，你可以在有网络的机器上下载后，把整个目录复制到目标机器的 `models/` 下。

## 9. 构建沙箱镜像

沙箱执行器需要这个镜像：

```bash
docker build -f sandbox/sandbox.Dockerfile -t omnisell-sandbox:latest ./sandbox
```

说明：沙箱 Dockerfile 使用 `python:3.12-alpine`。Docker 官方 Python 镜像标签中有 `3.12-slim` 和 `3.12-alpine`，没有 `3.12-slim-alpine` 这个组合标签；为了保证目标机器可以实际构建，这里使用官方存在的 Alpine 标签。

验证：

```bash
docker run --rm --network none --read-only omnisell-sandbox:latest
```

## 10. 启动 Docker Compose 服务

先确认 `.env` 存在：

```bash
ls .env
```

启动：

```bash
docker compose up -d --build
```

查看状态：

```bash
docker compose ps
```

查看日志：

```bash
docker compose logs -f mock-platform
docker compose logs -f chromadb
docker compose logs -f vllm-server
docker compose logs -f api
```

vLLM 官方文档说明 `vllm/vllm-openai` 是 OpenAI API 兼容服务镜像，可通过 Docker 运行并暴露 OpenAI-compatible server。参考：[vLLM Docker deployment](https://docs.vllm.ai/en/stable/deployment/docker/) 和 [vLLM OpenAI-compatible quickstart](https://docs.vllm.ai/en/latest/getting_started/quickstart/)。

Chroma 官方 Docker 文档说明可以用容器运行 Chroma server，并通过客户端连接；也支持挂载目录持久化。参考：[Chroma Docker docs](https://docs.trychroma.com/guides/deploy/docker)。

## 11. 构建 RAG 知识库

如果使用 Docker Compose 中的 ChromaDB：

```bash
python rag/build_kb.py --host localhost --port 8001 --collection omnisell_customer_kb --reset
```

如果只想本地文件持久化测试：

```bash
python rag/build_kb.py --persist-dir ./chroma_data --reset
```

dry-run 检查数据：

```bash
python rag/build_kb.py --dry-run
```

预期输出包含：

```text
FAQ documents: 140
Product documents: 56
Policy documents: 20
Total prepared: 216
```

## 12. 验证 Mock Platform

健康检查：

```bash
curl http://localhost:8002/health
```

Swagger 文档：

```text
http://localhost:8002/docs
```

OAuth 授权码流程由 API 网关自动处理。手动测试时，可以先访问：

```text
http://localhost:8002/oauth/authorize?response_type=code&client_id=omnisell_agent&redirect_uri=http://localhost:3000/oauth/callback&state=test&scope=product.read%20product.write%20order.read%20order.write
```

## 13. 验证 vLLM

健康检查：

```bash
curl http://localhost:8000/health
```

运行项目测试脚本：

```bash
python scripts/test_vllm.py
```

如果失败，优先检查：

1. `docker compose logs -f vllm-server`
2. `nvidia-smi`
3. `docker run --rm --gpus all nvidia/cuda:12.4.1-base-ubuntu22.04 nvidia-smi`
4. `models/Qwen2.5-7B-Instruct-GPTQ-Int4` 是否存在

## 14. 验证 API 网关

健康检查不需要 API key：

```bash
curl http://localhost:8080/api/admin/health
```

业务接口需要：

```text
X-API-Key: dev_api_key_12345
```

客服问答：

```bash
curl -X POST http://localhost:8080/api/customer/ask \
  -H "Content-Type: application/json" \
  -H "X-API-Key: dev_api_key_12345" \
  -d "{\"question\":\"如何申请退货？\",\"target_language\":\"zh\",\"category_filter\":\"退换货\"}"
```

商品同步 dry-run：

```bash
curl -X POST http://localhost:8080/api/sync-product \
  -H "Content-Type: application/json" \
  -H "X-API-Key: dev_api_key_12345" \
  -d "{\"product_id\":\"P1001\",\"target_languages\":[\"en\"],\"dry_run\":true}"
```

价格风控：

```bash
curl -X POST http://localhost:8080/api/price/monitor \
  -H "Content-Type: application/json" \
  -H "X-API-Key: dev_api_key_12345" \
  -d "{\"product_id\":\"P1001\",\"current_price\":100,\"currency\":\"USD\",\"market\":\"SG\"}"
```

查看 HITL 待审批：

```bash
curl http://localhost:8080/api/admin/approvals \
  -H "X-API-Key: dev_api_key_12345"
```

查看审计日志：

```bash
curl http://localhost:8080/api/admin/audit \
  -H "X-API-Key: dev_api_key_12345"
```

## 15. 本地测试

安装依赖后运行：

```bash
pytest -q tests
```

当前本机最后一次结果：

```text
15 passed
```

注意：这些测试使用 mock/fallback 覆盖外部服务，因此能验证代码逻辑，但不等同于 Docker + GPU 全链路启动成功。

## 16. 常见问题

### 16.1 docker 命令不存在

说明 Docker 没安装或没有加入 PATH。

验证：

```bash
docker --version
docker compose version
```

Windows 请确认 Docker Desktop 已启动，并使用 Linux containers。

### 16.2 vLLM 容器看不到 GPU

先验证：

```bash
nvidia-smi
docker run --rm --gpus all nvidia/cuda:12.4.1-base-ubuntu22.04 nvidia-smi
```

若第二条失败，重点检查 NVIDIA Container Toolkit。

### 16.3 模型路径错误

Compose 中 vLLM 使用：

```text
/models/Qwen2.5-7B-Instruct-GPTQ-Int4
```

宿主机必须存在：

```text
./models/Qwen2.5-7B-Instruct-GPTQ-Int4
```

### 16.4 API 返回 401

请求头缺少或填错：

```text
X-API-Key: dev_api_key_12345
```

如果你修改了 `.env` 的 `API_KEY`，请求头也要同步修改。

### 16.5 RAG 检索为空

先构建知识库：

```bash
python rag/build_kb.py --host localhost --port 8001 --collection omnisell_customer_kb --reset
```

然后重启 API：

```bash
docker compose restart api
```

### 16.6 AutoGen 未安装或导入失败

项目有 fallback，可跑测试；但真实 GroupChat 建议安装：

```bash
pip install pyautogen>=0.7.0
```

如果安装后仍有 API 兼容问题，先运行：

```bash
python -c "import autogen; print(autogen.__version__)"
```

### 16.7 OPUS-MT 翻译模型下载慢

首次调用翻译会从 Hugging Face 下载模型。可提前设置 Hugging Face 缓存目录，或在有网络的机器上预下载。

## 17. 推荐启动顺序

完整启动建议按这个顺序：

1. 安装 NVIDIA 驱动、Docker、NVIDIA Container Toolkit
2. 验证 `docker --version`
3. 验证 `nvidia-smi`
4. 验证 `docker run --rm --gpus all nvidia/cuda:12.4.1-base-ubuntu22.04 nvidia-smi`
5. 复制项目到目标机器
6. 创建 `.env`
7. 安装 Python 依赖
8. 下载 Qwen 模型
9. 构建沙箱镜像
10. `docker compose up -d --build`
11. 构建 RAG 知识库
12. 测试 vLLM
13. 测试 API 网关
14. 运行 `pytest -q tests`

## 18. 关键端口

| 服务 | 端口 | 地址 |
| --- | --- | --- |
| vLLM | 8000 | http://localhost:8000/v1 |
| ChromaDB | 8001 | http://localhost:8001 |
| Mock Platform | 8002 | http://localhost:8002 |
| API Gateway | 8080 | http://localhost:8080 |

## 19. 外部 API 和密钥

当前项目默认不依赖真实商业外部 API。

需要的配置：

| 名称 | 是否必须 | 说明 |
| --- | --- | --- |
| `API_KEY` | 必须 | API 网关请求鉴权 |
| `OAUTH_CLIENT_ID` | 必须 | Mock Platform OAuth 客户端 ID |
| `OAUTH_CLIENT_SECRET` | 必须 | Mock Platform OAuth 客户端密钥 |
| `HF_TOKEN` | 可选 | Hugging Face 私有模型或限流时可能需要 |
| `VLLM_API_KEY` | 可选 | 本地 vLLM 默认不需要，保留兼容 |

默认开发值可以直接跑本地 mock；生产或多人环境请务必修改。
