/** @type {import('tailwindcss').Config} */
module.exports = {
  content: [
    "./app/**/*.{js,ts,jsx,tsx,mdx}",
    "./components/**/*.{js,ts,jsx,tsx,mdx}",
  ],
  theme: {
    extend: {
      colors: {
        // Bloomberg-dark palette
        bg:       { DEFAULT: "#0a0e17", surface: "#111827", card: "#1a2233", border: "#1e2d45" },
        accent:   { green: "#00d084", red: "#ff4d4d", yellow: "#fbbf24", blue: "#3b82f6" },
        text:     { primary: "#e2e8f0", secondary: "#94a3b8", muted: "#4b5563" },
      },
      fontFamily: {
        sans: ["Inter", "system-ui", "sans-serif"],
        mono: ["JetBrains Mono", "Fira Code", "monospace"],
      },
      animation: {
        "pulse-green": "pulse-green 2s ease-in-out infinite",
        "pulse-red":   "pulse-red 2s ease-in-out infinite",
        "fade-in":     "fadeIn 0.4s ease-out",
        "slide-in":    "slideIn 0.3s ease-out",
      },
      keyframes: {
        "pulse-green": {
          "0%, 100%": { boxShadow: "0 0 0 0 rgba(0, 208, 132, 0.7)" },
          "50%":       { boxShadow: "0 0 0 8px rgba(0, 208, 132, 0)" },
        },
        "pulse-red": {
          "0%, 100%": { boxShadow: "0 0 0 0 rgba(255, 77, 77, 0.7)" },
          "50%":       { boxShadow: "0 0 0 8px rgba(255, 77, 77, 0)" },
        },
        fadeIn:  { from: { opacity: 0 },              to: { opacity: 1 } },
        slideIn: { from: { transform: "translateY(-8px)", opacity: 0 }, to: { transform: "translateY(0)", opacity: 1 } },
      },
    },
  },
  plugins: [],
}
