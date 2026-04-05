# =============================================
# 校招判定引擎 — 多维度信号综合判定
# =============================================
# 判定维度：
#   1. 平台维度：实习僧默认为校招
#   2. 标题关键词：校招/春招/秋招/应届/管培/XX届等
#   3. 经验字段：应届/在校/无经验
#   4. 岗位类型：实习/校招
#   5. JD内容关键词：描述中出现校招相关高频词
# 规则：任意维度命中即标记 is_campus=True
# =============================================

import re

# 标题 / JD 中的校招关键词（覆盖常见变体）
_CAMPUS_KEYWORDS = [
    r"校招", r"春招", r"秋招", r"校园招聘",
    r"应届", r"届毕业", r"管培生", r"实习生?",
    r"2[0-9]届", r"[0-9]{2}届",  # 25届、2026届 等
    r"暑期实习", r"日常实习", r"寒假实习",
    r"graduate", r"intern", r"campus",
    r"new\s*grad", r"fresh\s*grad",
]
_CAMPUS_PATTERN = re.compile("|".join(_CAMPUS_KEYWORDS), re.IGNORECASE)

# 经验字段中的校招关键词
_CAMPUS_EXPERIENCE = {"应届", "应届生", "在校生", "在校", "无经验", "不限", "经验不限", "0年"}

# 默认标记为校招的数据源
_CAMPUS_SOURCES = {"shixiseng"}

# 岗位类型中视为校招的值
_CAMPUS_JOB_TYPES = {"实习", "校招", "intern", "campus"}


def detect_campus(
    *,
    title: str = "",
    source: str = "",
    experience: str = "",
    job_type: str = "",
    raw_description: str = "",
) -> bool:
    """
    综合判定是否为校招岗位

    只要以下任一条件成立即返回 True：
    1. 数据源为实习僧
    2. 标题匹配校招关键词
    3. 经验字段为应届/在校/无经验
    4. 岗位类型为实习/校招
    5. JD 描述中出现校招关键词（至少匹配2次，避免误判）
    """
    # 维度 1：平台
    if source.lower() in _CAMPUS_SOURCES:
        return True

    # 维度 2：标题关键词
    if _CAMPUS_PATTERN.search(title):
        return True

    # 维度 3：经验字段
    exp_lower = experience.strip().lower()
    if exp_lower in _CAMPUS_EXPERIENCE:
        return True

    # 维度 4：岗位类型
    jt_lower = job_type.strip().lower()
    if jt_lower in _CAMPUS_JOB_TYPES:
        return True

    # 维度 5：JD 内容（需命中 ≥2 次，降低误判率）
    if raw_description:
        matches = _CAMPUS_PATTERN.findall(raw_description[:3000])  # 只检查前 3000 字符
        if len(matches) >= 2:
            return True

    return False
