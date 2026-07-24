# WeChat Insight Windows 便携版

整个目录复制到另一台 Windows 电脑即可运行，不需要安装 Python、pip、Git
或项目依赖。包内自带 python.org 官方签名的嵌入式运行时。目标电脑仍需安装
并登录 Windows 微信 4.x。

## 安装开机后台采集

在便携包的 `portable` 目录中运行：

```powershell
powershell -ExecutionPolicy Bypass -File .\install-autostart.ps1
```

系统会显示一次 UAC 管理员确认。确认后会注册名为
`WeChat Insight Collector` 的登录启动计划任务，并立即启动无窗口后台采集器。

第一次运行时：

1. 检查 `%LOCALAPPDATA%\wechat-insight\wechat-keys.json`。
2. key 不存在或无效时自动执行 `setup`。
3. setup 获取 key 后执行一次全量导出。
4. 后续每 60 秒导出最近两天消息，并只上报未成功上报过的消息。

## 配置局域网上报

安装时可直接指定接口：

```powershell
powershell -ExecutionPolicy Bypass -File .\install-autostart.ps1 `
  -ReportUrl "http://192.168.1.100:8080/api/wechat/messages" `
  -ReportToken "your-token"
```

也可编辑：

`%LOCALAPPDATA%\wechat-insight\wechat-insight.json`

支持字段见 `agent-config.example.json`。`lan_report_url` 留空时只采集到本地，
不会上报。

请求方法为 `POST`，请求体：

```json
{
  "schema_version": "wechat-insight.lan.v1",
  "device_name": "PC-NAME",
  "generated_at": "2026-07-23T16:00:00Z",
  "message_count": 1,
  "messages": []
}
```

接口返回任意 2xx 状态即视为成功。配置 token 时会发送
`Authorization: Bearer <token>`。

## 常用位置

- 本地消息：`%LOCALAPPDATA%\wechat-insight\data`
- 配置：`%LOCALAPPDATA%\wechat-insight\wechat-insight.json`
- key：`%LOCALAPPDATA%\wechat-insight\wechat-keys.json`
- 后台状态：`%LOCALAPPDATA%\wechat-insight\agent-state.json`
- 日志：`%LOCALAPPDATA%\wechat-insight\logs\agent.log`

卸载开机任务：

```powershell
powershell -ExecutionPolicy Bypass -File .\uninstall-autostart.ps1
```

卸载不会删除 key、配置、聊天记录或日志。
