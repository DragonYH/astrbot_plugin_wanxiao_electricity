# astrbot_plugin_wanxiao_electricity

AstrBot 插件：查询完美校园中当前账号已绑定房间的水电信息。

## 配置

在 AstrBot 插件配置页填写以下字符串配置。两项都保留为字符串，以免丢失前导零。

```json
{
  "school_code": "学校代码",
  "student_account": "学号"
}
```

未填写配置时插件仍可加载；执行命令会提示管理员完成配置。

## 使用

管理员发送：

```text
查水电
```

插件会先读取该账号绑定的房间，再依次查询每个房间的水费和电费。多房间时，某个房间查询失败不会阻断其他房间结果。

## 依赖

运行环境需要安装：

```text
aiohttp>=3.8,<4
```

## 参考

- [AstrBot 插件开发指南](https://docs.astrbot.app/dev/star/plugin-new.html)
- [完美校园 API 文档](https://s.apifox.cn/ad1e6ba6-6cd2-4b6e-88b2-a1ca1577e14c/folder-41557094)
