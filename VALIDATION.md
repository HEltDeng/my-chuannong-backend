# 内部预览验证记录

日期：2026-10-01。交付 APK SHA-256：DDC90156AB6F166A3A629EB1822A2CA9CA1795FEABA5F661F2C489B40F764E19。签名证书 SHA-256：A8F8EAC6FF810CAC0B21CBD59A08D5525E1ACA9C225F86F516FBD1172BECDF6D。两者与 Git Commit SHA 含义不同。

- PASS：Gradle assembleRelease / lintVitalRelease；独立包名、版本、签名校验；APK 解压扫描没有独立签名私钥、管理员密码和旧运行时域名。
- PASS_EMULATOR：Android 16 API36 模拟器安装、冷启动、首页/服务/个人中心/设置交互、公众号名称复制。安装后从设备读取的 APK 与交付哈希一致。
- PASS_EMULATOR：同时安装 com.mychuannong.app 和 com.mychangli.app，UID 10216 与 10217 不同。只启动川农，未运行长理或连接其生产服务。
- PASS_EMULATOR：同一 APK 配置 13→14→15，课表名称由“我的课表”到“课表入口”再恢复。无重新构建/安装，三张截图与 config-emulator.json 记录。
- PASS：独立后端19项本地接口测试，包括认证、Origin、产品隔离、固定URL/模式、回滚、空列表、关闭更新和会话失效。
- PASS：管理后台实际浏览器登录、概览/配置可用，截图位于项目 05_Output/screenshots。
- 教务：公开本机探测与模拟器访问失败，App显示失败并保留刷新/返回/关闭/外部浏览器入口。不是教务查询成功。学校认证和缴费公开页200不代表真实学校账号流程通过。
- PENDING_REAL_DEVICE：没有连接USB手机；模拟器不能替代真实手机验收。
- PENDING_REAL_ACCOUNT：学校登录、校园网、课表/成绩/考试深层页、微信及完美校园实际业务未验证。
- PENDING_CONTENT：地图附件图面/时效、校历适用学年、完整电精灵业务路径等。

源码归档进入独立预览开发分支，不进入正式 Release。没有Tag、GitHub Release、公开下载、生产部署或Store Release。

补充：最终APK学校统一认证页面在模拟器WebView正常呈现短信/密码登录界面；没有输入账号、验证码、发送短信或完成认证。教务页面失败状态及刷新/关闭经过操作。独立后台停止后，配置刷新失败仍保留服务清单，首页/服务/关于/隐私/官网筹备页继续可用；后台随后恢复。
