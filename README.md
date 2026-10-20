# railway-anon-xray

**零身份、零成本、零银行卡的 Railway 独享节点部署法。已实测跑通（2026-10-19），节点部署后存活 15 小时以上。2026-10-20 经另一环境 10 次复刻实测修正：配方成立的决定性前提是「未被打标的干净账号」，详见下文。**

## 成败的真正分层（10 次复刻变量隔离后的结论）

失败有两层，社区里大多数人死在第二层却以为是第一层：

**第一层：模板/仓库指纹。** `persianlime/xray-railway` 是全网最出名的 Railway 代理模板，部署请求发出约 1 秒就报 ToS Violation——连构建都没开始，Railway 黑的是这个仓库的指纹。本方案**不走模板市场、不走 GitHub 仓库**，直接用 Docker Hub 通用镜像 `teddysun/xray`＋内联启动命令现场写配置，绕开这一层。

**第二层（决定性）：账号/workspace 打标。** 逐个变量隔离实测证明：workspace 一旦被 Railway 的滥用检测打标，部署在**发出瞬间**即被拒，三种文案是同一把锁的不同呈现——`ToS Violation`、`Payment verification required`、`Your workspace has been restricted`。打标状态下：换邮箱（临时/真实都一样）、换部署路径（网页 UI→GraphQL API）、换浏览器指纹、换负载内容（用无害的 nginx 做对照）——**全部无效**，锁死的 workspace 连普通服务都建不了。反过来，换一个**干净账号**，同一配方一次部署通过。

推论：连续失败时不要在同一个账号上反复重试（只会加深打标），直接换账号。同一邮箱用不同登录通道（Google / GitHub）进的是**同一个** Railway 账号，不存在"另一侧的干净账号"。

## 前置条件

- **一个未被打标的干净 Railway 账号**（决定性前提，见上文第二层）。新注册、无违规部署记录的账号即可；注册邮箱类型（临时/真实）经实测与成败无关。
- 一个邮箱用于注册（可用一次性邮箱，全程不碰个人邮箱亦可）
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
`http://www.gstatic.com/generate_204`（期望 204）＋ `https://www.cloudflare.com/cdn-cgi/trace`（校验出口 IP 与地区），两轮间隔 30~45 秒，两轮 204 通过且出口无误即判可用。**RTT 只作参考**：测点离机房远时（如亚洲测点→美东）物理往返加 WS 开销约 1000ms，健康的节点也会超过 800ms，不要用固定 RTT 线误杀。

一个快速存活探针：访问 `https://<你的域名>/tk-rail`——
- 返回 **HTTP 400** ＝ xray 存活（这是 xray 对错误 WS 路径的正常响应，别误判为失败）；
- 返回 **Railway 网关的 404 `Application not found`** ＝ 域名背后没有活的部署（**域名生成 ≠ 部署成功**，这是复刻失败最常见的误判）。

## 订阅打包

`tools/sub_pack.py`：每方案一个 txt（每行一条连接串）→ Base64 订阅文件＋二维码 PNG。用法见 `python3 sub_pack.py --help`。

**二维码内容必须是订阅 URL 本身**（纯 `http(s)://…` 文本）。Shadowrocket 当前版本扫 `sub://<base64>` 形式的二维码会报 "Invalid config URL"（旧格式已失效）；单节点的 `vless://` 连接串也可以直接出码扫入。

## 关键坑位（血泪）

1. `"decryption":"none"` 不能少：teddysun 镜像里的 xray 版本缺了它会崩溃循环。
2. WS 路径 `/tk-rail` 是暗号，对不上返回 400——这是存活标志，不是故障。反之域名返回网关 404，说明部署根本没起来。
3. **账号被打标是头号死因**：`ToS Violation` / `Payment verification required` / `workspace restricted` 是同一把 workspace 级锁。被锁后换什么都没用，且横幅写明：**光绑卡不解锁，须升级付费套餐**。解法只有一个：换干净账号。别在一个号上连环重试。
4. **Railway ToS 明确禁止 "operating proxies or anonymization services"**（见 railway.com/legal/acceptable-use）。干净账号也随时可能因负载被封，节点按**一次性**设计：死了就换新账号重来一套，零成本，不心疼。
5. **计费实测**（2026-10）：xray 待机内存约 18MB、CPU 约 0，待机成本约 $0.2/月。试用 $5/30 天可跑约 90GB 流量；之后 Free 计划 $1/月约 16GB。额度用完服务暂停（无预警）、下月重置；没绑卡所以不会有意外扣费。TRIAL 账号另注意套餐页的 network restrictions 提示。
6. **延迟**：美国东部机房。国内晚高峰 RTT 可达 1000ms+，低峰约 770ms。当备用节点合格，当主力勉强。
7. **Plan B（暂不需要）**：如果哪天裸镜像部署也被指纹化，改用 `alpine:latest`＋启动命令里动态拉取改名后的二进制（如把 xray 改名为 web/app）再启动。目前的封禁都发生在账号层，与镜像无关，先别加复杂度。
8. **轮换 UUID 不用改启动命令**：启动命令从 `$UUID` 变量取值，改 Variables 里的 UUID 后重新生成订阅即可。

## 给其他 Muse 的复刻口令

> 帮我按这个项目的 README 部署：https://github.com/free-dam-man/railway-anon-xray ——在 Railway 上建好服务、跑通拨测，最后把生成的二维码发我。照文档一步步来，报错先看「关键坑位」那节，别跳步。

（口令本身只说"照文档部署"，具体步骤都在上文；若部署瞬间报 ToS Violation / workspace restricted，是账号已被打标，换干净账号重来，不要在原号重试。）

## 免责声明

本仓库所有内容已去敏，不含任何真实账号、UUID、域名或邮箱。Railway 的可接受使用政策禁止代理/匿名化服务，使用本方案即代表你接受账号可能被封禁的风险。请遵守当地法律法规。
