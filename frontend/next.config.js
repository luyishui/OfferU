/** @type {import('next').NextConfig} */
const { PHASE_DEVELOPMENT_SERVER } = require("next/constants");

// 浏览器把 /api/* 发到 Next.js 自身（同源），由 dev server 代理到后端。
// 这样 SameSite=Lax 的 offeru_browser_principal cookie 会随请求携带；
// 若浏览器直连后端（不同 host），Lax cookie 会被浏览器丢弃导致 401。
// Docker 编排内前端经服务名访问后端：INTERNAL_API_URL=http://backend:8000。
// 本地裸跑 npm run dev 时默认打到 http://127.0.0.1:9000（compose 映射的宿主端口）。
const INTERNAL_API_URL = process.env.INTERNAL_API_URL || "http://127.0.0.1:9000";

const nextConfig = {
  output: "standalone",
  // 关掉 Next 对尾斜杠的规范化重定向：/api/resume/ 本来要 308 到 /api/resume，
  // 但 backend 又把 /api/resume 307 到 /api/resume/ -> 经 rewrite 代理形成无限循环。
  // 后端 FastAPI 自带 redirect_slashes，由它统一处理；Next 不再插手。
  skipTrailingSlashRedirect: true,
};

module.exports = (phase) => ({
  ...nextConfig,
  distDir: phase === PHASE_DEVELOPMENT_SERVER ? ".next-dev" : ".next",
  async rewrites() {
    return [
      // 精确映射带尾斜杠的集合路径：/api/x/ -> BASE/api/x/
      // (:path* 对 "resume/" 会吃掉尾斜杠，导致 backend redirect_slashes 循环)
      {
        source: "/api/:path*/",
        destination: `${INTERNAL_API_URL}/api/:path*/`,
      },
      {
        source: "/api/:path*",
        destination: `${INTERNAL_API_URL}/api/:path*`,
      },
    ];
  },
});
