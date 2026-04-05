// =============================================
// 岗位卡片组件 — 单个岗位展示
// =============================================
// 展示：标题、公司、来源标签、关键词 Chips
// hover 提升动画
// =============================================

"use client";

import { useRouter } from "next/navigation";
import { Card, CardBody, Chip, Avatar } from "@nextui-org/react";
import { MapPin, Briefcase, GraduationCap, DollarSign, Building2 } from "lucide-react";
import type { Job } from "@/lib/hooks";

// 来源平台颜色映射
const sourceColorMap: Record<string, "primary" | "success" | "warning" | "danger" | "secondary"> = {
  boss: "warning",
  zhilian: "primary",
  linkedin: "secondary",
  shixiseng: "success",
  maimai: "danger",
  corporate: "primary",
};

export function JobCard({ job }: { job: Job }) {
  const router = useRouter();
  const sourceColor = sourceColorMap[job.source] || "primary";

  return (
    <Card
      isPressable
      onPress={() => router.push(`/jobs/${job.id}`)}
      className="bg-white/5 border border-white/10 hover:border-white/20 transition-colors"
    >
      <CardBody className="p-5 space-y-3">
        {/* 头部：公司Logo + 标题 + 来源 */}
        <div className="flex items-start gap-3">
          <Avatar
            src={job.company_logo || undefined}
            name={job.company?.[0] || "?"}
            size="sm"
            className="shrink-0 bg-white/10"
          />
          <div className="flex-1 min-w-0">
            <h3 className="text-base font-bold text-blue-300 leading-tight truncate">
              {job.title}
            </h3>
            <div className="flex items-center gap-2 text-sm text-white/50 mt-0.5">
              <span className="truncate">{job.company}</span>
              {job.company_industry && (
                <>
                  <span>·</span>
                  <span className="truncate">{job.company_industry}</span>
                </>
              )}
            </div>
          </div>
          <Chip size="sm" variant="dot" color={sourceColor} className="text-xs shrink-0">
            {job.source}
          </Chip>
        </div>

        {/* 薪资（醒目展示） */}
        {job.salary_text && (
          <div className="flex items-center gap-1.5 text-green-400 font-semibold text-sm">
            <DollarSign size={14} />
            <span>{job.salary_text}</span>
          </div>
        )}

        {/* 标签行：地点 + 学历 + 经验 + 岗位类型 */}
        <div className="flex flex-wrap items-center gap-2 text-xs text-white/50">
          {job.location && (
            <span className="flex items-center gap-0.5">
              <MapPin size={12} />{job.location}
            </span>
          )}
          {job.education && (
            <span className="flex items-center gap-0.5">
              <GraduationCap size={12} />{job.education}
            </span>
          )}
          {job.experience && (
            <span className="flex items-center gap-0.5">
              <Briefcase size={12} />{job.experience}
            </span>
          )}
          {job.company_size && (
            <span className="flex items-center gap-0.5">
              <Building2 size={12} />{job.company_size}
            </span>
          )}
        </div>

        {/* 摘要 */}
        {job.summary && (
          <p className="text-sm text-white/60 line-clamp-2">{job.summary}</p>
        )}

        {/* 关键词 + 岗位类型/校招标签 */}
        <div className="flex flex-wrap gap-1.5">
          {job.is_campus && (
            <Chip size="sm" variant="flat" color="success" className="text-xs">
              校招
            </Chip>
          )}
          {job.job_type && (
            <Chip size="sm" variant="flat" color="warning" className="text-xs">
              {job.job_type}
            </Chip>
          )}
          {job.keywords?.slice(0, 4).map((kw) => (
            <Chip
              key={kw}
              size="sm"
              variant="flat"
              className="bg-white/5 text-white/60 text-xs"
            >
              {kw}
            </Chip>
          ))}
        </div>
      </CardBody>
    </Card>
  );
}
