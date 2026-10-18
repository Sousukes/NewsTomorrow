## 批次 P1-53（2026-10-18）：D1-1 第一张表迁移 + 踩中 SQLite CAST 陷阱

把 `rss_source_health_events.at` 改成 `DateTime(timezone=True)`（迁移 0030）。
它是**唯一**参与 SQL 侧范围过滤的时间列，一张表就能把 D1-1 的全部经验试完。

## 本批最重要的事：迁移一度静默毁数据

第一版迁移用了 Alembic 最常见的写法：

```python
with op.batch_alter_table(TABLE) as batch:
    batch.alter_column("at", type_=DateTime(timezone=True), existing_type=String(30))
```

在 SQLite 上它用 `CAST(旧列 AS 新类型)` 拷贝，而 SQLite 把**以数字开头的字符串
当数字**：

```sql
SELECT CAST('2026-10-18T06:00:00+00:00' AS DATETIME)  -->  2026
```

整列时间被截成了年份。**它不报错、行数不变、排序看着还正常**——因为两行都
变成 2026 之后相对次序恰好没变。真实的迁移验证第一次跑完时，我看到的正是
"3 行数据都在、顺序正确"，差点就提交了。

## 为什么会漏：上一批的实验给了假信心

P1-51 的实验断言的是「转换后 `ORDER BY at DESC` 顺序仍等价于时间序」——而
CAST 毁数据**恰好满足**这个断言。实验本身设计得不够狠：**只断言顺序，不断言值**。

已把这条教训写进测试（`TestSqliteCastTrap`，2 项）：
- 直接断言 `CAST(...)` 的结果是 `2026`（把陷阱钉死）；
- 断言 `batch_alter_table` 之后**值长度 < 19**（毁数据的事实本身）。

若哪天 SQLite 行为变了，这两条会红并提示"可以把 0030 换回简单写法"——
护栏双向可用，不是只朝一个方向报警。

## 正确路径：显式重建 + Python 逐行转换

```
反射旧表 → 建新表（只把 at 换成 DateTime）→ Python 读旧值、parse_dt 解析后写入
→ 删旧表改名 → 补回索引
```

宁可慢，也不能让数据库猜时间怎么转。实测：3 行全部转换、值完整
（`2026-10-18 06:00:00.000000`）、排序正确、其它列与索引未受影响。

顺带修掉一个自己写出的第二个错误：迁移里在 alembic 已开启事务的连接上又调
`bind.begin()` → `InvalidRequestError`，**迁移中止、数据没转换**。而当时我的
验证脚本只检查"行还在、顺序对"，于是又是一次**假通过**。教训相同：
验证必须断言**转换确实发生了**，而不只是结果看起来合理。

## 顺带确立的三条边界约定

1. **写入口径分流**：`now_dt()`（DateTime 列用）与 `now_str()`（字符串列用）
   都在 `models.py` 并排写明，避免变成"看情况用"；
2. **读出边界统一**：SQLite 的 DateTime 读回**不带时区**（PG 的 timestamptz 带），
   所以进响应前一律过 `iso_utc()`，否则同一批数据在不同部署下格式不一致，
   前端 `new Date()` 的解释会随环境变化；
3. **cutoff 改参数绑定**：`_since()` 现在返回 **aware datetime** 而不是 ISO 串。
   此前靠"字典序 == 时间序"做字符串比较，列有了真实类型后继续拼字符串等于
   主动放弃类型的好处，还多一层隐式转换的出错面。

## 测试改造

`test_health_history.py` 的夹具从「塞 ISO 串」改成「塞 datetime 对象」
（SQLite 会直接拒绝字符串）。锚点 `BASE` 也改成 **aware UTC**——PG 的
timestamptz 会按**会话时区**解释 naive 值，用本地 naive 锚点会让测试随时区飘。

新增护栏 5 项（`TestDateTimeColumnBoundary`）：`iso_utc` 必带偏移、naive/aware
同值同串、非 UTC 转换而非原样带出、真实数据路径（`summarize`）出参带偏移、
`_since` 返回 aware datetime。

## 重建路径本身也踩了两次（都靠 `test_migrations` 抓住）

显式重建 = 丢掉旧表的一切定义，只搬你记得的那些。两次都是"搬漏了"：

1. **索引没了**：我按自己的理解补了个 `ix_rss_source_health_events_source_key`，
   而模型里叫 `ix_rss_health_key_at`（复合索引 `(source_key, at)`）。
   **索引名"看起来对"最危险**——真正的问题（复合索引没了）被掩盖成名字不对；
2. **外键没了**：`sa.Column(name, type, nullable=...)` 只搬列定义，
   `ON DELETE CASCADE` 不会跟着走。源被删时健康历史不清理，**没有任何症状**，
   数据只会越积越多；
3. 补回外键后又变成 `NO ACTION`——反射对象的 `ondelete` 也要显式带上。

顺带一个 SQLAlchemy 细节：`foreign_keys=[...]` 写成**关键字参数**会被当成
方言专用参数（`foreign_keys` 被拆成方言名 `foreign`，报
`Can't validate argument 'foreign_keys'`），必须用**位置参数**传 `ForeignKey`。

每一条都已写进迁移脚本注释——**重建类迁移的坑不在算法里，在于"你记得搬哪些"**。

## 回归

pytest 831 → **838** 全绿（新增 5 项边界护栏 + 实验加 2 项）。
契约双端双档全绿（71 / 284，真后端是**带 0030 迁移**起的库；比上批少一项
是因为一条断言依赖数据形态，无失败）。vitest 567 不变。
