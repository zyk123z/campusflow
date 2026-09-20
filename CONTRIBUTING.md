# Contributing to CampusFlow

感谢你对 CampusFlow 的关注。为了让变更更容易审查和回溯，请遵循以下流程。

## 开始之前

1. 先搜索现有 Issue，避免重复报告。
2. 较大的功能改动建议先开 Issue，说明用例、边界和预期行为。
3. 不要提交真实学生数据、凭据、本地数据库、运行台账或模型权重。

## 本地开发

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
Copy-Item .env.example .env
```

启动 MySQL 和 Redis：

```powershell
docker compose up -d mysql redis
```

## 提交前检查

```powershell
$env:AI_PROVIDER = "mock"
$env:DATABASE_URL = "sqlite:///:memory:"
$env:KNOWLEDGE_VECTOR_ENABLED = "false"
python -m compileall app tests
python -m unittest discover -s tests -v
```

请确保：

- 新功能包含相应测试。
- 行为变化已更新 README 或相关知识文档。
- `git status` 中不包含 `.env`、`data/*.xlsx`、`target/` 或 `models/`。
- Pull Request 保持单一主题，描述动机、实现方式与验证结果。

## 提交信息

建议使用简洁的命令式提交信息，例如：

```text
Add ticket retry metrics
Fix duplicate emergency alerts
Document local Ollama setup
```
