## 批次 P1-58（F5 第二批）：深链组合式与 API 层补测 + 修两个真 bug

补掉 12 个 0% 里的三个（组合式 + 两个 API 层，28 项），并修了两个由测试暴露的真问题。

## 修了什么（测试先暴露、后修复）

1. **`useNewsDetail` 的 `failed` 语义是空的**：`loadDetail` 抛错时既不置 `failed`
   （`finally` 只复位 `resolving`），错误还会变成 **unhandled rejection**
   （`onMounted` 里没人 await 它）。结果是网络失败时视图落到 `v-else` 的
   「内容不存在或已下架」——**把临时故障说成了下架**。
   修法：`catch` 里置 `failed = true`，并把 `failed` 接进两个视图，文案分开
   （「加载失败，请稍后重试」 vs 「内容不存在或已下架」）。
2. **未读数可能显示 NaN**：`Number(data ?? 0)` 在后端返回对象/非数字时得到 `NaN`，
   角标会渲染成 NaN——比不显示更让用户以为坏了。改成
   `Number.isFinite(n) && n > 0 ? Math.floor(n) : 0`。

## 补了什么

| 文件 | 项数 | 覆盖 | 核心断言 |
| --- | --- | --- | --- |
| `composables/useNewsDetail.ts` | 8 | 0% → **100%** | 内存有则不发请求；后端说没有 → `failed`；**请求失败也 `failed`**（不能误报已下架）；`resolving` 必复位；id 变化重新解析；`reload` 能从失败态恢复 |
| `api/comments.ts` | 10 | 0% → 85.2% | **裸数组与 `{list}` 两种响应都能吃**；缺 `hasMore`/`nextCursor` 归一到没有更多（否则无限翻页）；`normalize` 缺失字段兜底 |
| `api/messages.ts` | 10 | 0% → 90.9% | 同上三条；**未读数非数字归 0**；`read-all` 必须 `POST /messages/read-all`（路径错会 404，用户只看到点了没反应） |

API 层那两个「兼容两种响应形态」的断言不是形式主义：它是**后端契约变化时的缓冲带**
——一旦接口从 `{list}` 改成裸数组（或反之），前端不会整页崩，而是照常工作。

## 覆盖率

| | P1-57 | P1-58 |
| --- | --- | --- |
| statements | 73.39% | **75.11%** |
| branches | 66.37% | **68.69%** |
| functions | 62.00% | **63.53%** |
| lines | 76.30% | **78.29%** |
| 0% 文件数 | 12 | **9** |

阈值再抬到 **74/67/62/77**（继续锁住收益）。

## 剩余 9 个 0% 文件

- 组件：`StepUpDialog.vue`、`PhoneFrame.vue`、`App.vue`
- 视图：`Gallery / History / Messages / Search / Showcase / Videos`（= F3' 剩余项）

两条测试写错的教训（都被拦住）：`getById` 在 init 与 `onMounted` 里各读一次，
`mockReturnValueOnce` 只喂第一次；`setCommentLike` 是 `PATCH` 不是 `PUT`
——**断言要按真实实现写，不能凭印象**。

## 回归

vitest 593 → **621** 全绿；`test:cov` 全部高于新阈值；`verify` 与 `build` 通过。
