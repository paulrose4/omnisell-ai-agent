# OmniSell Admin Console

轻量级 Next.js 管理后台，用于 Agent 任务监控和 HITL 人工审批。

## 目录

```text
frontend/
├── app/
│   ├── agents/                 # Agent 监控大盘
│   ├── approvals/              # HITL 审批 Inbox
│   └── api/                    # Next.js BFF，服务端代理后端认证
├── components/
│   ├── agents/                 # 任务列表、指标与状态筛选
│   ├── approvals/              # 审批卡片和批准/拒绝表单
│   └── ui/                     # 通用状态组件
└── lib/api/
    ├── client.ts               # 浏览器 Fetch 封装
    ├── server.ts               # 后端代理与凭据注入
    └── types.ts                # 后端 API 类型
```

## 启动

```powershell
cd frontend
Copy-Item .env.example .env.local
npm install
npm run dev
```

打开 `http://localhost:3000`。

开发环境默认通过 API Key 连接 `http://localhost:8080`。OIDC 环境请设置 `BACKEND_AUTH_MODE=oidc` 和服务端短期 `BACKEND_ACCESS_TOKEN`。生产环境建议把 `server.ts` 接入正式登录会话，由服务端读取用户 Access Token，不要使用共享静态 Token。
