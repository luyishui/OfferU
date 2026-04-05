// =============================================
// 岗位列表页 — 卡片式展示 + 多维度筛选
// =============================================
// 筛选：关键词搜索 / 数据源 / 时间范围 / 岗位类型 / 学历 / 校招
// 布局：响应式网格 + 动画列表
// =============================================

"use client";

import { useState, useCallback, useEffect } from "react";
import { motion } from "framer-motion";
import {
  Card,
  CardBody,
  Tabs,
  Tab,
  Pagination,
  Spinner,
  Input,
  Select,
  SelectItem,
  Switch,
} from "@nextui-org/react";
import { Search } from "lucide-react";
import { JobCard } from "@/components/jobs/JobCard";
import { useJobs } from "@/lib/hooks";

const container = {
  hidden: { opacity: 0 },
  show: { opacity: 1, transition: { staggerChildren: 0.06 } },
};

const item = {
  hidden: { opacity: 0, x: -20 },
  show: { opacity: 1, x: 0, transition: { type: "spring", damping: 15 } },
};

const SOURCE_OPTIONS = [
  { value: "", label: "全部来源" },
  { value: "boss", label: "BOSS直聘" },
  { value: "zhilian", label: "智联招聘" },
  { value: "linkedin", label: "领英" },
  { value: "shixiseng", label: "实习僧" },
  { value: "maimai", label: "脉脉" },
  { value: "corporate", label: "大厂官网" },
];

const JOB_TYPE_OPTIONS = [
  { value: "", label: "全部类型" },
  { value: "全职", label: "全职" },
  { value: "实习", label: "实习" },
  { value: "校招", label: "校招" },
  { value: "兼职", label: "兼职" },
];

const EDUCATION_OPTIONS = [
  { value: "", label: "全部学历" },
  { value: "不限", label: "不限" },
  { value: "本科", label: "本科" },
  { value: "硕士", label: "硕士" },
  { value: "博士", label: "博士" },
];

export default function JobsPage() {
  const [period, setPeriod] = useState<string>("week");
  const [page, setPage] = useState(1);
  const [keyword, setKeyword] = useState("");
  const [debouncedKeyword, setDebouncedKeyword] = useState("");
  const [source, setSource] = useState("");
  const [jobType, setJobType] = useState("");
  const [education, setEducation] = useState("");
  const [isCampus, setIsCampus] = useState(false);

  // 搜索关键词 debounce（300ms）
  useEffect(() => {
    const timer = setTimeout(() => {
      setDebouncedKeyword(keyword);
      setPage(1);
    }, 300);
    return () => clearTimeout(timer);
  }, [keyword]);

  const { data, isLoading } = useJobs({
    page,
    period,
    source: source || undefined,
    keyword: debouncedKeyword || undefined,
    job_type: jobType || undefined,
    education: education || undefined,
    is_campus: isCampus || undefined,
  });

  const jobs = data?.items ?? [];
  const totalPages = Math.ceil((data?.total ?? 0) / (data?.page_size ?? 20));

  const resetFilters = useCallback(() => {
    setPage(1);
    setKeyword("");
    setDebouncedKeyword("");
    setSource("");
    setJobType("");
    setEducation("");
    setIsCampus(false);
  }, []);

  return (
    <motion.div
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      className="space-y-6"
    >
      <div className="flex items-center justify-between">
        <h1 className="text-3xl font-bold">岗位匹配</h1>
        {data && (
          <span className="text-sm text-white/40">
            共 {data.total} 个岗位
          </span>
        )}
      </div>

      {/* 筛选栏 */}
      <div className="space-y-4">
        {/* 第一行：搜索 + 时间 */}
        <div className="flex flex-wrap items-center gap-4">
          <Input
            placeholder="搜索岗位或公司..."
            value={keyword}
            onValueChange={setKeyword}
            startContent={<Search size={16} className="text-white/40" />}
            classNames={{
              base: "max-w-xs",
              inputWrapper: "bg-white/5 border border-white/10",
            }}
            size="sm"
          />
          <Tabs
            aria-label="时间范围"
            variant="underlined"
            classNames={{ tabList: "gap-4" }}
            selectedKey={period}
            onSelectionChange={(key) => {
              setPeriod(key as string);
              setPage(1);
            }}
          >
            <Tab key="today" title="今日" />
            <Tab key="week" title="本周" />
            <Tab key="month" title="本月" />
          </Tabs>
        </div>

        {/* 第二行：下拉筛选 + 校招开关 */}
        <div className="flex flex-wrap items-center gap-3">
          <Select
            aria-label="数据来源"
            size="sm"
            selectedKeys={source ? [source] : [""]}
            onSelectionChange={(keys) => {
              const val = Array.from(keys)[0] as string;
              setSource(val);
              setPage(1);
            }}
            classNames={{
              base: "w-32",
              trigger: "bg-white/5 border border-white/10",
            }}
          >
            {SOURCE_OPTIONS.map((o) => (
              <SelectItem key={o.value}>{o.label}</SelectItem>
            ))}
          </Select>

          <Select
            aria-label="岗位类型"
            size="sm"
            selectedKeys={jobType ? [jobType] : [""]}
            onSelectionChange={(keys) => {
              const val = Array.from(keys)[0] as string;
              setJobType(val);
              setPage(1);
            }}
            classNames={{
              base: "w-32",
              trigger: "bg-white/5 border border-white/10",
            }}
          >
            {JOB_TYPE_OPTIONS.map((o) => (
              <SelectItem key={o.value}>{o.label}</SelectItem>
            ))}
          </Select>

          <Select
            aria-label="学历要求"
            size="sm"
            selectedKeys={education ? [education] : [""]}
            onSelectionChange={(keys) => {
              const val = Array.from(keys)[0] as string;
              setEducation(val);
              setPage(1);
            }}
            classNames={{
              base: "w-32",
              trigger: "bg-white/5 border border-white/10",
            }}
          >
            {EDUCATION_OPTIONS.map((o) => (
              <SelectItem key={o.value}>{o.label}</SelectItem>
            ))}
          </Select>

          <Switch
            size="sm"
            isSelected={isCampus}
            onValueChange={(val) => {
              setIsCampus(val);
              setPage(1);
            }}
            classNames={{ wrapper: "bg-white/10" }}
          >
            <span className="text-sm text-white/60">仅校招</span>
          </Switch>

          {(keyword || source || jobType || education || isCampus) && (
            <button
              onClick={resetFilters}
              className="text-xs text-blue-400 hover:underline"
            >
              清除筛选
            </button>
          )}
        </div>
      </div>

      {/* 岗位列表 */}
      {isLoading ? (
        <div className="flex justify-center py-20">
          <Spinner size="lg" />
        </div>
      ) : jobs.length > 0 ? (
        <>
          <motion.div
            variants={container}
            initial="hidden"
            animate="show"
            className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4"
          >
            {jobs.map((job) => (
              <motion.div
                key={job.id}
                variants={item}
                whileHover={{ y: -4 }}
                transition={{ type: "spring", stiffness: 300 }}
              >
                <JobCard job={job} />
              </motion.div>
            ))}
          </motion.div>

          {/* 分页 */}
          {totalPages > 1 && (
            <div className="flex justify-center pt-4">
              <Pagination
                total={totalPages}
                page={page}
                onChange={setPage}
                showControls
                classNames={{
                  cursor: "bg-blue-500",
                }}
              />
            </div>
          )}
        </>
      ) : (
        <Card className="bg-white/5 border border-white/10">
          <CardBody className="p-8 text-center text-white/40">
            <p className="text-lg mb-2">暂无岗位数据</p>
            <p className="text-sm">尝试调整筛选条件，或前往爬虫页面抓取岗位</p>
          </CardBody>
        </Card>
      )}
    </motion.div>
  );
}
