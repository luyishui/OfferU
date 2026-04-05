# =============================================
# 数据源适配器注册 — 导入所有适配器以触发 register_scraper()
# =============================================

from app.services.scrapers.boss import BossScraper       # noqa: F401
from app.services.scrapers.zhilian import ZhilianScraper  # noqa: F401
from app.services.scrapers.linkedin import LinkedInScraper # noqa: F401
from app.services.scrapers.shixiseng import ShixisengScraper # noqa: F401
from app.services.scrapers.corporate import ByteDanceScraper, AlibabaScraper, TencentScraper  # noqa: F401

try:
    from app.services.scrapers.jobspy import JobSpyScraper  # noqa: F401
except ImportError:
    pass  # python-jobspy requires Python >= 3.10
