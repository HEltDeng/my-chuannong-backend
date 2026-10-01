# 数据与会话边界

- applicationId com.mychuannong.app，独立 Android 沙箱、Room/MMKV、WebView Cookie。学校解绑清理本包学校会话和本地学习缓存；平台退出独立处理。
- SchoolWebActivity 只接受 HTTPS 与解析后明确主机集合；拒绝 userinfo/非443端口/证书错误，无通用 JavaScript Bridge，不伪装微信。
- SchoolSyncEngine 是唯一新的课程/成绩/考试刷新入口。SchoolReader 身份/课表/成绩/考试解析目前明确 PARSER_PENDING。页面加载结束只标记身份尚未确认。所有模拟或合成资料不进入普通安装用户数据区。
- 缓存成功与失败分开；课程刷新按明确学期在 Room 事务中更新，失败保留缓存，成绩和考试分项保存；换号/解绑清除旧账号缓存。真实身份解析和真实学校网络尚未验收。
- 本产品平台账号、公告、配置和更新通过独立本地后台；响应必须具备匹配的产品与应用标识。无母版后端/官网/latest.json 回退。正式强更关闭。
- 凭据在 D:/Codex-Local-Secrets/MyChuannong，不在仓库。学校密码在学校页面输入，本产品不保存密码。未配置短信/邮件/推送，没有模拟发送成功。
- 非四川农业大学官方 App；临时绿色属于产品主题，不声称学校官方标准色。
