"use client";

import { useParams } from "next/navigation";
import ResumePreview from "../../components/ResumePreview";
import { DEFAULT_STYLE_CONFIG } from "../../components/StyleToolbar";
import { useResume } from "@/lib/hooks";
import { resolveApiAssetUrl } from "@/lib/api";


export default function ResumePrintPage() {
  const params = useParams();
  const resumeId = Number(params.id);
  const { data: resume, error } = useResume(resumeId);

  if (error) {
    return <div className="resume-print p-6 text-sm text-red-700">Resume failed to load.</div>;
  }

  if (!resume) {
    return <div className="resume-print p-6 text-sm text-black">Loading resume...</div>;
  }

  return (
    <div className="resume-print-page-shell">
      <div className="resume-print bg-white">
        <ResumePreview
          userName={resume.user_name || ""}
          title={resume.title || ""}
          photoUrl={resolveApiAssetUrl(resume.photo_url)}
          summary={resume.summary || ""}
          contactJson={resume.contact_json || {}}
          sections={resume.sections || []}
          styleConfig={resume.style_config || DEFAULT_STYLE_CONFIG}
        />
      </div>
    </div>
  );
}
