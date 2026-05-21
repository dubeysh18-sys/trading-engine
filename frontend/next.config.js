/** @type {import('next').NextConfig} */
const nextConfig = {
  output: "export",          // Static export for Netlify
  trailingSlash: true,       // Netlify needs this for clean URLs
  env: {
    NEXT_PUBLIC_API_URL: process.env.NEXT_PUBLIC_API_URL || "https://trading-engine-58hz.onrender.com",
  },
}

module.exports = nextConfig
