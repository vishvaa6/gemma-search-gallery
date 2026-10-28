/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        yt: {
          bg: "#0f0f0f",
          surface: "#181818",
          card: "#212121",
          hover: "#272727",
          border: "#303030",
          red: "#ff0000",
          redHover: "#cc0000",
          blue: "#3ea6ff",
          green: "#2ba640",
          text: "#f1f1f1",
          subtext: "#aaaaaa",
        },
      },
      fontFamily: {
        sans: ["Roboto", "Segoe UI", "system-ui", "-apple-system", "sans-serif"],
      },
    },
  },
  plugins: [],
};
