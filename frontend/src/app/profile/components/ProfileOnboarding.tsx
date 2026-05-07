// =============================================
// ProfileOnboarding — 5步 Profile 冷启动向导
// =============================================
// Step 1: 基础信息表单 (姓名/学校/专业/学位/GPA/邮箱/电话)
// Step 2: 目标岗位选择 (多选 + 自定义 + fit_level)
// Step 2.5: 即时价值钩子 (3段经历 → AI秒出简历框架)
// Step 3-4: AI 对话引导 (进入主 Profile 页面)
// Step 5: 职业叙事总结 (headline + exit_story)
// =============================================

"use client";

import { useMemo, useState } from "react";
import { motion, AnimatePresence } from "framer-motion";
import {
  Button,
  Input,
  Textarea,
  Card,
  CardBody,
  Chip,
  Select,
  SelectItem,
  Spinner,
} from "@nextui-org/react";
import {
  ArrowLeft,
  ArrowRight,
  User,
  Target,
  Zap,
  MessageSquare,
  Sparkles,
  Plus,
  CheckCircle2,
} from "lucide-react";
import { profileApi } from "@/lib/api";

interface ProfileOnboardingProps {
  onComplete: () => void;
}

const TOTAL_STEPS = 6;

type QuizOption = {
  value: string;
  label: string;
  description: string;
  traits: string[];
  roles: string[];
  proofAngles: string[];
};

type QuizQuestion = {
  id: string;
  title: string;
  prompt: string;
  options: QuizOption[];
};

const QUIZ_QUESTIONS: QuizQuestion[] = [
  {
    id: "work_style",
    title: "面对一件新任务，你更自然的反应是？",
    prompt: "选更像你的那边，不用纠结标准答案。",
    options: [
      {
        value: "organize",
        label: "先拆目标和节奏",
        description: "我会把人、时间、交付物排清楚。",
        traits: ["组织推进型", "执行闭环"],
        roles: ["运营", "项目助理", "产品运营"],
        proofAngles: ["流程推进", "跨方协作", "交付结果"],
      },
      {
        value: "discover",
        label: "先找真实需求",
        description: "我会先问用户、同学或业务方到底卡在哪。",
        traits: ["用户洞察型", "问题拆解"],
        roles: ["用户研究", "产品助理", "市场洞察"],
        proofAngles: ["用户反馈", "需求分析", "问题定义"],
      },
    ],
  },
  {
    id: "output_style",
    title: "哪种产出更让你有掌控感？",
    prompt: "这会影响简历把你写成内容型、数据型还是产品型。",
    options: [
      {
        value: "content",
        label: "一篇能被看见的内容",
        description: "文章、视频、海报、活动文案、账号内容都算。",
        traits: ["内容表达型", "传播敏感"],
        roles: ["内容运营", "品牌市场", "新媒体运营"],
        proofAngles: ["内容作品", "传播数据", "受众反馈"],
      },
      {
        value: "data",
        label: "一张说清问题的表",
        description: "我喜欢用数据、对比、归因找到下一步。",
        traits: ["数据分析型", "理性归因"],
        roles: ["商业分析", "数据运营", "产品运营"],
        proofAngles: ["数据分析", "指标变化", "决策依据"],
      },
    ],
  },
  {
    id: "team_role",
    title: "团队里你常常承担什么角色？",
    prompt: "这会帮 Agent 判断该追问领导力、协作还是专业深度。",
    options: [
      {
        value: "connector",
        label: "把资源和人拉起来",
        description: "我会沟通、协调、推进合作。",
        traits: ["资源整合型", "沟通协调"],
        roles: ["BD", "活动运营", "校园招聘"],
        proofAngles: ["资源拓展", "合作对象", "活动规模"],
      },
      {
        value: "builder",
        label: "把方案和作品做扎实",
        description: "我更愿意沉下去打磨方案、产品或研究。",
        traits: ["方案打磨型", "作品导向"],
        roles: ["产品助理", "行业研究", "策划"],
        proofAngles: ["方案产出", "作品链接", "方法论"],
      },
    ],
  },
  {
    id: "achievement",
    title: "哪类经历最容易让你觉得“这事我做成了”？",
    prompt: "选项会变成后面经历输入框的提示。",
    options: [
      {
        value: "community",
        label: "让活动、社群或项目跑起来",
        description: "从 0 到 1 组织一群人完成一件事。",
        traits: ["场景运营型", "节奏管理"],
        roles: ["社群运营", "活动运营", "用户运营"],
        proofAngles: ["参与人数", "留存互动", "活动复盘"],
      },
      {
        value: "product",
        label: "做出一个工具、系统或作品",
        description: "可以被展示、使用、复盘的东西让我更有成就感。",
        traits: ["产品项目型", "交付导向"],
        roles: ["产品经理", "项目运营", "AI 产品运营"],
        proofAngles: ["功能上线", "使用人数", "作品链接"],
      },
    ],
  },
  {
    id: "proof_style",
    title: "你更容易拿出哪种证明？",
    prompt: "这会提醒 Agent 优先追问哪种 proof point。",
    options: [
      {
        value: "numbers",
        label: "数字结果",
        description: "人数、金额、增长比例、排名、周期、覆盖范围。",
        traits: ["结果证明型", "指标意识"],
        roles: ["增长运营", "数据运营", "商业分析"],
        proofAngles: ["人数/金额", "增长比例", "排名/周期"],
      },
      {
        value: "portfolio",
        label: "作品案例",
        description: "文章、方案、Demo、研究报告、活动物料。",
        traits: ["作品证明型", "案例表达"],
        roles: ["内容策划", "产品助理", "市场策划"],
        proofAngles: ["作品案例", "方案文档", "展示链接"],
      },
    ],
  },
  {
    id: "job_strategy",
    title: "你现在更想怎么投？",
    prompt: "这会影响推荐岗位是保守入口还是成长入口。",
    options: [
      {
        value: "stable",
        label: "先拿稳入口",
        description: "希望方向清楚、门槛匹配、能尽快投起来。",
        traits: ["稳健求职型", "匹配优先"],
        roles: ["运营", "HR", "行政", "市场助理"],
        proofAngles: ["岗位匹配点", "基础能力", "可迁移经验"],
      },
      {
        value: "growth",
        label: "愿意冲成长方向",
        description: "可以接受学习曲线，想往 AI、产品、增长靠。",
        traits: ["成长探索型", "高潜迁移"],
        roles: ["AI 产品运营", "产品助理", "增长运营"],
        proofAngles: ["学习速度", "迁移能力", "AI 工具使用"],
      },
    ],
  },
];

// 预设岗位标签
const PRESET_ROLES = [
  "运营", "市场", "产品", "行政", "教育",
  "BD", "HR", "财务", "内容", "策划",
];

const FIT_LEVELS = [
  { value: "primary", label: "首选" },
  { value: "secondary", label: "次选" },
  { value: "adjacent", label: "相关" },
];

function rankByCount(items: string[], limit: number) {
  const counts = new Map<string, number>();
  for (const item of items) {
    counts.set(item, (counts.get(item) || 0) + 1);
  }
  return Array.from(counts.entries())
    .sort((a, b) => b[1] - a[1])
    .map(([item]) => item)
    .slice(0, limit);
}

function uniqueLimit(items: string[], limit: number) {
  return Array.from(new Set(items.filter(Boolean))).slice(0, limit);
}

function buildCareerProfile(options: QuizOption[]) {
  const traits = rankByCount(options.flatMap((option) => option.traits), 4);
  const suggestedRoles = rankByCount(options.flatMap((option) => option.roles), 5);
  const proofAngles = uniqueLimit(options.flatMap((option) => option.proofAngles), 6);
  const archetype = traits.length >= 2 ? `${traits[0]} + ${traits[1]}` : traits[0] || "探索型";

  return {
    archetype,
    traits,
    suggestedRoles,
    proofAngles,
    summary:
      traits.length > 0
        ? `你的简历更适合围绕「${traits.slice(0, 2).join(" / ")}」来讲，后续重点补齐 ${proofAngles.slice(0, 3).join("、") || "可验证成果"}。`
        : "先完成这组选择，我会把答案转成岗位方向和经历追问线索。",
  };
}

// 动画变体
const slideVariants = {
  enter: (dir: number) => ({
    x: dir > 0 ? 300 : -300,
    opacity: 0,
  }),
  center: { x: 0, opacity: 1 },
  exit: (dir: number) => ({
    x: dir < 0 ? 300 : -300,
    opacity: 0,
  }),
};

export function ProfileOnboarding({ onComplete }: ProfileOnboardingProps) {
  const [step, setStep] = useState(0);
  const [direction, setDirection] = useState(1);

  // Step 0: MBTI 式职业画像
  const [quizAnswers, setQuizAnswers] = useState<Record<string, string>>({});

  // Step 1: 基础信息
  const [name, setName] = useState("");
  const [school, setSchool] = useState("");
  const [major, setMajor] = useState("");
  const [degree, setDegree] = useState("本科");
  const [gpa, setGpa] = useState("");
  const [email, setEmail] = useState("");
  const [phone, setPhone] = useState("");

  // Step 2: 目标岗位
  const [selectedRoles, setSelectedRoles] = useState<
    { title: string; fit_level: string }[]
  >([]);
  const [customRole, setCustomRole] = useState("");

  // Step 2.5: 即时价值
  const [experiences, setExperiences] = useState(["", "", ""]);
  const [generating, setGenerating] = useState(false);
  const [draftGenerated, setDraftGenerated] = useState(false);

  // Step 5: 叙事
  const [headline, setHeadline] = useState("");
  const [exitStory, setExitStory] = useState("");
  const [narrativeLoading, setNarrativeLoading] = useState(false);

  // 全局
  const [saving, setSaving] = useState(false);

  const selectedQuizOptions = useMemo(
    () =>
      QUIZ_QUESTIONS.flatMap((question) => {
        const option = question.options.find((item) => item.value === quizAnswers[question.id]);
        return option ? [{ question, option }] : [];
      }),
    [quizAnswers]
  );
  const careerProfile = useMemo(
    () => buildCareerProfile(selectedQuizOptions.map((item) => item.option)),
    [selectedQuizOptions]
  );
  const quizComplete = selectedQuizOptions.length === QUIZ_QUESTIONS.length;
  const experiencePlaceholders = [
    `例：我做过一个${careerProfile.proofAngles[0] || "项目/活动"}，当时目标是...我负责...最后带来了...`,
    `例：一段最能证明${careerProfile.traits[0] || "能力"}的经历，背景是...我具体做了...结果是...`,
    `例：我想补充一个${careerProfile.proofAngles[1] || "作品/数据"}，里面有...人数/金额/增长/反馈是...`,
  ];

  const goNext = () => {
    setDirection(1);
    setStep((s) => Math.min(s + 1, TOTAL_STEPS - 1));
  };
  const goBack = () => {
    setDirection(-1);
    setStep((s) => Math.max(s - 1, 0));
  };

  const handleSaveQuiz = () => {
    const recommendedRoles = careerProfile.suggestedRoles.slice(0, 3);
    if (recommendedRoles.length > 0) {
      setSelectedRoles((prev) => {
        const existing = new Set(prev.map((role) => role.title));
        const additions = recommendedRoles
          .filter((role) => !existing.has(role))
          .map((role, index) => ({
            title: role,
            fit_level: index === 0 ? "primary" : "secondary",
          }));
        return [...prev, ...additions];
      });
    }
    goNext();
  };

  // Step 1 → 保存基础信息
  const handleSaveBasic = async () => {
    const careerProfilePayload = {
      ...careerProfile,
      answers: selectedQuizOptions.reduce<Record<string, string>>((acc, item) => {
        acc[item.question.id] = item.option.label;
        return acc;
      }, {}),
    };
    setSaving(true);
    try {
      await profileApi.update({
        name: name.trim(),
        base_info_json: {
          email: email.trim(),
          phone: phone.trim(),
          school: school.trim(),
          major: major.trim(),
          degree,
          gpa: gpa.trim(),
          career_profile: careerProfilePayload,
        },
      });
      goNext();
    } catch {
      // 允许继续
      goNext();
    } finally {
      setSaving(false);
    }
  };

  // Step 2 → 保存目标岗位
  const handleSaveRoles = async () => {
    setSaving(true);
    try {
      for (const role of selectedRoles) {
        await profileApi.addTargetRole(role);
      }
      goNext();
    } catch {
      goNext();
    } finally {
      setSaving(false);
    }
  };

  // 添加岗位
  const toggleRole = (title: string) => {
    setSelectedRoles((prev) => {
      const exists = prev.find((r) => r.title === title);
      if (exists) return prev.filter((r) => r.title !== title);
      return [...prev, { title, fit_level: "primary" }];
    });
  };

  const addCustomRole = () => {
    if (!customRole.trim()) return;
    const t = customRole.trim();
    if (!selectedRoles.find((r) => r.title === t)) {
      setSelectedRoles((prev) => [
        ...prev,
        { title: t, fit_level: "primary" },
      ]);
    }
    setCustomRole("");
  };

  const updateFitLevel = (title: string, level: string) => {
    setSelectedRoles((prev) =>
      prev.map((r) => (r.title === title ? { ...r, fit_level: level } : r))
    );
  };

  // Step 2.5 → 即时草稿
  const handleInstantDraft = async () => {
    const filled = experiences.filter((e) => e.trim());
    if (filled.length === 0) return;
    setGenerating(true);
    try {
      await profileApi.instantDraft({
        experiences: filled,
        target_roles: selectedRoles.map((role) => role.title),
      });
      setDraftGenerated(true);
    } catch {
      // 允许继续
    } finally {
      setGenerating(false);
    }
  };

  // Step 5 → 生成叙事
  const handleGenerateNarrative = async () => {
    setNarrativeLoading(true);
    try {
      const result: any = await profileApi.generateNarrative();
      if (result.headline) setHeadline(result.headline);
      if (result.exit_story) setExitStory(result.exit_story);
    } catch {
      // ignore
    } finally {
      setNarrativeLoading(false);
    }
  };

  // 完成向导
  const handleFinish = async () => {
    setSaving(true);
    try {
      // 保存叙事到 Profile
      if (headline || exitStory) {
        await profileApi.update({ headline, exit_story: exitStory });
      }
      onComplete();
    } catch {
      onComplete();
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 bg-black/90 backdrop-blur-xl flex items-center justify-center p-4">
      <motion.div
        initial={{ opacity: 0, scale: 0.95 }}
        animate={{ opacity: 1, scale: 1 }}
        className="w-full max-w-3xl"
      >
        {/* 进度条 */}
        <div className="flex items-center gap-2 mb-6 justify-center">
          {Array.from({ length: TOTAL_STEPS }).map((_, i) => (
            <div
              key={i}
              className={`h-1.5 rounded-full transition-all duration-300 ${
                i <= step
                  ? "bg-blue-500 w-12"
                  : "bg-white/10 w-8"
              }`}
            />
          ))}
          <span className="text-xs text-white/30 ml-2">
            {step + 1}/{TOTAL_STEPS}
          </span>
        </div>

        {/* Step Content */}
        <Card className="bg-white/5 border border-white/10 overflow-hidden">
          <CardBody className="max-h-[78vh] overflow-y-auto p-8">
            <AnimatePresence mode="wait" custom={direction}>
              {step === 0 && (
                <StepWrapper key="s0" dir={direction}>
                  <StepHeader
                    icon={Sparkles}
                    title="先做一个求职画像测试"
                    subtitle="像 MBTI 一样选更像你的答案，我会把它转成岗位方向和简历素材线索"
                  />

                  <div className="mt-6 space-y-5">
                    {QUIZ_QUESTIONS.map((question, index) => (
                      <div key={question.id} className="border-b border-white/10 pb-4 last:border-b-0 last:pb-0">
                        <div className="flex items-start justify-between gap-4">
                          <div>
                            <p className="text-xs font-semibold uppercase tracking-[0.18em] text-blue-300/80">
                              Question {index + 1}
                            </p>
                            <h3 className="mt-1 text-sm font-semibold text-white">{question.title}</h3>
                            <p className="mt-1 text-xs text-white/45">{question.prompt}</p>
                          </div>
                          {quizAnswers[question.id] && (
                            <CheckCircle2 size={18} className="mt-1 shrink-0 text-green-400" />
                          )}
                        </div>

                        <div className="mt-3 grid gap-2 sm:grid-cols-2">
                          {question.options.map((option) => {
                            const selected = quizAnswers[question.id] === option.value;
                            return (
                              <button
                                key={option.value}
                                type="button"
                                onClick={() =>
                                  setQuizAnswers((prev) => ({
                                    ...prev,
                                    [question.id]: option.value,
                                  }))
                                }
                                className={`rounded-xl border px-4 py-3 text-left transition ${
                                  selected
                                    ? "border-blue-400 bg-blue-500/20 text-white"
                                    : "border-white/10 bg-white/[0.03] text-white/70 hover:border-white/25 hover:bg-white/[0.06]"
                                }`}
                              >
                                <span className="block text-sm font-semibold">{option.label}</span>
                                <span className="mt-1 block text-xs leading-5 text-white/45">{option.description}</span>
                              </button>
                            );
                          })}
                        </div>
                      </div>
                    ))}
                  </div>

                  <div className="mt-5 rounded-xl border border-blue-400/20 bg-blue-500/10 p-4">
                    <p className="text-sm font-semibold text-white">{careerProfile.archetype}</p>
                    <p className="mt-1 text-xs leading-5 text-white/55">{careerProfile.summary}</p>
                    {careerProfile.suggestedRoles.length > 0 && (
                      <div className="mt-3 flex flex-wrap gap-2">
                        {careerProfile.suggestedRoles.slice(0, 5).map((role) => (
                          <Chip key={role} size="sm" variant="flat" color="primary">
                            {role}
                          </Chip>
                        ))}
                      </div>
                    )}
                  </div>

                  <div className="flex justify-end mt-6">
                    <Button
                      color="primary"
                      endContent={<ArrowRight size={16} />}
                      isDisabled={!quizComplete}
                      onPress={handleSaveQuiz}
                    >
                      生成我的求职画像
                    </Button>
                  </div>
                </StepWrapper>
              )}

              {step === 1 && (
                <StepWrapper key="s1" dir={direction}>
                  <StepHeader
                    icon={User}
                    title="基础信息"
                    subtitle="让我们先了解你的基本情况"
                  />
                  <div className="grid grid-cols-2 gap-4 mt-6">
                    <Input
                      label="姓名"
                      isRequired
                      value={name}
                      onValueChange={setName}
                      variant="bordered"
                      classNames={inputClasses}
                    />
                    <Input
                      label="学校"
                      isRequired
                      value={school}
                      onValueChange={setSchool}
                      variant="bordered"
                      classNames={inputClasses}
                    />
                    <Input
                      label="专业"
                      isRequired
                      value={major}
                      onValueChange={setMajor}
                      variant="bordered"
                      classNames={inputClasses}
                    />
                    <Select
                      label="学位"
                      selectedKeys={[degree]}
                      onSelectionChange={(keys) => {
                        const v = Array.from(keys)[0] as string;
                        if (v) setDegree(v);
                      }}
                      variant="bordered"
                      classNames={inputClasses}
                    >
                      <SelectItem key="本科">本科</SelectItem>
                      <SelectItem key="硕士">硕士</SelectItem>
                      <SelectItem key="博士">博士</SelectItem>
                      <SelectItem key="大专">大专</SelectItem>
                    </Select>
                    <Input
                      label="GPA (可选)"
                      value={gpa}
                      onValueChange={setGpa}
                      variant="bordered"
                      classNames={inputClasses}
                    />
                    <Input
                      label="邮箱"
                      isRequired
                      type="email"
                      value={email}
                      onValueChange={setEmail}
                      variant="bordered"
                      classNames={inputClasses}
                    />
                    <Input
                      label="电话"
                      isRequired
                      value={phone}
                      onValueChange={setPhone}
                      variant="bordered"
                      classNames={inputClasses}
                      className="col-span-2 sm:col-span-1"
                    />
                  </div>
                  <div className="flex justify-end mt-6">
                    <Button
                      color="primary"
                      endContent={<ArrowRight size={16} />}
                      isLoading={saving}
                      isDisabled={!name.trim() || !school.trim() || !major.trim()}
                      onPress={handleSaveBasic}
                    >
                      下一步
                    </Button>
                  </div>
                </StepWrapper>
              )}

              {step === 2 && (
                <StepWrapper key="s2" dir={direction}>
                  <StepHeader
                    icon={Target}
                    title="目标岗位"
                    subtitle="我已经根据测试预选了几个方向，你可以继续增删"
                  />
                  <div className="mt-5 rounded-xl border border-white/10 bg-white/[0.03] p-4">
                    <p className="text-sm font-semibold text-white">{careerProfile.archetype}</p>
                    <p className="mt-1 text-xs leading-5 text-white/50">{careerProfile.summary}</p>
                    {careerProfile.proofAngles.length > 0 && (
                      <div className="mt-3 flex flex-wrap gap-2">
                        {careerProfile.proofAngles.slice(0, 4).map((angle) => (
                          <Chip key={angle} size="sm" variant="bordered" className="border-white/15 text-white/65">
                            {angle}
                          </Chip>
                        ))}
                      </div>
                    )}
                  </div>
                  {/* 预设标签 */}
                  <div className="flex flex-wrap gap-2 mt-6">
                    {PRESET_ROLES.map((role) => {
                      const selected = selectedRoles.some(
                        (r) => r.title === role
                      );
                      return (
                        <Chip
                          key={role}
                          variant={selected ? "solid" : "bordered"}
                          color={selected ? "primary" : "default"}
                          className="cursor-pointer"
                          onClick={() => toggleRole(role)}
                        >
                          {role}
                        </Chip>
                      );
                    })}
                  </div>

                  {/* 自定义输入 */}
                  <div className="flex gap-2 mt-4">
                    <Input
                      size="sm"
                      placeholder="输入其他岗位..."
                      value={customRole}
                      onValueChange={setCustomRole}
                      onKeyDown={(e) => e.key === "Enter" && addCustomRole()}
                      variant="bordered"
                      classNames={inputClasses}
                    />
                    <Button
                      size="sm"
                      isIconOnly
                      variant="flat"
                      onPress={addCustomRole}
                    >
                      <Plus size={16} />
                    </Button>
                  </div>

                  {/* Fit Level 设定 */}
                  {selectedRoles.length > 0 && (
                    <div className="mt-4 space-y-2">
                      <p className="text-xs text-white/40">
                        为每个岗位设置匹配度（可选）:
                      </p>
                      {selectedRoles.map((role) => (
                        <div
                          key={role.title}
                          className="flex items-center gap-3"
                        >
                          <Chip
                            variant="flat"
                            onClose={() => toggleRole(role.title)}
                          >
                            {role.title}
                          </Chip>
                          <div className="flex gap-1">
                            {FIT_LEVELS.map((fl) => (
                              <Chip
                                key={fl.value}
                                size="sm"
                                variant={
                                  role.fit_level === fl.value
                                    ? "solid"
                                    : "bordered"
                                }
                                color={
                                  role.fit_level === fl.value
                                    ? "primary"
                                    : "default"
                                }
                                className="cursor-pointer text-xs"
                                onClick={() =>
                                  updateFitLevel(role.title, fl.value)
                                }
                              >
                                {fl.label}
                              </Chip>
                            ))}
                          </div>
                        </div>
                      ))}
                    </div>
                  )}

                  <div className="flex justify-between mt-6">
                    <Button
                      variant="light"
                      startContent={<ArrowLeft size={16} />}
                      onPress={goBack}
                    >
                      上一步
                    </Button>
                    <Button
                      color="primary"
                      endContent={<ArrowRight size={16} />}
                      isLoading={saving}
                      isDisabled={selectedRoles.length === 0}
                      onPress={handleSaveRoles}
                    >
                      下一步
                    </Button>
                  </div>
                </StepWrapper>
              )}

              {step === 3 && (
                <StepWrapper key="s3" dir={direction}>
                  <StepHeader
                    icon={Zap}
                    title="快速预览简历"
                    subtitle="不用写完整简历，按测试结果先补 3 段能证明你的素材"
                  />
                  <div className="mt-5 flex flex-wrap gap-2">
                    {careerProfile.proofAngles.slice(0, 5).map((angle) => (
                      <Chip key={angle} size="sm" variant="flat" color="primary">
                        优先补：{angle}
                      </Chip>
                    ))}
                  </div>
                  <div className="space-y-3 mt-6">
                    {experiences.map((exp, i) => (
                      <Textarea
                        key={i}
                        label={`经历 ${i + 1}`}
                        placeholder={experiencePlaceholders[i]}
                        value={exp}
                        onValueChange={(v) =>
                          setExperiences((prev) => {
                            const next = [...prev];
                            next[i] = v;
                            return next;
                          })
                        }
                        minRows={2}
                        variant="bordered"
                        classNames={inputClasses}
                      />
                    ))}
                  </div>

                  {/* AI 秒出简历框架 */}
                  <div className="mt-4 p-4 bg-blue-500/10 border border-blue-500/20 rounded-xl">
                    {draftGenerated ? (
                      <div className="flex items-center gap-2 text-green-400">
                        <CheckCircle2 size={18} />
                        <span className="text-sm font-medium">
                          已为你生成简历框架！完成向导后可在"简历"页查看
                        </span>
                      </div>
                    ) : (
                      <Button
                        color="primary"
                        variant="flat"
                        startContent={
                          generating ? (
                            <Spinner size="sm" />
                          ) : (
                            <Zap size={16} />
                          )
                        }
                        isDisabled={
                          generating ||
                          experiences.every((e) => !e.trim())
                        }
                        onPress={handleInstantDraft}
                        className="w-full"
                      >
                        {generating
                          ? "AI 正在分析你的经历..."
                          : "AI 秒出简历框架"}
                      </Button>
                    )}
                  </div>

                  <div className="flex justify-between mt-6">
                    <Button
                      variant="light"
                      startContent={<ArrowLeft size={16} />}
                      onPress={goBack}
                    >
                      上一步
                    </Button>
                    <Button
                      color="primary"
                      endContent={<ArrowRight size={16} />}
                      onPress={goNext}
                    >
                      {draftGenerated ? "继续完善" : "跳过"}
                    </Button>
                  </div>
                </StepWrapper>
              )}

              {step === 4 && (
                <StepWrapper key="s4" dir={direction}>
                  <StepHeader
                    icon={MessageSquare}
                    title="AI 对话引导"
                    subtitle="接下来 Agent 会按你的画像追问 proof points，而不是让你干填经历"
                  />
                  <div className="mt-6 text-center py-8">
                    <div className="w-16 h-16 mx-auto mb-4 rounded-full bg-blue-500/20 flex items-center justify-center">
                      <MessageSquare size={28} className="text-blue-400" />
                    </div>
                    <p className="text-white/70 text-sm">
                      点击"开始对话"进入 Profile 构建页面
                    </p>
                    <p className="text-white/40 text-xs mt-2">
                      每轮只问一个缺口：背景、动作、结果、数字、作品或技能关键词
                    </p>
                  </div>
                  <div className="flex justify-between mt-6">
                    <Button
                      variant="light"
                      startContent={<ArrowLeft size={16} />}
                      onPress={goBack}
                    >
                      上一步
                    </Button>
                    <Button
                      color="primary"
                      endContent={<ArrowRight size={16} />}
                      onPress={goNext}
                    >
                      开始对话
                    </Button>
                  </div>
                </StepWrapper>
              )}

              {step === 5 && (
                <StepWrapper key="s5" dir={direction}>
                  <StepHeader
                    icon={Sparkles}
                    title="职业叙事"
                    subtitle="基于你的经历，生成你的职业故事"
                  />

                  <div className="mt-6 space-y-4">
                    <Button
                      variant="flat"
                      color="secondary"
                      startContent={
                        narrativeLoading ? (
                          <Spinner size="sm" />
                        ) : (
                          <Sparkles size={16} />
                        )
                      }
                      onPress={handleGenerateNarrative}
                      isDisabled={narrativeLoading}
                    >
                      {narrativeLoading
                        ? "AI 正在生成..."
                        : "🎯 AI 一键生成职业叙事"}
                    </Button>

                    <div>
                      <label className="text-xs text-white/40 mb-1 block">
                        Headline（一句话自我介绍）
                      </label>
                      <Textarea
                        value={headline}
                        onValueChange={setHeadline}
                        placeholder="运营从业者，擅长内容策略与用户增长..."
                        minRows={2}
                        variant="bordered"
                        classNames={inputClasses}
                      />
                    </div>

                    <div>
                      <label className="text-xs text-white/40 mb-1 block">
                        Exit Story（你为什么选择这个方向）
                      </label>
                      <Textarea
                        value={exitStory}
                        onValueChange={setExitStory}
                        placeholder="我热爱创意创作与用户交互..."
                        minRows={3}
                        variant="bordered"
                        classNames={inputClasses}
                      />
                    </div>
                  </div>

                  <div className="flex justify-between mt-6">
                    <Button
                      variant="light"
                      startContent={<ArrowLeft size={16} />}
                      onPress={goBack}
                    >
                      上一步
                    </Button>
                    <Button
                      color="success"
                      endContent={<CheckCircle2 size={16} />}
                      isLoading={saving}
                      onPress={handleFinish}
                    >
                      完成
                    </Button>
                  </div>
                </StepWrapper>
              )}
            </AnimatePresence>
          </CardBody>
        </Card>
      </motion.div>
    </div>
  );
}

// ---- Helper Components ----

function StepWrapper({
  children,
  dir,
}: {
  children: React.ReactNode;
  dir: number;
}) {
  return (
    <motion.div
      custom={dir}
      variants={slideVariants}
      initial="enter"
      animate="center"
      exit="exit"
      transition={{ type: "spring", stiffness: 300, damping: 30 }}
    >
      {children}
    </motion.div>
  );
}

function StepHeader({
  icon: Icon,
  title,
  subtitle,
}: {
  icon: React.ElementType;
  title: string;
  subtitle: string;
}) {
  return (
    <div className="flex items-center gap-3">
      <div className="w-10 h-10 rounded-xl bg-blue-500/20 flex items-center justify-center">
        <Icon size={20} className="text-blue-400" />
      </div>
      <div>
        <h2 className="text-lg font-semibold text-white">{title}</h2>
        <p className="text-sm text-white/50">{subtitle}</p>
      </div>
    </div>
  );
}

// 统一 Input 暗色样式
const inputClasses = {
  input: "text-white/80",
  inputWrapper: "bg-white/5 border-white/10 hover:border-white/20",
  label: "text-white/50",
};
