/** @type {import('tailwindcss').Config} */
module.exports = {
  content: ["./app/**/*.{js,ts,jsx,tsx,mdx}", "./components/**/*.{js,ts,jsx,tsx,mdx}"],
  theme: {
    extend: {
      colors: {
        ink: "#101828",
        canvas: "#F5F7FA",
        brand: { 50: "#EEF4FF", 500: "#3B6FF5", 600: "#2E5EDD", 700: "#244BB8" },
      },
      boxShadow: { panel: "0 1px 2px rgba(16,24,40,.04), 0 8px 24px rgba(16,24,40,.06)" },
    },
  },
  plugins: [],
};
