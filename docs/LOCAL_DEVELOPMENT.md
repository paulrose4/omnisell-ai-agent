# 本地运行与云端解耦

本地运行不需要域名、DNS、TLS、公司 OIDC 或真实 Amazon 凭证。它使用与未来云端相同的应用镜像、PostgreSQL、Redis、Celery、Alembic、租户 RLS 和权限链，只替换边缘入口和外部服务适配器。

## 首次环境准备

OmniSell 的本地 Python 基线是 3.12。首次 clone 或依赖变化后运行：

```powershell
./scripts/bootstrap_dev.ps1
```

该脚本创建隔离虚拟环境，并执行锁定依赖安装、`pip check`、`pip-audit`、`compileall`、Alembic 快速检查和关键安全 smoke。已有虚拟环境不会被静默覆盖；需要重建时显式使用 `-Recreate`。

当前活动迁移历史已经冻结为单一 `0023` 基线。任何曾运行旧 `0001`-`0023` 原型迁移链的本地 SQLite 或 Compose PostgreSQL 数据都不兼容，必须使用 `./scripts/stop_local.ps1 -ResetData` 后重新创建。冻结决策和证据边界见 `docs/adr/ADR-001-freeze-alembic-baseline-before-default-branch-merge.md`。

Bootstrap 会校验版本化的 SQLite schema fingerprint，并完成 `upgrade -> downgrade base -> re-upgrade`。该结果只证明 DDL 可移植性，不证明 PostgreSQL RLS；权威 RLS 证据由 CI 的 PostgreSQL 非 owner 测试和 `postgres-schema-fingerprint.json` 产物提供。

## 一键启动

前提：Docker Desktop 已启动，并已完成上述 bootstrap。

```powershell
./scripts/start_local.ps1
```

首次运行会从 `.env.local.example` 创建被 Git 忽略的 `.env.local`。启动并验证完成后访问：

- Web UI：`http://localhost:3000`
- API：`http://localhost:8080`
- Mock OIDC/Amazon：`http://localhost:9000`
- PostgreSQL：`localhost:55432`

登录页提供 Owner、Operator、Approver 和 Viewer 四种本地身份。所有身份都经过真实 OIDC JWT、MFA claim、membership、tenant 和 RBAC 校验。Amazon 始终保持只读 pilot，批准不会绕过写入门禁。

如果只需要启动、不重复运行集成 smoke：

```powershell
./scripts/start_local.ps1 -SkipSmoke
```

如果镜像已经构建：

```powershell
./scripts/start_local.ps1 -NoBuild
```

## 停止与重置

保留 PostgreSQL/Redis 数据：

```powershell
./scripts/stop_local.ps1
```

删除本地容器和持久化数据：

```powershell
./scripts/stop_local.ps1 -ResetData
```

## 配置分层

| 层 | 文件 | 职责 | 云端替换方式 |
| --- | --- | --- | --- |
| 应用/数据层 | `docker-compose.e2e.yml` | API、worker、scheduler、PostgreSQL、Redis、迁移和 Mock 外部服务 | 云端使用托管 PostgreSQL/Redis 和真实 OIDC/Amazon endpoints |
| 本地入口层 | `docker-compose.local.yml` | Next.js UI、localhost 端口和容器内部 backchannel | 云端使用 `docker-compose.production.yml`、Ingress/LB/Caddy |
| 本地参数 | `.env.local` | 端口和本机专用测试 secret | 云端由 Secret Manager 和部署平台注入 |
| 生产参数 | `.env.production`、`frontend/.env.production` | OIDC、数据库、Redis、Amazon、模型和观测配置 | 不进入 Git，不进入镜像 |

## 解耦边界

- **公网 URL 与内部 URL 分离**：浏览器使用 `AUTH_OIDC_ISSUER`，前端容器使用 `AUTH_OIDC_WELL_KNOWN_URL` 与 `AUTH_OIDC_INTERNAL_BASE_URL` 完成 discovery/token/JWKS backchannel。上云后可分别映射公网 IdP 地址和集群内部地址。
- **前端只访问 API 服务名**：容器内 `BACKEND_API_URL=http://api:8000`，不依赖宿主机端口。云端只需把该值替换为内部 Service 地址。
- **外部平台通过适配器替换**：本地 Mock OIDC/Amazon 与真实服务使用相同 HTTP/OAuth 契约，业务服务和领域规则不需要修改。
- **边缘代理可选**：本地直接暴露 3000/8080；生产才添加 TLS、域名、Caddy/Ingress/WAF。
- **状态服务可替换**：应用只依赖 PostgreSQL URL、Redis URL 和 OTLP endpoint；Compose volume 可以无代码修改地替换为托管服务。
- **迁移独立执行**：Alembic migration job 与 API 镜像一致，但使用独立 owner 连接；云端可变成一次性 Job。

## 本地端口冲突

编辑 `.env.local` 中的端口后重新运行启动脚本。前端、API、Mock Cloud、PostgreSQL 和 Redis 端口都可独立配置。不要修改 Compose 文件来适配个人电脑。

## 外部 OpenAI-compatible 模型

本地控制面默认尝试连接宿主机 OpenAI-compatible 模型端点。要使用外部 staging 模型，在 `.env.local` 填写 `LOCAL_MODEL_BASE_URL`、`LOCAL_MODEL_NAME` 和 `LOCAL_MODEL_API_KEY`。地址必须包含供应商要求的 `/v1` 或 compatible-mode 路径，Key 只保存在被 Git 忽略的 `.env.local`，不得写入 Compose、文档或提交记录。

如需启用备用中转站，额外填写 `LOCAL_MODEL_FALLBACK_ENABLED=true`、`LOCAL_MODEL_FALLBACK_BASE_URL=https://api.wenrugouai.com/v1`、`LOCAL_MODEL_FALLBACK_MODEL_NAME` 和 `LOCAL_MODEL_FALLBACK_API_KEY`。备用模型只在主模型超时、连接失败、限流或可重试的 5xx 错误时使用一次；安全拒绝、Schema 错误和预算错误不会切换。

填写后执行 `./scripts/start_local.ps1 -NoBuild` 重新创建运行容器，再执行 `python scripts/test_model_gateway.py` 做基础连通性检查。严格 JSON Schema 和 Advisor 安全边界仍需使用 `scripts/run_real_model_staging_smoke.py` 验证。

## 不属于本地启动前提的项目

域名、DNS、TLS、WAF、公司 OIDC、真实 Amazon Draft/Public 应用、托管数据库、Secret Manager 和正式告警平台都不是当前本地运行的前提。它们属于后续云端部署覆盖层，不应写死在业务代码中。
