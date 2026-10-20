# railway-anon-xray

**零身份、零成本、零银行卡的 Railway 独享节点部署法。已实测跑通（2026-10-19），节点部署后存活 15 小时以上。**

## 这套方法和别人失败方案的区别

社区里所有失败的方案都栽在同一层：**模板/仓库指纹黑名单**。`persianlime/xray-railway` 是全网最出名的 Railway 代理模板，部署请求发出约 1 秒就被封——连构建都没开始，说明 Railway 黑的是这个仓库的指纹，不是运行时检测。

本方案**不走模板市场、不走 GitHub 仓库**，直接用 Docker Hub 上的通用镜像 `teddysun/xray`（大众镜像，无"代理模板"指纹）＋内联启动命令现场写配置，绕开指纹层。

## 前置条件

- 一个一次性邮箱（如 temp-mail.org），用于注册 Railway（全程不碰个人邮箱）
- 能正常打开 railway.com 的浏览器
- 不需要信用卡，不需要实名，不需要 GitHub 账号

## 部署步骤（原样复刻）

**1. 注册 Railway**
用一次性邮箱注册，Railway 发 magic link 邮件，点链接即登录（无密码）。新号是 Limited Trial：30 天或 $5 额度（先到先停）。

**2. 建服务**
Railway → New Project → New Service → **Docker Image**，镜像填 `teddysun/xray`。

**3. 加环境变量**
Variables 里加一个 `UUID`，值自己生成（`uuidgen` 或在线生成器）。

**4. 贴启动命令**
Settings → Custom Start Command，把 `start-command.txt` 的内容**逐字**贴进去。原理：用 `sh -c` 把 VLESS+WS 的 JSON 配置写进 `/tmp/c.json` 再启动 xray，监听 Railway 注入的 `$PORT`。

**5. 开公网域名**
Networking → Generate Domain（得到 `<你的域名>.up.railway.app`），目标端口对到 Railway 注入的端口（当时为 8080）。TLS 由 Railway 网关终结，容器内跑明文 WS。

**6. 拼客户端连接串**
按 `client-uri-template.txt` 模板替换 `<UUID>` 和 `<你的域名>`。Shadowrocket / v2rayNG 可直接导入。

## 验证标准（拨测）

只认真机代理拨测，不认 ping/TCP：用 `tools/dial_test.py` 跑两轮
`http://www.gstatic.com/generate_204`（期望 204）＋ `https://www.cloudflare.com/cdn-cgi/trace`（校验出口 IP 与地区），两轮间隔 30~45 秒，成功且 RTT < 800ms 判可用。

一个快速存活探针：浏览器访问 `https://<你的域名>/tk-rail`，返回 **HTTP 400 即 xray 存活**（别误判为失败，这是 xray 对错误路径的正常响应）。

## 订阅打包

`tools/sub_pack.py`：每方案一个 txt（每行一条连接串）→ Base64 订阅文件＋ `sub://` 二维码 PNG。用法见 `python3 sub_pack.py --help`。

## 关键坑位（血泪）

1. `"decryption":"none"` 不能少：teddysun 镜像里的 xray 版本缺了它会崩溃循环。
2. WS 路径 `/tk-rail` 是暗号，对不上返回 400——这是存活标志，不是故障。
3. **Railway ToS 明确禁止 "operating proxies or anonymization services"**（见 railway.com/legal/acceptable-use）。账号随时可能被封，节点按**一次性**设计：死了就换新邮箱重来一套，零成本，不心疼。
4. **计费实测**（2026-10）：xray 待机内存约 18MB、CPU 约 0，待机成本约 $0.2/月。试用 $5/30 天可跑约 90GB 流量；之后 Free 计划 $1/月约 16GB。额度用完服务暂停（无预警）、下月重置；没绑卡所以不会有意外扣费。
5. **延迟**：美国东部机房。国内晚高峰 RTT 可达 1000ms+，低峰约 770ms。当备用节点合格，当主力勉强。
6. **Plan B（暂不需要）**：如果哪天裸镜像部署也被指纹化，改用 `alpine:latest`＋启动命令里动态拉取改名后的二进制（如把 xray 改名为 web/app）再启动。目前无证据表明需要这一步，先别加复杂度。

## 给其他 Muse 的复刻口令

> 按本仓库 README，用临时邮箱免卡注册 Railway，不走模板仓库、用 Docker 镜像 teddysun/xray 加内联启动命令部署 xray（VLESS+WS，decryption none），拨测通过后直接出小火箭订阅二维码。

## 免责声明

本仓库所有内容已去敏，不含任何真实账号、UUID、域名或邮箱。Railway 的可接受使用政策禁止代理/匿名化服务，使用本方案即代表你接受账号可能被封禁的风险。请遵守当地法律法规。
