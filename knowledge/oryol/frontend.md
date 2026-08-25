# Oryol Frontend Architecture & UI/UX Standards

Frontend applications in Oryol Workspace are fast, responsive, privacy-respecting, and visually unified.

---

## 1. Frontend Stack

- **Framework**: React 19 with TypeScript
- **Bundler & Tooling**: Vite with Hot Module Replacement (HMR)
- **Routing**: React Router v7 with clean client-side routes (`/`, `/mail/*`, `/admin/*`, `/settings`, `/login`)
- **Styling**: Tailwind CSS v4 with unified design tokens
- **Icons**: Lucide React
- **Typography**: Geist font family (`font-sans`, `font-mono`)

---

## 2. Design System Principles

- **Visual Consistency**: High contrast, subtle borders (`border-slate-200` / `border-slate-800`), clean slate dark-sidebar backgrounds (`bg-slate-900`), and indigo brand accents (`bg-indigo-600`).
- **Responsive Layout**: Every view must adapt cleanly from mobile viewports (375px) to desktop ultra-wide displays.
- **Command Palette (`⌘K` / `Ctrl+K`)**: Rapid navigation across mailboxes, settings, and domain tooling.
- **Keyboard Shortcuts**: Common power-user keyboard actions (e.g. compose, search, archive).
