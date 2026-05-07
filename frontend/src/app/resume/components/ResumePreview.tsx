"use client";

import { forwardRef, useMemo } from "react";
import { ResumeSwissSingle } from "./templates/ResumeSwissSingle";
import { ResumeSwissTwoColumn } from "./templates/ResumeSwissTwoColumn";
import { ResumeModernSingle } from "./templates/ResumeModernSingle";
import { ResumeModernTwoColumn } from "./templates/ResumeModernTwoColumn";
import { ResumeReference } from "./templates/ResumeReference";
import {
  normalizeTemplateSettings,
  settingsToCssVars,
  type NormalizedResumeData,
  type NormalizedResumeItem,
  type NormalizedResumeSection,
} from "./templates/templateSettings";
import { dateRange, splitBullets, textFromHtml } from "./templates/shared";

interface Section {
  id: number;
  section_type: string;
  title: string;
  visible: boolean;
  content_json: any[];
  sort_order: number;
}

interface ResumePreviewProps {
  userName: string;
  title?: string;
  photoUrl: string;
  summary: string;
  contactJson: Record<string, string>;
  sections: Section[];
  styleConfig: Record<string, string>;
  highlightKeywords?: string[];
}

function normalizeSkillEntry(item: any, index: number): NormalizedResumeItem {
  const isCertificate =
    item?._entryType === "certificate" || item?.name || item?.issuer || item?.scoreOrLevel || item?.date;
  if (isCertificate) {
    return {
      id: `skill-cert-${index}`,
      title: item.name || `Certificate ${index + 1}`,
      subtitle: [item.scoreOrLevel, item.issuer].filter(Boolean).join(" / "),
      date: item.date || "",
      url: item.url || "",
      bullets: [],
    };
  }
  return {
    id: `skill-${index}`,
    title: item.category || `Skill Group ${index + 1}`,
    tags: Array.isArray(item.items)
      ? item.items.map((value: unknown) => String(value)).filter(Boolean)
      : String(item.items || "")
          .split(/[,，、]/)
          .map((value) => value.trim())
          .filter(Boolean),
    bullets: [],
  };
}

function normalizeSectionItem(sectionType: string, item: any, index: number): NormalizedResumeItem {
  if (sectionType === "education") {
    return {
      id: `education-${index}`,
      title: item.school || `Education ${index + 1}`,
      subtitle: [item.degree, item.major, item.gpa ? `GPA ${item.gpa}` : ""].filter(Boolean).join(" / "),
      date: dateRange(item.startDate, item.endDate),
      descriptionHtml: item.description || "",
      bullets: splitBullets(item.description || ""),
    };
  }
  if (sectionType === "experience") {
    return {
      id: `experience-${index}`,
      title: item.position || item.company || `Experience ${index + 1}`,
      organization: item.company || "",
      location: item.location || "",
      date: dateRange(item.startDate, item.endDate),
      descriptionHtml: item.description || "",
      bullets: splitBullets(item.description || ""),
    };
  }
  if (sectionType === "project") {
    return {
      id: `project-${index}`,
      title: item.name || `Project ${index + 1}`,
      subtitle: item.role || "",
      date: dateRange(item.startDate, item.endDate),
      url: item.url || "",
      descriptionHtml: item.description || "",
      bullets: splitBullets(item.description || ""),
    };
  }
  if (sectionType === "skill") {
    return normalizeSkillEntry(item, index);
  }
  if (sectionType === "certificate") {
    return {
      id: `certificate-${index}`,
      title: item.name || `Certificate ${index + 1}`,
      subtitle: [item.scoreOrLevel, item.issuer].filter(Boolean).join(" / "),
      date: item.date || "",
      url: item.url || "",
      bullets: [],
    };
  }
  return {
    id: `${sectionType}-${index}`,
    title: item.subtitle || item.title || `Item ${index + 1}`,
    subtitle: item.organization || "",
    date: dateRange(item.startDate, item.endDate),
    url: item.url || "",
    descriptionHtml: item.description || item.content || "",
    bullets: splitBullets(item.description || item.content || ""),
  };
}

function resolveAssetUrl(url?: string) {
  if (!url) return "";
  return url.startsWith("/")
    ? `${process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000"}${url}`
    : url;
}

function normalizeContactJson(contactJson: Record<string, string> = {}) {
  const contact = { ...contactJson };
  for (const key of ["schoolLogoUrl", "universityLogoUrl", "logoUrl", "school_logo_url"]) {
    if (contact[key]) {
      contact[key] = resolveAssetUrl(contact[key]);
    }
  }
  return contact;
}

function normalizeResumeData(props: ResumePreviewProps): NormalizedResumeData {
  const normalizedSections: NormalizedResumeSection[] = [...(props.sections || [])]
    .sort((a, b) => (a.sort_order || 0) - (b.sort_order || 0))
    .map((section) => ({
      id: section.id,
      key: section.section_type,
      title: section.title || section.section_type,
      visible: section.visible !== false,
      sortOrder: section.sort_order || 0,
      items: (section.content_json || [])
        .map((item, index) => normalizeSectionItem(section.section_type, item, index))
        .filter((item) => item.title || item.bullets.length || item.tags?.length),
    }));

  return {
    userName: props.userName,
    title: props.title || props.contactJson?.headline || props.contactJson?.title || "",
    photoUrl: props.photoUrl,
    summary: textFromHtml(props.summary),
    contact: normalizeContactJson(props.contactJson || {}),
    sections: normalizedSections,
  };
}

const ResumePreview = forwardRef<HTMLDivElement, ResumePreviewProps>(function ResumePreview(props, ref) {
  const settings = useMemo(() => normalizeTemplateSettings(props.styleConfig), [props.styleConfig]);
  const cssVars = useMemo(() => settingsToCssVars(settings), [settings]);
  const data = useMemo(() => normalizeResumeData(props), [props]);
  const highlightKeywords = props.highlightKeywords || [];

  const template = (() => {
    if (
      settings.template === "reference" ||
      settings.template === "reference-compact" ||
      settings.template === "reference-no-photo"
    ) {
      return <ResumeReference data={data} highlightKeywords={highlightKeywords} template={settings.template} />;
    }
    if (settings.template === "swiss-two-column") {
      return <ResumeSwissTwoColumn data={data} highlightKeywords={highlightKeywords} />;
    }
    if (settings.template === "modern") {
      return <ResumeModernSingle data={data} highlightKeywords={highlightKeywords} />;
    }
    if (settings.template === "modern-two-column") {
      return <ResumeModernTwoColumn data={data} highlightKeywords={highlightKeywords} />;
    }
    return <ResumeSwissSingle data={data} highlightKeywords={highlightKeywords} />;
  })();

  return (
    <div className="inline-block swiss-resume">
      <div
        ref={ref}
        className={`resume-body ${settings.template}`}
        data-template={settings.template}
        style={cssVars}
      >
        {template}
      </div>
    </div>
  );
});

export default ResumePreview;
