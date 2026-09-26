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
};

module.exports = (phase) => ({
  ...nextConfig,
  distDir: phase === PHASE_DEVELOPMENT_SERVER ? ".next-dev" : ".next",
  async rewrites() {
    return [
      {
        source: "/api/:path*",
        destination: `${INTERNAL_API_URL}/api/:path*`,
      },
    ];
  },
});
