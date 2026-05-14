import { Component, type ReactNode } from "react"

interface Props {
  children: ReactNode
}

interface State {
  hasError: boolean
}

export default class ErrorBoundary extends Component<Props, State> {
  constructor(props: Props) {
    super(props)
    this.state = { hasError: false }
  }

  static getDerivedStateFromError(): State {
    return { hasError: true }
  }

  componentDidCatch(error: Error, info: React.ErrorInfo) {
    console.error("ErrorBoundary caught:", error, info)
  }

  render() {
    if (this.state.hasError) {
      return (
        <div className="min-h-screen bg-paper flex items-center justify-center">
          <div className="text-center space-y-6">
            <p className="text-lg text-ink font-serif">页面出错了</p>
            <button
              onClick={() => this.setState({ hasError: false })}
              className="px-6 py-2 rounded-lg bg-primary text-white text-sm hover:bg-primary/90 transition-all shadow-sm"
            >
              重试
            </button>
          </div>
        </div>
      )
    }

    return this.props.children
  }
}
