# AgentRouter GitHub 云端自动登录

本模式使用 GitHub Actions 托管机器，不依赖个人电脑开机。
每次重新完成 GitHub OAuth，并核对 AgentRouter 用户 ID 和一份新的用户信息响应。

## 登录态与权限范围

GitHub OAuth 需要一个仍然有效的 GitHub 登录会话。首次仍需在本机独立浏览器中本人登录和完成二次验证。
使用 `agentrouter_cloud.py export` 会导出该独立浏览器中的 GitHub 和 AgentRouter Cookie。
其他网站、GitHub localStorage，以及 AgentRouter 的邮箱、访问令牌等字段不会导出。

**GitHub 的网页登录 Cookie 能代表你的 GitHub 账号，无法像 API Token 一样限制成“只许签到”。**
只有接受该权限范围后，才应将加密会话及解密密钥分别放入自己仓库的专用 Environment Secrets。
二者同时可被该环境中的工作流读取；AES 加密主要用于保护会话文件和公开仓库中的 artifact。
请只允许可信默认分支访问该 Environment，保持仓库写权限仅授予可信人员。

## 配置

1. 本机运行 `uv run python agentrouter_github.py bootstrap --name main`，本人完成 GitHub 登录。
2. 本机运行 `uv run python agentrouter_cloud.py export --name main`。
3. 在 GitHub 仓库创建 Environment：`agentrouter-cloud`，限制部署分支为默认分支。
4. 在该 Environment 添加：

| Secret | 本地文件内容 |
| --- | --- |
| `CLOUD_STATE_KEY` | `.local_state/cloud/state-key.txt` |
| `CLOUD_LOGIN_STATE` | `.local_state/cloud/initial-state.enc` |

不要把这些文件提交到 Git，也不要上传原始浏览器目录。

5. 手动运行“AgentRouter GitHub 云端签到”，勾选 preflight，先测试公共登录页面。
6. 取消 preflight 再运行一次，验证真实账号登录。
7. 成功后把仓库变量 `AGENTROUTER_CLOUD_ENABLED` 设置为 `true`，开启周期运行。

默认每 6 小时尝试一次（北京时间约 03:17、09:17、15:17、21:17），以覆盖服务器的 24 小时领取间隔和偶发失败。
GitHub Actions 可能延迟运行。登录不代表每次都能领取奖励，程序会明确区分奖励确认和未知状态。

## 更新后的会话

每次完成新 OAuth 并确认目标账号后，程序用新的随机 nonce 加密更新后的会话，保存为 `state.enc`。
Actions 仅上传此加密文件，保留 30 天。后续只恢复同一仓库、同一工作流、同一分支上最近成功运行的对应 artifact。
若没有历史 artifact，则使用初始 Secret。篡改、密钥错误、账号错误或不允许的域名会使恢复失败。

工作流的 GITHUB_TOKEN 只有 contents/actions 读取权限；它不需要写入仓库 Secret。
日志不打印 Cookie、OAuth URL、账号 ID 或余额，失败时也不会上传登录页截图或原始响应。
preflight 可上传公开的、无账号登录页面截图。

## 登录过期与站点验证

如果 GitHub 要求重新登录或二次验证，工作流退出并提示手动更新登录态。
它不会自动填写密码、验证码、授权新应用或绕过浏览器安全提示。
更新初始 Secret 时应同时更新密钥。artifact 名称包含密钥指纹，更新密钥后会自动忽略旧会话并使用新导出。
网络和验证码限制仍受 GitHub、AgentRouter 的实际策略约束；只有真实云端测试成功后才应停用本机任务。

如果公共页面测试显示 Access Verification 滑块，说明该 Runner 的访问需要人工验证。
可在专用 Environment 添加可选 `PROXY_SUBSCRIPTION_URL` Secret，使用自己的 Clash/Mihomo 订阅后重新测试。
预检和真实登录共用同一网络配置。没有可用网络出口时应保持定时开关关闭，程序不会自动完成验证码。
