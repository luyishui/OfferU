# =============================================
# Instant Draft — Step 2.5 即时价值钩子
# =============================================
# 用户给出 3 段经历名称 + 目标岗位 → 秒出简历框架草稿，激励继续填充。
# 自 app/agents/skills/conversational_extractor.py 迁移（旧 agent 栈已拆除）。
# =============================================

from __future__ import annotations

from typing import Optional

from app.services.llm import chat_completion, extract_json

INSTANT_VALUE_PROMPT = """你是 OfferU 求职助手。用户刚刚告诉了你他的3段核心经历名称和目标岗位。
请根据这些信息，快速生成一份简历框架草稿。

## 规则
1. 基于用户提供的经历名称，为每段经历生成 1-2 个 Bullet Point 占位符
2. Bullet 内容用「待补充」标注具体数据位（如"负责XXX，[具体成果待补充]"）
3. 根据目标岗位调整用词方向
4. 输出完整的简历框架 JSON

## 输出格式
{{
  "headline": "一句话职业定位",
  "sections": [
    {{
      "section_type": "internship|project|activity|...",
      "title": "经历标题",
      "bullets": [
        "Bullet 1（含占位符）",
        "Bullet 2（含占位符）"
      ]
    }}
  ],
  "missing_hints": ["建议补充的内容1", "建议补充的内容2"],
  "encouragement": "对用户的鼓励语（一句话）"
}}"""


async def generate_instant_draft(
    experiences: list[str],
    target_roles: list[str],
) -> Optional[dict]:
    """即时价值钩子 — 3句话生成简历草稿框架。"""
    roles_str = "、".join(target_roles) if target_roles else "通用"
    exp_str = "\n".join(f"- {e}" for e in experiences)

    messages = [
        {"role": "system", "content": INSTANT_VALUE_PROMPT},
        {"role": "user", "content": f"我的目标岗位：{roles_str}\n\n我的3段经历：\n{exp_str}"},
    ]

    raw = await chat_completion(
        messages=messages,
        temperature=0.5,
        json_mode=True,
        max_tokens=2048,
        tier="standard",
    )

    if not raw:
        return None
    return extract_json(raw)
