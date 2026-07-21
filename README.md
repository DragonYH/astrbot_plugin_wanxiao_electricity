# astrbot_plugin_wanxiao_electricity

AstrBot 插件：查询多个完美校园账号中已绑定房间的水电信息。

## 环境要求

需要 AstrBot `>=4.10.4`，以支持插件配置中的 `template_list` 多账号字段。

## 配置

在 AstrBot 插件配置页的“完美校园账号”中添加一个或多个账号。每项包含可选备注、启用开关、完整学校名称和学号。学校代码由插件从内置学校表自动解析，不会在 AstrBot 前端配置中显示或要求填写；学号请按字符串填写，以保留前导零。

```json
{
  "accounts": [
    {
      "__template_key": "wanxiao_account",
      "name": "宿舍",
      "enabled": true,
      "school_name": "郑州大学",
      "student_account": "2024000001"
    },
    {
      "__template_key": "wanxiao_account",
      "name": "家人",
      "enabled": false,
      "school_name": "华东师范大学",
      "student_account": "2024000002"
    }
  ]
}
```

从旧版迁移时，将每个 `accounts[].school_code` 替换为 `accounts[].school_name`，并填写完整学校名称。名称只进行全半角、空白和大小写规范化后精确匹配，不会猜测简称或相近名称。旧版仅含 `school_code` 的已启用账号会提示迁移。

未填写任何有效账号时插件仍可加载；执行命令会提示管理员完成配置。

### 学校表

学校名称与代码是对完美校园曾公开的 [`kdword_fl02.html`](https://open.17wanxiao.com/kdword_fl02.html) 中名称-编码事实独立结构化生成的本地快照；该官方地址当前已不可用。用于复核这一历史事实的镜像固定为 [`school-list.md` revision `f258c6438040f03dbcfb909c7b705c970d976ea3`](https://github.com/zuwei522/perfect-campus_electricity-alert/blob/f258c6438040f03dbcfb909c7b705c970d976ea3/school-list.md)，其原始内容 SHA-256 为 `d1a069ddd4f91235ad1110d42a575bd1774646eb99ab64379796c5b153ba121c`，并以 [Apifox 转录](https://apifox.com/apidoc/shared/38ff3833-9d57-42f3-9cd5-ffeaef43be3a) 核验。完整固定 URL、Git blob SHA、内容摘要和精确记录数也保存在 `school_codes.json` 元数据中；加载时会严格校验实际记录数与其中的 `record_count`（358）一致。运行时不会访问官方地址、镜像或任何在线查询服务；引用镜像仅为可复核来源，不声称镜像拥有许可证，也不表示本仓库获得其授权。学校名单发生变化时，需要由维护者核验来源并更新快照。

## 使用

管理员发送：

```text
查水电
```

插件会先读取每个已启用账号绑定的房间，再查询每个房间的水费和电费。结果按配置顺序分组展示，账号标题带稳定序号且只显示脱敏的学号后四位；即使备注和后四位相同也可以区分。多个命令同时执行时会排队，插件全局最多同时查询两个账号。单个账号没有绑定房间或服务暂时不可用时，会保留该账号的状态并继续查询其他账号。单个账号下某个房间查询失败同样不会阻断其余房间结果。

## 依赖

运行环境需要安装：

```text
aiohttp>=3.8,<4
```

## 参考

- [AstrBot 插件开发指南](https://docs.astrbot.app/dev/star/plugin-new.html)
- [AstrBot 插件配置指南](https://docs.astrbot.app/dev/star/guides/plugin-config.html)
- [完美校园 API 文档](https://s.apifox.cn/ad1e6ba6-6cd2-4b6e-88b2-a1ca1577e14c/folder-41557094)
