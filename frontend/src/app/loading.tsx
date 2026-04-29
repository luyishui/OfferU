export default function Loading() {
  return (
    <div className="grid min-h-[60vh] place-items-center px-6">
      <div className="bauhaus-panel flex items-center gap-5 bg-white px-6 py-5">
        <div className="relative h-14 w-16" aria-hidden="true">
          <span className="absolute left-0 top-7 h-5 w-5 bg-[#C95548] motion-safe:animate-bounce" />
          <span
            className="absolute left-6 top-4 h-6 w-6 bg-[#E4C46A] motion-safe:animate-bounce"
            style={{ animationDelay: "120ms" }}
          />
          <span
            className="absolute left-11 top-8 h-5 w-5 bg-[#6F8396] motion-safe:animate-bounce"
            style={{ animationDelay: "240ms" }}
          />
        </div>
        <div>
          <p className="bauhaus-label text-black/55">OfferU</p>
          <p className="text-sm font-semibold text-black">加载中...</p>
        </div>
      </div>
    </div>
  );
}
