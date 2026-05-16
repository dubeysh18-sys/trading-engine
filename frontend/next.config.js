/** @type {import('next').NextConfig} */
const nextConfig = {
  output: "export",          // Static export for Netlify
  trailingSlash: true,       // Netlify needs this for clean URLs
  env: {
    NEXT_PUBLIC_API_URL: process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000",
  },
}

module.exports = nextConfig
