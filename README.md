# astrbot_plugin_wanxiao_electricity

AstrBot 插件：查询多个完美校园账号中已绑定房间的水电信息。

## 环境要求

需要 AstrBot `>=4.10.4`，以支持插件配置中的 `template_list` 多账号字段。

## 配置

在 AstrBot 插件配置页的“完美校园账号”中添加一个或多个账号。每项包含可选备注、启用开关、学校代码和学号；学校代码与学号必须按字符串填写，以保留前导零。

```json
{
  "accounts": [
    {
      "__template_key": "wanxiao_account",
      "name": "宿舍",
      "enabled": true,
      "school_code": "100",
      "student_account": "2024000001"
    },
    {
      "__template_key": "wanxiao_account",
      "name": "家人",
      "enabled": false,
      "school_code": "100",
      "student_account": "2024000002"
    }
  ]
}
```

未填写任何有效账号时插件仍可加载；执行命令会提示管理员完成配置。

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
