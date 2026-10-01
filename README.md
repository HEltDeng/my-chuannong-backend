# 我的川农 Backend/Admin · 完整产品恢复预览

继承母版实际 server/core/runtime/security/schema/admin 及业务模块，恢复平台账号注册/登录/资料/退出、权限、公告与服务动态、功能开关、版本查询、配置版本激活/回滚、审计、失物招领及资料等自建内容能力。未复制生产库和种子运营内容。非学校官方服务。

## 本地启动

在本仓库执行 `.venv\Scripts\python.exe run_local.py` 或 `start-local.ps1`。依赖在本项目 .venv；`requirements.txt`。凭据从环境变量 MY_CHUANNONG_LOCAL_CREDENTIALS 指向文件读取，默认 D:/Codex-Local-Secrets/MyChuannong/backend-local.json。

仅监听 `http://127.0.0.1:8792`，管理后台 `/admin/`，健康 `/health`。管理员说明存于受限目录“后台本地凭据说明.txt”，不展示密码。平台联调账号说明为同目录“平台联调账号说明.txt”，仅本地合成账号，无短信认证。

完整 schema 独立 SQLite：data/my-chuannong-v105.db。preview.1 的 data/my-chuannong.db 原样保留，因为旧精简 schema 与成熟产品不兼容；未把旧库伪称已迁移，也未导入长理数据。新库仅本地管理员、测试账号、设备联调记录与明确的预览公告。

独立管理员、phone/device 哈希密钥、PII 加密密钥、会话、上传目录和端口。没有发送真实短信/邮件/推送。学校 Cookie、密码、课表成绩不通过后台。无生产部署、无公开端口、无正式发布/强更。

App 使用 ADB reverse tcp:8792 tcp:8792；响应标注 X-Product-Id / X-Application-Id，客户端校验后才消费。动态配置及公告由恢复后的原生 UI 消费；本地公告已在模拟器服务轮播中显示。
