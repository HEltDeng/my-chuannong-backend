# Backend/Admin 接手入口

本仓库保存 **2026-10-13 用户确认归档的最新审计后端源码**，默认分支 `codex/accepted-preview9`。Android 的学校登录、VPN、课程和成绩直接在手机访问学校系统，不需要这个后台。此后台负责平台账号、管理员、自建内容、公告、配置与版本信息。

## 新电脑开始开发

GitHub 插件选择 `HEltDeng/my-chuannong-backend`，先读 `AGENTS.md`、本文件与 `README.md`。实际运行需要完整克隆和 Python 环境：

```text
git clone https://github.com/HEltDeng/my-chuannong-backend.git
cd my-chuannong-backend
git branch --show-current
python -m venv .venv
```

本轮验证使用 Python 3.12。Windows：

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.lock.txt
.\.venv\Scripts\python.exe -m unittest discover -s tests -p test_audit_guards.py -v
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

macOS/Linux 将解释器改为 `.venv/bin/python`。在仓库根目录执行；测试使用合成资料和临时 SQLite 数据，不需要旧电脑的账号库、真实密码或生产配置。运行 `python tools/verify_checkout.py` 可核对本次交付的源码清单；后续修改造成差异属于正常情况。

## 必须知道的当前状态

完整测试当前为 35 通过、8 失败、1 模块导入错误：

- `scripts/database_tools.py` 缺失，`tests/test_production.py` 中 17 项测试无法加载。应补齐并审查备份/恢复实现。
- 6 项失败涉及旧场地/库存或功能开放状态，2 项涉及旧发布配置；需要改用本产品的合成测试夹具。
- 不通过删除测试、填入旧产品运营资料或解除发布保护掩盖这些问题。
- 下载目标/重定向限制、流式大小上限、依赖制品校验等待办见 [Android 仓库内完整审计](https://github.com/HEltDeng/my-chuannong-android/blob/codex/accepted-preview9/docs/audit/2026-10-13-source-audit.md)。

这是当前最新后端源码的真实状态，不是尚未编写后端，也不是已部署且全部验收成功的后端。此次用户授权将现状连同审计结论归档，后续开发从这里补齐。

## 本机联调和凭据

`run_local.py` 从 `MY_CHUANNONG_LOCAL_CREDENTIALS` 指定的仓库外 JSON 读取 `adminUsername`、`adminPassword`、`phoneHashSecret`、`deviceKeySecret`、`piiEncryptionKey`。在新电脑为本产品单独生成配置；`piiEncryptionKey` 必须是 Fernet key。不要依赖脚本中的旧电脑默认 D 盘位置，也不要复制其他产品的凭据。设置变量后执行 `python run_local.py`，仅监听 `127.0.0.1:8792`。

生产入口、配置校验和管理端文件已包含在仓库；本次没有生产部署。正式证书指纹通过 `MY_CHUANNONG_SIGNING_CERTIFICATE_SHA256` 外部配置，缺失时拒绝发布，不能拿 Debug 指纹冒充正式签名。

SQLite 库、uploads、backups、日志、`.env`、私钥和运行配置不进入 Git。学校 Cookie、教务账号密码、课表和成绩绝不上传本产品后台。

Android 主仓库：[HEltDeng/my-chuannong-android](https://github.com/HEltDeng/my-chuannong-android)。[本次 APK 与两个仓库提交对应清单](https://github.com/HEltDeng/my-chuannong-android/releases/tag/preview9-audited-20261013)均在该私有仓库中。
