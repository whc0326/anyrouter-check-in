# AgentRouter：GitHub 账号自动重新登录（Windows）

原来的 Session Cookie 模式只查询 `/api/user/self`，不能证明触发了登录签到。
新增模式每次清除 **AgentRouter 自身** 的登录会话，通过网站的 GitHub 按钮完成一次新的 OAuth 登录，
并验证服务器返回的用户 ID。GitHub 登录态保存在本机独立浏览器目录，不读取你的日常 Chrome 配置，
也不上传到 GitHub Actions、缓存或 artifacts。

## 首次使用

在项目目录运行：

```powershell
uv sync
uv run python agentrouter_github.py bootstrap --name main
```

Windows 默认使用已安装的 Google Chrome。在新打开的浏览器里手动登录 GitHub，完成必要的二次验证。
脚本等待 AgentRouter OAuth 回调成功、返回控制台，并确认一份新的用户信息响应后自动关闭浏览器。
如需确保账号正确，可加 `--user-id 你的AgentRouter用户ID`。

之后运行：

```powershell
uv run python agentrouter_github.py run --name main
uv run python agentrouter_github.py status --name main
```

日常运行默认无窗口。若 GitHub 登录过期或需要验证码，程序退出并提示重新运行 bootstrap；
不会尝试填写密码、处理验证码或使用旧 AgentRouter Cookie 冒充一次新的登录。

## 每日自动运行

首次登录和一次 `run` 均验证成功后，在 PowerShell 中运行：

```powershell
.\scripts\install_windows_task.ps1 -AccountName main -At '09:17'
```

这会创建当前用户的 Windows 计划任务 `AgentRouter-GitHub-main`：每天本机时间 09:17 运行，
并在用户登录 Windows 时尝试补跑。任务使用最低权限，不保存 Windows 密码。
电脑需要开机、有网络并且该 Windows 用户已登录；不需要保持 Codex 打开。
关机期间不能执行。不要在尚未完成 bootstrap 时安装任务。

日志在 `logs/github-日期.log`，最新结构化结果在 `.local_state/last-result.json`。
程序返回 0 表示 OAuth 和账号验证完成，2 表示需要手动登录，其他非零值表示失败。
任务不并行执行；若进程被强制终止，确认没有任务运行后再移除 `.local_state/github.lock`。

## 接入原来的多账号入口

`.env` 中的配置必须保持单行：

```dotenv
ANYROUTER_ACCOUNTS=[{"name":"main","provider":"agentrouter","auth_method":"github"}]
```

然后运行原来的 `uv run checkin.py`。`name` 必须与 bootstrap 时一致。
本模式不需要 `email`、`password`、`cookies`。可选的 `api_user` 用于严格限制目标账号 ID。
GitHub 托管 Runner 没有本机登录态，程序会明确拒绝在那里运行此账号；原来的 AnyRouter 流程不受影响。

## 结果含义

| `checkin_status` | 含义 |
| --- | --- |
| `reward_confirmed` | 登录回调明确包含 `check_in: true`，或两次实时余额中的总额度增加 |
| `no_new_reward` | 登录回调明确包含 `check_in: false`；不能据此推断今天已经领取 |
| `login_verified_reward_unknown` | 新登录与账号身份已验证，但服务器没有足够证据证明新增额度 |

额度变化以 `quota + used_quota` 计算，避免期间 API 消耗掩盖新增额度。
不会比较不同账号的余额，也不会使用上次缓存的余额作为本次查询结果。
程序不承诺每次登录都发放额度，实际领取限制由 AgentRouter 决定。

## 网络与浏览器

如果本机需要 HTTP 代理，在 `.env` 显式配置：

```dotenv
CHECKIN_PROXY_URL=http://127.0.0.1:7890
PROVIDERS={"agentrouter":{"domain":"https://agentrouter.org","use_proxy":true}}
```

这里填本机代理地址，不是订阅链接。没有代理时程序直接连接。
可以设置 `CHECKIN_HEADLESS=false` 显示日常浏览器进行排查。
其他系统默认使用 Playwright Chromium，需要先运行 `uv run playwright install chromium`。

`.browser_profiles/` 含 GitHub 登录态，应当按密码一样保管。它已被 `.gitignore` 排除。
不要同步、提交、上传或共享该目录；本模式不要求将 GitHub 登录 Cookie 放进仓库 Secret。
