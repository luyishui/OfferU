# =============================================
# Skill 基类 — 模块化 AI 能力的统一接口
# =============================================
# 设计思路:
#   OfferU 的 Skill 系统借鉴 AIHawk 的模块化架构,
#   将复杂的简历优化任务拆分为多个独立的 Skill 模块。
#   每个 Skill 有独立的 Prompt + 逻辑，由 Pipeline
#   调度器串行编排执行。
#
#   与传统 Agent 框架（CrewAI/LangChain）的区别：
#   - 不依赖任何外部 Agent 框架
#   - 纯 Python 代码 + OpenAI 兼容 API
#   - 每个 Skill 是一个独立 Python 类
#   - Pipeline 按固定顺序串行调用
#   - 用户在 Web 前端操作，零额外安装
#
# 继承链:
#   BaseSkill (抽象基类)
#     ├── JDAnalyzerSkill     — JD 智能解析
#     ├── ResumeMatcherSkill  — 简历-JD 匹配分析
#     ├── ContentOptimizer    — 内容优化（后续）
#     └── ResumeGenerator     — 新副本生成（后续）
# =============================================

from abc import ABC, abstractmethod
from typing import Any, Optional


class BaseSkill(ABC):
    """
    Skill 基类 — 所有 AI 技能模块的抽象接口

    每个 Skill 必须实现:
      - name: 技能唯一标识
      - execute(): 核心执行方法，接收输入，返回结构化输出
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Skill 唯一标识，如 'jd-analyzer', 'resume-matcher'"""
        ...

    @abstractmethod
    async def execute(self, context: dict) -> dict:
        """
        执行技能

        参数:
          context: 包含上一步输出和全局上下文的字典
            - resume_text: 简历纯文本
            - resume_data: 简历结构化 JSON（含 sections）
            - jd_text: JD 原文
            - 上一步 Skill 的输出（如 jd_analysis）

        返回:
          该 Skill 的结构化输出，会合并到 context 中传递给下一步
        """
        ...
