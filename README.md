# 软件质量管理后端

这个项目从 `rag-center` 复制而来，保留同样的技术栈和分层方式：
`FastAPI + SQLAlchemy 2.x async + Alembic + PostgreSQL/pgvector + Pydantic Settings`。

当前业务目标改成**通用软件质量管理系统**，先聚焦两类资产：

1. 生命周期文档：需求、设计、测试、评审、发布说明等
2. 代码：仓库代码、模块文件、测试代码

后续要做的核心事情是：

- 文档和需求的一致性检查
- 文档之间的一致性检查
- 文档与代码的一致性检查
- 代码功能与需求的一致性检查
- 测试对需求的覆盖分析

## 当前定位

这是一个“资料接入 + 质量分析底座”，不是纯 RAG 产品。
底层仍复用原来的文档切片、向量存储、重排和统一异常处理能力，方便后面快速扩展到：

- 需求追踪
- 变更影响分析
- 测试覆盖评估
- 质量门禁
- 证据链汇总

## 技术栈

- Python 3.11
- FastAPI
- Pydantic v2 / pydantic-settings
- SQLAlchemy 2.x async
- Alembic
- PostgreSQL 16 + pgvector 16
- Docker / Docker Compose
- OpenAI-compatible embedding / LLM 接口

## 目录结构

```text
app/
  api/              # HTTP 接口与依赖注入
  core/             # 配置、日志、异常
  db/               # SQLAlchemy Base 与异步会话
  models/           # 持久化模型
  schemas/          # 请求 / 响应模型
  repositories/     # 数据访问层
  providers/        # 文档、embedding、LLM、重排、向量库适配器
  services/         # 业务编排
  utils/            # id、切分器等通用工具
migrations/         # Alembic 迁移
tests/              # 单元与契约测试
```

## 当前可复用能力

- 文档接入与切片
- 向量化索引
- pgvector 相似度检索
- LLM 重排开关
- 统一错误码与日志
- 异步 SQLAlchemy 数据层

## 运行方式

```bash
cp .env.example .env
docker compose up --build
```

本地开发：

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -e ".[dev]"
alembic upgrade head
uvicorn app.main:app --reload
```

## 现在的 API

当前仍沿用原始骨架的接口名，方便先把底座跑起来：

- `POST /api/v1/knowledge-bases/create`
- `POST /api/v1/documents/upload`
- `POST /api/v1/rag/retrieve`

后续建议按软件质量管理语义逐步改成：

- `projects`
- `artifacts`
- `analysis`

## 质量管理下一步

- 接入生命周期文档类型识别
- 接入代码仓库导入
- 建立需求-文档-代码-测试的 trace link
- 做一致性检查与覆盖率分析
- 建立数据集导入、样本标注和评测集管理
