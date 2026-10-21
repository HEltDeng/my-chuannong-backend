# 我的川农后台 1.0.0

配套 Android **1.0.0** 的冻结源码，交付分支 `codex/release-1.0.0`。沿用现有 Python + SQLite API 和 HTML/CSS/JavaScript 管理后台，本轮包含已完成的新功能，不新建另一套后台。源码上传不包含腾讯云部署、官网发布或运行数据迁移。

新电脑接手见 [START_HERE.md](START_HERE.md)。客户端：[HEltDeng/my-chuannong-android](https://github.com/HEltDeng/my-chuannong-android/tree/codex/release-1.0.0)。

本次清洗、测试和剩余限制见 [1.0.0 源码审计](docs/2026-10-21-source-audit.md)。生产版本下载地址须为公网 HTTPS 443 直链，不能重定向；安装包最大 512 MiB，并校验声明大小和 SHA-256。

## 功能

- 我的川农平台账号注册、登录、会话、资料、退出与权限。
- 二手出售/求购、失物寻物/招领的审核、原因通知、完成状态、删除同步、筛选和完整图片预览。
- 驾校、雅思托福、考公考研、健身房的机构课程资料、预约意向及处理结果。
- 公告与服务动态、校园指南、资料库、功能开关、配置激活回滚、版本管理与审计。
- 数据库备份、完整性校验及离线恢复工具。

接口与联调范围见 [后台接入记录](docs/2026-10-19-campus-admin.md)、[二手审核记录](docs/2026-10-19-marketplace-review.md)、[失物审核记录](docs/2026-10-19-lostfound-review.md)。这些历史联调记录不等于生产部署验收。

## 配置和运行

Python 依赖使用 `requirements.lock.txt`。在仓库外建立本产品独立凭据 JSON，通过 `MY_CHUANNONG_LOCAL_CREDENTIALS` 指定；字段格式参照不含真实密码的 `credentials.example.json`。

```text
python -m pip install -r requirements.lock.txt
python -m unittest discover -s tests -v
python run_local.py
```

本地入口只监听 `127.0.0.1:8792`，管理页 `/admin/`，健康检查 `/health`。运行库仍为 `data/my-chuannong-v105.db`，保留路径以免原有数据被意外替换。新建库不包含示例公告、商家、商品或场地；必要配置和 1.0.0 草稿版本自动初始化，更新及强更关闭。已有库与备份不因源码清理被删除或重建。

生产入口为 `wsgi.py`，生产配置校验见 `production.py` 与 `runtime_config.py`。HTTPS 域名和腾讯云部署由用户后续安排；手机不能把本机 `127.0.0.1` 当作公网后台。正式 APK 发布仍须配置核验后的 `MY_CHUANNONG_SIGNING_CERTIFICATE_SHA256` 并通过包名、摘要、大小及元数据检查。

学校登录、VPN 与教务数据在手机直接处理，后台不接收学校密码、Cookie、课表和成绩。运行库、上传文件、备份、日志、私钥、JKS 和真实凭据不进入 Git。
