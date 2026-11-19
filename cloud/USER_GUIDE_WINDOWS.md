# MD-agent API · Windows 用户使用说明

**两种方式**：①装 `mdagent-client` 客户端（推荐，Codex 式体验，智能体可经你批准读写你本机文件）；②直接 curl 调 API（本说明下半部分）。

客户端有**网页工作台版**（浏览器打开即用，图形界面）和**终端版**两种，安装都只需一条命令：

```powershell
# 一键安装(推荐):下载安装脚本再执行,装完自动打开浏览器工作台
curl.exe -k -H "Authorization: Bearer mda_你的token" "https://<SERVER-IP>/mdagent/v1/install.ps1" -o mdagent_install.ps1
powershell -ExecutionPolicy Bypass -File mdagent_install.ps1

# 或手动装网页工作台版:
curl.exe -k -H "Authorization: Bearer mda_你的token" "https://<SERVER-IP>/mdagent/v1/webclient.py" -o mdagent_web.py
python mdagent_web.py --token mda_你的token

# 终端版(无浏览器环境用):
curl.exe -k -H "Authorization: Bearer mda_你的token" "https://<SERVER-IP>/mdagent/v1/client.py" -o mdagent_client.py
python mdagent_client.py --token mda_你的token
```

客户端详细用法见仓库 `mdagent-client/README.md`。

以下为纯 API 方式（无需安装任何东西，Windows 10/11 自带 `curl.exe`）。

服务地址：`https://<SERVER-IP>/mdagent`
认证方式：请求头 `Authorization: Bearer <你的token>`（token 不要写进 URL）

---

## 第一步：申请账号（一次性）

账号为审批制。你需要先有一个自己的大模型 API Key（三选一）：

| provider 取值 | 说明 | 申请处 |
|---|---|---|
| `kimi-coding` | 月之暗面 Kimi | platform.moonshot.cn |
| `bigmodel-anthropic` | 智谱 GLM | open.bigmodel.cn |
| `zai-coding-cn` | 智谱 Z.AI 编码套餐 | z.ai |

打开 **PowerShell**，执行（把示例值换成你的）：

```powershell
curl.exe -k -X POST "https://<SERVER-IP>/mdagent/v1/apply" `
  -H "Content-Type: application/json" `
  -d "{\"username\":\"你的名字拼音\",\"contact\":\"微信或邮箱\",\"reason\":\"做水凝胶模拟\",\"provider\":\"kimi-coding\",\"api_key\":\"sk-你的key\"}"
```

- username 规则：小写字母开头，3~16 位小写字母/数字/`_`/`-`
- 返回 `{"ok":true,...}` 即提交成功，**等管理员审批**
- 审批通过后管理员会把 `mda_` 开头的 token 发给你，**只显示一次，立即保存**

## 第二步：对话

```powershell
# 1) 发消息(project 是会话分组,同 project 内上下文连续)
curl.exe -k -X POST "https://<SERVER-IP>/mdagent/v1/chat" `
  -H "Authorization: Bearer mda_你的token" `
  -H "Content-Type: application/json" `
  -d "{\"project\":\"水凝胶\",\"message\":\"NPT 平衡后怎么判断收敛?\"}"
# 返回 {"job_id":"xxxx","position":1}

# 2) 轮询取结果(把 xxxx 换成 job_id,status=done 时 reply 就是回答)
curl.exe -k "https://<SERVER-IP>/mdagent/v1/jobs/xxxx" `
  -H "Authorization: Bearer mda_你的token"
```

回答通常需要 10 秒~几分钟（长任务更久），每 10 秒查一次即可。

## 其它接口

```powershell
curl.exe -k "https://<SERVER-IP>/mdagent/v1/conversations" -H "Authorization: Bearer mda_你的token"   # 历史会话
curl.exe -k "https://<SERVER-IP>/mdagent/v1/usage" -H "Authorization: Bearer mda_你的token"           # 用量
```

## 规则与注意

- 每小时最多 20 次对话，同时只能有 1 个进行中的任务
- 你的对话和记忆与他人完全隔离；进化策略层全员共享（只读）
- `-k` 是因为服务用自签证书，属正常；token 泄露请立即联系管理员吊销
- 想写脚本批量调用：任何能发 HTTPS 请求的语言都行（Python `requests.post(url, headers={"Authorization":"Bearer ..."}, verify=False)`）
