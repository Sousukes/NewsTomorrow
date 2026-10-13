"""pytest 全局环境固定：在**任何测试模块导入 app 之前**生效。

为什么需要它：
    各测试文件此前各自在模块顶部写 `os.environ[...]`，而这些值由 `app.config`
    **在首次 import 时读取**。于是"哪个文件先被导入"决定了最终配置——
    先导入者胜出，后导入者的设置全部静默失效。表现为
    `pytest tests/test_sources.py tests/test_api.py` 让 `test_refresh_admin_accepted`
    拿到 403（管理员号段从未生效），而默认字母序全量跑却是绿的。
    本文件让结果**与收集顺序无关**。

为何用直接赋值而非 `setdefault`：测试绝不能被开发者本机的环境变量带偏
（例如本机 `.env` 里指向 PostgreSQL 的 `DATABASE_URL`）。
个别文件仍可在此基础上覆盖——如 `test_api.py` 会再显式指定自己的管理员号段。
"""
import os

os.environ["DATABASE_URL"] = "sqlite:///:memory:"
os.environ["JWT_SECRET"] = "test-secret-key-for-unit-tests-32bytes"
os.environ["ENVIRONMENT"] = "development"
# 不信任任何代理头：测试里才能稳定断言 X-Forwarded-For 的行为
os.environ["TRUSTED_PROXIES"] = ""
# 关掉限流：多数文件会反复登录，同一 testclient IP 很快触顶，
# 表现为 login 返回 data=None，报错栈指向莫名其妙的取值行
os.environ["RATE_LIMIT_ENABLED"] = "false"
os.environ["SEED_SAMPLE_DATA"] = "false"
# 关掉定时抓取：TestClient 触发 lifespan → 启动调度 → **真的去抓网络**
os.environ["FEED_CRON_ENABLED"] = "false"
# 默认管理员号段（与 test_api.py 一致；需要别的号段的文件自行覆盖）
os.environ["ADMIN_PHONES"] = "13800138000"
