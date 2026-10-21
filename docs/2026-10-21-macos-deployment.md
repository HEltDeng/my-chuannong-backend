# Mac 接手与生产部署

用户于 2026-10-21 在 Mac 明确授权部署川农独立后台，使用现有域名的子域名，并要求全新干净数据库。此授权接续此前仅上传源码的交付阶段。

GitHub 基线：`61db65f7661b0ae9a9cff3e4cb59293ad17b7a73`，分支 `codex/release-1.0.0`。通过已登录的 GitHub 下载源码 ZIP，并核验 ZIP 注释中的提交及原源码清单。Mac 当前 Git 为该归档的本地快照；原远端历史未修改，不能将本地快照分支直接合并覆盖远端历史。

部署时仅将后台界面中的“本地联调”“本地管理后台”等固定环境文案改为通用运营文案；API、账号、权限、业务模型及冻结功能未修改。源码清单中对应三个文件的摘要同步更新，其余基线摘要保留。

- API：https://chuannong-api.mychangliapp.cn/api/v1/
- 管理：https://chuannong-admin.mychangliapp.cn/admin/
- 文件：https://chuannong-files.mychangliapp.cn/
- 独立服务：`mychuannong-backend.service`，监听 `127.0.0.1:8792`。
- 运行配置、数据库、上传文件、日志和备份均位于代码目录外。
- 初始化仅包含必要系统配置、草稿版本和首个超级管理员，不导入测试账号和业务记录。

Mac 验证：97 项后台测试通过；生产域名完成管理员登录、退出、资料列表及公告列表等 HTTPS 检查。实际部署记录及运维入口见上层开发目录的 README。
