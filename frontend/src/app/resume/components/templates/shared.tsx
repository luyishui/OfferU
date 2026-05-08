import { splitBullets, textFromHtml } from "@/lib/resumeText";
import { KeywordHighlightView } from "../KeywordHighlightView";
import type { NormalizedResumeData, NormalizedResumeItem, NormalizedResumeSection } from "./templateSettings";

export { splitBullets, textFromHtml };

export function dateRange(start?: string, end?: string) {
  const parts = [start, end].map((item) => String(item || "").trim()).filter(Boolean);
  return parts.join(" - ");
}

export function cleanRichHtml(value?: string) {
  if (!value) return "";
  return value
    .replace(/<script[\s\S]*?<\/script>/gi, "")
    .replace(/\son\w+="[^"]*"/gi, "")
    .replace(/\son\w+='[^']*'/gi, "")
    .replace(/\sstyle="[^"]*"/gi, "")
    .replace(/\sstyle='[^']*'/gi, "");
}

export function HighlightText({ text, keywords }: { text: string; keywords: string[] }) {
  return <KeywordHighlightView text={text} keywords={keywords} />;
}

export function ContactLine({ data }: { data: NormalizedResumeData }) {
  const contact = data.contact || {};
  const parts = [
    contact.phone,
    contact.email,
    contact.location,
    contact.linkedin,
    contact.github,
    contact.website,
  ].filter(Boolean);
  if (!parts.length) return null;
  return (
    <div className="resume-meta">
      {parts.map((part, index) => (
        <span key={`${part}-${index}`}>
          {index > 0 && <span aria-hidden> / </span>}
          {part}
        </span>
      ))}
    </div>
  );
}

export function SectionBlock({
  section,
  accent = false,
  small = false,
  keywords,
}: {
  section: NormalizedResumeSection;
  accent?: boolean;
  small?: boolean;
  keywords: string[];
}) {
  if (!section.visible || section.items.length === 0) return null;
  const titleClass = accent
    ? small
      ? "resume-section-title-accent-sm"
      : "resume-section-title-accent"
    : small
      ? "resume-section-title-sm"
      : "resume-section-title";
  return (
    <section className="resume-section">
      <h3 className={titleClass}>{section.title}</h3>
      <div className="resume-items">
        {section.items.map((item) => (
          <ResumeItem key={item.id} item={item} compact={small} keywords={keywords} />
        ))}
      </div>
    </section>
  );
}

export function ResumeItem({
  item,
  compact = false,
  keywords,
}: {
  item: NormalizedResumeItem;
  compact?: boolean;
  keywords: string[];
}) {
  const title = item.title || item.organization || item.subtitle;
  const subtitle = [item.organization && item.organization !== title ? item.organization : "", item.subtitle, item.location]
    .filter(Boolean)
    .join(" / ");
  return (
    <article className="resume-item">
      <div className="resume-row-tight">
        <div>
          {title && (
            <h4 className="resume-item-title">
              <HighlightText text={title} keywords={keywords} />
            </h4>
          )}
          {subtitle && (
            <div className="resume-item-subtitle">
              <HighlightText text={subtitle} keywords={keywords} />
            </div>
          )}
        </div>
        {item.date && <span className="resume-date">{item.date}</span>}
      </div>
      {item.url && <div className="resume-meta">{item.url}</div>}
      {item.bullets.length > 0 && (
        <ul className="resume-list">
          {item.bullets.map((bullet, index) => (
            <li key={`${bullet}-${index}`}>
              <span aria-hidden>•</span>
              <span>
                <HighlightText text={bullet} keywords={keywords} />
              </span>
            </li>
          ))}
        </ul>
      )}
      {item.tags && item.tags.length > 0 && (
        <div className={compact ? "mt-1" : "mt-2"}>
          {item.tags.map((tag) => (
            <span key={tag} className="resume-skill-pill">
              <HighlightText text={tag} keywords={keywords} />
            </span>
          ))}
        </div>
      )}
    </article>
  );
}

export function sortSections(data: NormalizedResumeData) {
  return [...data.sections].filter((section) => section.visible).sort((a, b) => a.sortOrder - b.sortOrder);
}

export function splitTwoColumnSections(data: NormalizedResumeData) {
  const sections = sortSections(data);
  const sidebarKeys = new Set(["skill", "certificate", "education"]);
  return {
    main: sections.filter((section) => !sidebarKeys.has(section.key)),
    sidebar: sections.filter((section) => sidebarKeys.has(section.key)),
  };
}
