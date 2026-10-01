# 我的川农 Backend/Admin 内部预览

独立 Python 标准库 WSGI + SQLite 本地服务，与母版技术路线一致。保留母版 RateLimiter 与 Admin 样式，按本轮最小职责重建产品配置与独立管理员会话；不复制母版完整业务数据库、部署脚本或生产配置。当前支持服务配置、版本查询、配置历史回滚、审计、公告/失物/资料空列表及本地管理界面。尚未开放用户注册、内容发布或真实运营。

## 启动

```powershell
python -m venv .venv
.\start-local.ps1
```

默认从 `D:\Codex-Local-Secrets\MyChuannong\backend-local.json` 读取本产品凭据，也可通过 `MY_CHUANNONG_BACKEND_CONFIG` 指定独立受限本地文件。不能使用其他产品的凭据。管理员可读说明在同一受限目录。

浏览器访问 http://127.0.0.1:8791/admin/ 。只监听回环地址，不开放局域网、不部署生产。内置 WSGI 服务适用于本地预览。

数据库 `data/my-chuannong.db` 首次启动新建。管理员密码 PBKDF2-SHA256/300000 次，随机会话令牌仅哈希入库，1小时过期，Cookie `my_chuannong_admin` / HttpOnly / SameSite=Strict；本机HTTP不设置Secure，仅回环使用。严格 Host/Origin 检查；无跨域授权。`sessionSecret` 已独立生成预留，当前采用数据库随机会话，不使用JWT。无外部缓存、对象存储、容器、定时任务、消息队列、短信、邮件或推送。

公开接口：/health、/ready、/api/v1/public/config、version、announcements、lost、resources。管理员接口均位于 /api/v1/admin/。不提供学校账号、Cookie 或学生页面上传接口。配置只能调整已审核服务名称和可见性，回滚生成新版本。当前不提供正式 APK 更新。

管理后台可登录、查看独立空数据、调整服务清单、保存配置和回滚、查看审计。保留原样式结构但不展示未迁移的体育预约、生产数据治理等菜单。
