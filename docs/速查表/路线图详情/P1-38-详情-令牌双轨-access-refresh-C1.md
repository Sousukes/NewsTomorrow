#### P1-38 详情：令牌双轨 access / refresh（C1）
**接口形状**（登录 / 注册 / 续期三处一致，`ok()` 的 `data` 部分）：

| 字段 | 说明 |
| --- | --- |
| `token` | access，默认 30 分钟，访问业务接口 |
| `refreshToken` | refresh，默认 7 天，**只能**调 `/auth/refresh` |
| `expiresIn` | access 剩余秒数，前端据此**提前**续期（不等 401） |
| `user.isAdmin` / `user.namePendingReview` | 同前（续期时重新判定，不写死在令牌里） |

`POST /auth/refresh` 两种用法：

1. `body: { refreshToken }` —— 主路径，access 过期后靠它静默续期；
2. 不带 body，只带 `Authorization: Bearer <access>` —— 旧客户端 / 契约脚本路径。
   两种都返回**新的一对**令牌（滑动续期，活跃用户不会在 7 天时被硬登出）。
   refresh 令牌无效/过期/类型不符 → 401 + `40100`，**不回退**到 access。

**前端存储**（`api/http.ts`）：

| localStorage 键 | 内容 |
| --- | --- |
| `mingri-toutiao:token` | access |
| `mingri-toutiao:refresh-token` | refresh |
| `mingri-toutiao:token-exp` | access 到期时刻（epoch ms），0/缺省 = 不主动续期 |

三个键由 `setSession()` 同写、`clearSession()` 同清（登出与 401 兜底都走后者）。

**已知取舍**：refresh 令牌**未做一次性重放检测**（无状态，换发后旧 refresh 在
自身过期前仍可用）。要做检测需引入令牌表或 jti 黑名单，属另一笔债
（收益取决于是否已有多端共享凭据的场景）；当前靠「登出自增 `token_version`」
提供即时吊销能力。

**测试要点**：类型隔离（refresh 访问业务接口 401）、有效期等于对应配置、
过期 access 换不出新令牌、过期 access + 有效 refresh 仍能续期、登出同时吊销两类。

