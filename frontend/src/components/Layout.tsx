import { useEffect } from "react"
import { Outlet, Link, useLocation, useNavigate } from "react-router-dom"
import Logo from "./Logo"

export default function Layout() {
  const location = useLocation()
  const navigate = useNavigate()

  useEffect(() => {
    window.scrollTo({ top: 0, left: 0, behavior: "auto" })
  }, [location.pathname])

  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key !== "Escape" || location.pathname === "/") return
      const tag = (e.target as HTMLElement)?.tagName
      if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return
      navigate(-1)
    }
    window.addEventListener("keydown", handleKeyDown)
    return () => window.removeEventListener("keydown", handleKeyDown)
  }, [location.pathname, navigate])

  return (
    <div className="min-h-screen bg-paper flex flex-col">
      <header className="px-8 py-2 flex items-center justify-between sticky top-0 bg-paper/80 backdrop-blur-md z-50">
        <div className="flex items-center gap-8">
          <Link to="/" className="text-primary hover:opacity-80 transition-opacity">
            <Logo className="w-24 h-10" />
          </Link>
          <nav className="flex gap-8 text-xs font-medium tracking-normal uppercase">
            <Link
              to="/"
              className={location.pathname === "/" ? "text-ink" : "text-muted hover:text-ink transition-colors"}
              aria-current={location.pathname === "/" ? "page" : undefined}
            >
              分析
            </Link>
            <Link
              to="/library"
              className={location.pathname.startsWith("/library") ? "text-ink" : "text-muted hover:text-ink transition-colors"}
              aria-current={location.pathname.startsWith("/library") ? "page" : undefined}
            >
              仓库
            </Link>
            <Link
              to="/storyboard"
              className={location.pathname === "/storyboard" ? "text-ink" : "text-muted hover:text-ink transition-colors"}
              aria-current={location.pathname === "/storyboard" ? "page" : undefined}
            >
              分镜
            </Link>
            <Link
              to="/settings"
              className={location.pathname === "/settings" ? "text-ink" : "text-muted hover:text-ink transition-colors"}
              aria-current={location.pathname === "/settings" ? "page" : undefined}
            >
              设置
            </Link>
          </nav>
        </div>
      </header>
      <main className="px-8 py-8 max-w-6xl mx-auto w-full flex-1">
        <Outlet />
      </main>
      <footer className="px-8 py-16 text-center">
        <Logo className="w-16 h-8 mx-auto opacity-30 grayscale" />
        <p className="text-xs text-muted uppercase tracking-normal mt-4">Framebench / 拉片工作台</p>
      </footer>
    </div>
  )
}
