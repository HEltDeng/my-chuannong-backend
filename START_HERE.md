# 后台 1.0.0 接手入口

从 `codex/release-1.0.0` 开始，先读 AGENTS.md 与 README.md。沿用现有后台，包含二手/失物审核、校外机构预约、平台账号、资料公告等更新；无需另建仓库或导入旧产品数据库。

```text
git clone --branch codex/release-1.0.0 https://github.com/HEltDeng/my-chuannong-backend.git
cd my-chuannong-backend
python -m venv .venv
```

Windows 使用 `.venv/Scripts/python.exe`，macOS/Linux 使用 `.venv/bin/python`：

```text
python -m pip install -r requirements.lock.txt
python -m unittest discover -s tests -v
python tools/verify_checkout.py
```

测试使用隔离的合成资料和临时数据库，不需要旧电脑真实账号、学校凭据或生产数据。源码清单用于核对冻结版本；开发后有意修改会产生差异，不要为通过检查而重置成果。

本地启动时通过仓库外的 `MY_CHUANNONG_LOCAL_CREDENTIALS` JSON 配置 `adminUsername`、`adminPassword`、`phoneHashSecret`、`deviceKeySecret`、`piiEncryptionKey`，最后一项必须是 Fernet Key。执行 `python run_local.py` 或 `start-local.ps1`，仅监听 `127.0.0.1:8792`。不再提供预填示例公告入口。

正式部署使用独立 HTTPS 地址、生产环境配置和原正式签名公开指纹，不复用 Debug 配置。SQLite、uploads、backups、日志和凭据另行保管迁移，不能上传源码仓库。不要清空已有用户数据库来获得空白环境。

配套客户端：[Android 1.0.0](https://github.com/HEltDeng/my-chuannong-android/tree/codex/release-1.0.0)。本仓库仅同步源码，不包含本机运营数据，不部署公网。客户端 APK 需原签名构建；原 JKS 后续已由用户提供迁移包，在仓库外恢复并验证历史证书；密钥和口令不随源码上传。
